"""Built-in PluggableStrategy implementations for common output types.

Strategies are registered automatically when this module is imported (done in
:mod:`contextclipper.engine.filters`).  Each strategy is a callable with
signature ``(lines, command, exit_code) -> list[str]`` and can be selected by
name in a TOML filter via ``strategy = "<name>"``.

Available strategies
--------------------
- ``log``          — keep error lines + head/tail + level summary for log files
- ``diff``         — keep hunk headers + N context lines, drop unchanged context
- ``table``        — keep header + non-healthy rows; summarise all-healthy tables
- ``json-fields``  — reduce NDJSON logs to core fields (message, level, time, error)
"""

from __future__ import annotations

import json
import re
from typing import Any

from .filters import register_strategy

# ── log ───────────────────────────────────────────────────────────────────────

_LOG_LEVEL_RE = re.compile(
    r"\b(ERROR|CRITICAL|FATAL|FAIL|WARN|WARNING|INFO|DEBUG|TRACE)\b",
    re.IGNORECASE,
)
_ERROR_LEVEL_RE = re.compile(
    r"\b(ERROR|CRITICAL|FATAL|FAIL)\b",
    re.IGNORECASE,
)

_HEAD_N = 10
_TAIL_N = 10


def _strategy_log(lines: list[str], command: str, exit_code: int) -> list[str]:
    """Keep error/fatal lines plus first and last 10 lines and a level summary."""
    if not lines:
        return lines

    head = lines[:_HEAD_N]
    tail = lines[-_TAIL_N:] if len(lines) > _HEAD_N + _TAIL_N else []

    level_counts: dict[str, int] = {}
    error_lines: list[str] = []
    for ln in lines:
        m = _LOG_LEVEL_RE.search(ln)
        if m:
            lvl = m.group(1).upper()
            level_counts[lvl] = level_counts.get(lvl, 0) + 1
            if _ERROR_LEVEL_RE.search(ln):
                error_lines.append(ln)

    result: list[str] = list(head)
    middle_count = len(lines) - _HEAD_N - _TAIL_N
    if middle_count > 0:
        result.append(f"  [ctxclp: {middle_count} middle lines omitted]")
    result.extend(tail)

    if error_lines:
        result.append("")
        result.append("=== Error / Fatal lines ===")
        result.extend(error_lines[:50])
        if len(error_lines) > 50:
            result.append(f"  [ctxclp: {len(error_lines) - 50} more error lines omitted]")

    if level_counts:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(level_counts.items()))
        result.append(f"  [ctxclp log summary: {summary}]")

    return result


register_strategy("log", _strategy_log)


# ── diff ──────────────────────────────────────────────────────────────────────

_HUNK_HEADER_RE = re.compile(r"^@@")
_FILE_HEADER_RE = re.compile(r"^(---|\+\+\+|diff --git|index |Binary files )")

_DIFF_CONTEXT = 3  # lines of context around each change


def _strategy_diff(lines: list[str], command: str, exit_code: int) -> list[str]:
    """Keep hunk/file headers and changed lines with ``_DIFF_CONTEXT`` surrounding lines."""
    if not lines:
        return lines

    important: list[bool] = [False] * len(lines)
    for i, ln in enumerate(lines):
        if _FILE_HEADER_RE.match(ln) or _HUNK_HEADER_RE.match(ln):
            important[i] = True
        elif ln.startswith("+") or ln.startswith("-"):
            important[i] = True

    keep: list[bool] = list(important)
    for i, imp in enumerate(important):
        if imp:
            for j in range(
                max(0, i - _DIFF_CONTEXT),
                min(len(lines), i + _DIFF_CONTEXT + 1),
            ):
                keep[j] = True

    result: list[str] = []
    omitted = 0
    for i, ln in enumerate(lines):
        if keep[i]:
            if omitted > 0:
                result.append(f"  [ctxclp: {omitted} unchanged context lines omitted]")
                omitted = 0
            result.append(ln)
        else:
            omitted += 1
    if omitted > 0:
        result.append(f"  [ctxclp: {omitted} unchanged context lines omitted]")

    return result


register_strategy("diff", _strategy_diff)


# ── table ─────────────────────────────────────────────────────────────────────

_STATUS_HEALTHY = re.compile(
    r"\b(Up \d+|running|RUNNING|Exited \(0\)|healthy|Ready|Completed|Succeeded)\b"
)
_STATUS_PROBLEM = re.compile(
    r"\b(Error|error|Exited \([1-9]\d*\)|OOMKilled|CrashLoop|Pending|Failed|Dead|Terminating)\b"
)


def _strategy_table(lines: list[str], command: str, exit_code: int) -> list[str]:
    """Keep header row plus any rows that show a non-healthy / non-trivial status.

    Rows that appear perfectly healthy are dropped; if *all* rows are healthy a
    one-line summary replaces the body.
    """
    if not lines:
        return lines

    result: list[str] = []
    healthy_count = 0

    for i, ln in enumerate(lines):
        if i == 0:
            result.append(ln)
            continue
        if not ln.strip():
            continue
        if _STATUS_PROBLEM.search(ln):
            result.append(ln)
        elif _STATUS_HEALTHY.search(ln):
            healthy_count += 1
        else:
            result.append(ln)

    if healthy_count > 0:
        if len(result) <= 1:
            result.append(
                f"  [ctxclp: all {healthy_count} row(s) appear healthy/running; "
                "use --raw to see all]"
            )
        else:
            result.append(
                f"  [ctxclp: {healthy_count} additional healthy/running row(s) omitted]"
            )

    return result


register_strategy("table", _strategy_table)


# ── json-fields ───────────────────────────────────────────────────────────────

_KEEP_KEYS: frozenset[str] = frozenset(
    {
        "message", "msg", "level", "severity", "time", "timestamp", "ts",
        "error", "err", "status", "code",
    }
)


def _strategy_json_fields(lines: list[str], command: str, exit_code: int) -> list[str]:
    """Reduce newline-delimited JSON log lines to the most informative fields.

    Non-JSON lines are kept as-is.
    """
    result: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("{"):
            try:
                obj: dict[str, Any] = json.loads(stripped)
                kept = {k: v for k, v in obj.items() if k in _KEEP_KEYS}
                result.append(json.dumps(kept, separators=(",", ":")))
                continue
            except (json.JSONDecodeError, ValueError):
                pass
        result.append(ln)
    return result


register_strategy("json-fields", _strategy_json_fields)
