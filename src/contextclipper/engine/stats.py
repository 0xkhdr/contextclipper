"""Local analytics: tracks token savings and command usage in SQLite.

Privacy: command text is redacted via :mod:`contextclipper.engine.redact` before being
recorded. Disable persistence entirely with ``CTXCLP_DISABLE_STATS=1``.
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
    id           INTEGER PRIMARY KEY,
    ts           REAL    NOT NULL,
    command      TEXT    NOT NULL,
    original_lines INTEGER NOT NULL,
    kept_lines   INTEGER NOT NULL,
    bytes_in     INTEGER NOT NULL DEFAULT 0,
    bytes_out    INTEGER NOT NULL DEFAULT 0,
    elapsed_ms   REAL    NOT NULL DEFAULT 0.0,
    exit_code    INTEGER NOT NULL DEFAULT 0,
    had_raw_pull INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

_MIGRATIONS = [
    "ALTER TABLE events ADD COLUMN bytes_in INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE events ADD COLUMN bytes_out INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE events ADD COLUMN elapsed_ms REAL NOT NULL DEFAULT 0.0",
]


def _is_disabled() -> bool:
    return os.environ.get("CTXCLP_DISABLE_STATS") == "1"


class StatsDB:
    def __init__(self, db_path: Path = STATS_DB) -> None:
        self.disabled = _is_disabled()
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
                pass  # column already exists
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
    ) -> None:
        if self.disabled or self._conn is None:
            return
        cmd = redact_command(command)[:200]
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(ts, command, original_lines, kept_lines, bytes_in, bytes_out, "
                "elapsed_ms, exit_code, had_raw_pull) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), cmd, original_lines, kept_lines,
                    bytes_in, bytes_out, elapsed_ms, exit_code, int(had_raw_pull),
                ),
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
                "SELECT command, COUNT(*) as n FROM events WHERE ts > ? GROUP BY command ORDER BY n DESC LIMIT 10",
                (since,),
            )
            top_cmds = [{"command": r[0], "count": r[1]} for r in cur2.fetchall()]
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
            "top_commands": top_cmds,
        }

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
