"""Local analytics: tracks token savings and command usage in SQLite.

Privacy: command text is redacted via :mod:`contextclipper.core.redact` before being
recorded. Disable persistence entirely with ``CTXCLP_DISABLE_STATS=1``.

Telemetry (regret detection)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When ``CTXCLP_TELEMETRY=1`` the ``raw_output_id`` is stored alongside each
event.  When the agent later calls ``ctxclp fetch <uuid>``, the corresponding
event's ``had_raw_pull`` flag is set to ``1``.  ``suggestions()`` uses this to
identify filter + command combos with high regret (fetch rate ≥ threshold).

All data stays on-device; no network transmission ever occurs.  Use
``CTXCLP_DISABLE_STATS=1`` to opt out completely.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

from .redact import redact_command


def _xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


STATS_DB = _xdg_data_home() / "contextclipper" / "stats.db"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY,
    ts             REAL    NOT NULL,
    command        TEXT    NOT NULL,
    original_lines INTEGER NOT NULL,
    kept_lines     INTEGER NOT NULL,
    bytes_in       INTEGER NOT NULL DEFAULT 0,
    bytes_out      INTEGER NOT NULL DEFAULT 0,
    elapsed_ms     REAL    NOT NULL DEFAULT 0.0,
    exit_code      INTEGER NOT NULL DEFAULT 0,
    had_raw_pull   INTEGER NOT NULL DEFAULT 0,
    filter_name    TEXT,
    strategy_name  TEXT,
    raw_output_id  TEXT
);

CREATE TABLE IF NOT EXISTS raw_pulls (
    id         INTEGER PRIMARY KEY,
    ts         REAL    NOT NULL,
    output_id  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_ts       ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_rawid    ON events(raw_output_id);
CREATE INDEX IF NOT EXISTS idx_raw_pulls_ts    ON raw_pulls(ts);
CREATE INDEX IF NOT EXISTS idx_raw_pulls_oid   ON raw_pulls(output_id);
"""

_MIGRATIONS = [
    "ALTER TABLE events ADD COLUMN bytes_in INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE events ADD COLUMN bytes_out INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE events ADD COLUMN elapsed_ms REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE events ADD COLUMN filter_name TEXT",
    "ALTER TABLE events ADD COLUMN strategy_name TEXT",
    "ALTER TABLE events ADD COLUMN raw_output_id TEXT",
    (
        "CREATE TABLE IF NOT EXISTS raw_pulls ("
        "id INTEGER PRIMARY KEY, ts REAL NOT NULL, output_id TEXT NOT NULL)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_raw_pulls_ts  ON raw_pulls(ts)",
    "CREATE INDEX IF NOT EXISTS idx_raw_pulls_oid ON raw_pulls(output_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_rawid  ON events(raw_output_id)",
]


def _is_disabled() -> bool:
    return os.environ.get("CTXCLP_DISABLE_STATS") == "1"


def _is_telemetry_enabled() -> bool:
    """Return True when enhanced regret-detection telemetry is active."""
    return os.environ.get("CTXCLP_TELEMETRY", "0") == "1"


def _cmd_base(cmd: str) -> str:
    """Extract the base program name from a shell command string."""
    stripped = cmd.strip()
    if not stripped:
        return cmd
    first_token = stripped.split()[0]
    return first_token.split("/")[-1]


class StatsDB:
    def __init__(self, db_path: Path = STATS_DB) -> None:
        self.disabled = _is_disabled()
        self._telemetry_enabled = _is_telemetry_enabled()
        self._lock = threading.RLock()
        if self.disabled:
            self._conn: sqlite3.Connection | None = None
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column / table already exists
        self._conn.commit()

    def record(
        self,
        command: str,
        original_lines: int,
        kept_lines: int,
        exit_code: int = 0,
        had_raw_pull: bool = False,
        bytes_in: int = 0,
        bytes_out: int = 0,
        elapsed_ms: float = 0.0,
        filter_name: str | None = None,
        strategy_name: str | None = None,
        raw_output_id: str | None = None,
    ) -> None:
        if self.disabled or self._conn is None:
            return
        cmd = redact_command(command)[:200]
        stored_raw_id = raw_output_id if self._telemetry_enabled else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(ts, command, original_lines, kept_lines, bytes_in, bytes_out, "
                "elapsed_ms, exit_code, had_raw_pull, filter_name, strategy_name, raw_output_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), cmd, original_lines, kept_lines,
                    bytes_in, bytes_out, elapsed_ms, exit_code, int(had_raw_pull),
                    filter_name, strategy_name, stored_raw_id,
                ),
            )
            self._conn.commit()

    def record_raw_pull(self, output_id: str) -> None:
        """Record that a raw output was fetched.

        When telemetry is enabled the corresponding event row (matched by
        ``raw_output_id``) has its ``had_raw_pull`` flag set to 1 so that
        :meth:`suggestions` can compute per-filter regret rates.
        """
        if self.disabled or self._conn is None:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO raw_pulls(ts, output_id) VALUES(?,?)",
                (time.time(), output_id),
            )
            if self._telemetry_enabled:
                self._conn.execute(
                    "UPDATE events SET had_raw_pull = 1 WHERE raw_output_id = ?",
                    (output_id,),
                )
            self._conn.commit()

    def summary(self, days: int = 7) -> dict:
        if self.disabled or self._conn is None:
            return {
                "period_days": days,
                "total_commands": 0,
                "total_original_lines": 0,
                "total_kept_lines": 0,
                "reduction_pct": 0.0,
                "bytes_saved": 0,
                "avg_elapsed_ms": 0.0,
                "raw_pull_count": 0,
                "top_commands": [],
            }
        since = time.time() - days * 86400
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*), SUM(original_lines), SUM(kept_lines), "
                "SUM(bytes_in), SUM(bytes_out), AVG(elapsed_ms) "
                "FROM events WHERE ts > ?",
                (since,),
            )
            row = cur.fetchone()
            total_cmds, total_orig, total_kept, total_bin, total_bout, avg_ms = row
            total_orig = total_orig or 0
            total_kept = total_kept or 0
            total_bin = total_bin or 0
            total_bout = total_bout or 0
            avg_ms = avg_ms or 0.0
            reduction = round((1 - total_kept / total_orig) * 100, 1) if total_orig else 0.0

            cur2 = self._conn.execute(
                "SELECT command, COUNT(*) as n FROM events WHERE ts > ? "
                "GROUP BY command ORDER BY n DESC LIMIT 10",
                (since,),
            )
            top_cmds = [{"command": r[0], "count": r[1]} for r in cur2.fetchall()]

            cur3 = self._conn.execute(
                "SELECT COUNT(*) FROM raw_pulls WHERE ts > ?", (since,)
            )
            raw_pull_count = cur3.fetchone()[0] or 0

        return {
            "period_days": days,
            "total_commands": total_cmds or 0,
            "total_original_lines": total_orig,
            "total_kept_lines": total_kept,
            "reduction_pct": reduction,
            "bytes_in": total_bin,
            "bytes_out": total_bout,
            "bytes_saved": max(0, total_bin - total_bout),
            "avg_elapsed_ms": round(avg_ms, 2),
            "raw_pull_count": raw_pull_count,
            "top_commands": top_cmds,
        }

    def audit(self, days: int = 7, limit: int = 100, command_filter: str | None = None) -> list[dict]:
        """Return detailed per-event records for auditing what was clipped."""
        if self.disabled or self._conn is None:
            return []
        since = time.time() - days * 86400
        with self._lock:
            if command_filter:
                cur = self._conn.execute(
                    "SELECT ts, command, original_lines, kept_lines, bytes_in, bytes_out, "
                    "elapsed_ms, exit_code, had_raw_pull, filter_name, strategy_name "
                    "FROM events WHERE ts > ? AND command LIKE ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (since, f"%{command_filter}%", limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT ts, command, original_lines, kept_lines, bytes_in, bytes_out, "
                    "elapsed_ms, exit_code, had_raw_pull, filter_name, strategy_name "
                    "FROM events WHERE ts > ? ORDER BY ts DESC LIMIT ?",
                    (since, limit),
                )
            rows = cur.fetchall()

        results = []
        for row in rows:
            ts, cmd, orig, kept, bin_, bout, ms, ec, hrp, fn, sn = row
            orig = orig or 0
            kept = kept or 0
            reduction = round((1 - kept / orig) * 100, 1) if orig else 0.0
            results.append({
                "timestamp": ts,
                "command": cmd,
                "original_lines": orig,
                "kept_lines": kept,
                "reduction_pct": reduction,
                "bytes_in": bin_ or 0,
                "bytes_out": bout or 0,
                "elapsed_ms": ms or 0.0,
                "exit_code": ec or 0,
                "had_raw_pull": bool(hrp),
                "filter_name": fn,
                "strategy_name": sn,
            })
        return results

    def suggestions(
        self,
        days: int = 30,
        threshold: float = 0.3,
        min_runs: int = 3,
    ) -> list[dict]:
        """Return filter+command combos where agents fetch raw output too often.

        Requires ``CTXCLP_TELEMETRY=1``; returns an empty list otherwise.

        Args:
            days: Look-back window in days.
            threshold: Fetch-rate above which a suggestion is emitted (0–1).
            min_runs: Minimum event count required before suggesting.

        Returns:
            List of dicts with ``command_base``, ``filter_name``, ``runs``,
            ``fetches``, ``fetch_rate_pct``, and ``recommendation``.
        """
        if self.disabled or self._conn is None or not self._telemetry_enabled:
            return []
        since = time.time() - days * 86400
        with self._lock:
            cur = self._conn.execute(
                "SELECT command, filter_name, COUNT(*) as runs, SUM(had_raw_pull) as fetches "
                "FROM events WHERE ts > ? AND raw_output_id IS NOT NULL "
                "GROUP BY command, filter_name",
                (since,),
            )
            rows = cur.fetchall()

        from collections import defaultdict

        groups: dict[tuple[str, str | None], list[int]] = defaultdict(lambda: [0, 0])
        for cmd, filter_name, runs, fetches in rows:
            base = _cmd_base(cmd)
            key = (base, filter_name)
            groups[key][0] += runs
            groups[key][1] += fetches or 0

        result = []
        for (base, filter_name), (runs, fetches) in groups.items():
            if runs < min_runs:
                continue
            rate = fetches / runs
            if rate >= threshold:
                filter_label = filter_name or "fallback"
                result.append({
                    "command_base": base,
                    "filter_name": filter_label,
                    "runs": runs,
                    "fetches": fetches,
                    "fetch_rate_pct": round(rate * 100, 1),
                    "recommendation": (
                        f"Relax or review filter '{filter_label}' for '{base}' — "
                        f"agents retrieve the full output {round(rate * 100, 1)}% of the time"
                    ),
                })
        result.sort(key=lambda r: -r["fetch_rate_pct"])
        return result

    def all_command_stats(self, days: int = 30) -> list[dict]:
        """Return per-(command_base, filter) stats for the dashboard.

        Returns:
            List of dicts with ``command_base``, ``filter_name``, ``runs``,
            ``avg_reduction_pct``, ``fetch_rate_pct``.
        """
        if self.disabled or self._conn is None:
            return []
        since = time.time() - days * 86400
        with self._lock:
            cur = self._conn.execute(
                "SELECT command, filter_name, COUNT(*) as runs, "
                "AVG(CASE WHEN original_lines > 0 "
                "    THEN CAST(kept_lines AS REAL) / original_lines ELSE 1 END) as keep_ratio, "
                "SUM(had_raw_pull) as fetches "
                "FROM events WHERE ts > ? "
                "GROUP BY command, filter_name ORDER BY runs DESC LIMIT 200",
                (since,),
            )
            rows = cur.fetchall()

        from collections import defaultdict

        groups: dict[tuple[str, str | None], list] = defaultdict(lambda: [0, 0.0, 0])
        for cmd, fn, runs, keep_ratio, fetches in rows:
            base = _cmd_base(cmd)
            key = (base, fn)
            prev_runs, prev_ratio_sum, prev_fetches = groups[key]
            groups[key] = [
                prev_runs + runs,
                prev_ratio_sum + (keep_ratio or 1.0) * runs,
                prev_fetches + (fetches or 0),
            ]

        result = []
        for (base, fn), (runs, ratio_sum, fetches) in groups.items():
            avg_keep = ratio_sum / runs if runs else 1.0
            avg_reduction = round((1 - avg_keep) * 100, 1)
            fetch_rate = round(fetches / runs * 100, 1) if runs else 0.0
            result.append({
                "command_base": base,
                "filter_name": fn or "fallback",
                "runs": runs,
                "avg_reduction_pct": avg_reduction,
                "fetch_rate_pct": fetch_rate,
                "high_regret": fetch_rate >= 30.0,
            })
        result.sort(key=lambda r: -r["runs"])
        return result

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
