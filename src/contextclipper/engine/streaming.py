"""Line-by-line streaming filter engine for live output processing.

Used by ``ctxclp run --stream`` to apply compression rules as the subprocess
produces output, without buffering the entire response first.

Memory is bounded by prefix_collapse max_lines (default 10) because we never
accumulate the full output.  Rules that require full buffering (``tail``,
``json_select``) are silently skipped with a notice line emitted once.

Usage::

    from contextclipper.engine.streaming import StreamingFilter, run_streaming
    stats = run_streaming("npm test", flt, exit_code_ref=[0], timeout=120)
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .filters import CommandFilter

from .filters import (
    ANSI_RE,
    MAX_LINE_BYTES,
    FilterRule,
    _find_override,
)


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _truncate_line(line: str) -> str:
    if len(line) > MAX_LINE_BYTES:
        return line[:MAX_LINE_BYTES] + "…[line truncated]"
    return line


@dataclass
class StreamStats:
    """Metrics collected during a streaming run."""

    original_lines: int = 0
    kept_lines: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    elapsed_ms: float = 0.0
    truncated: bool = False
    filter_name: str | None = None
    batch_only_rules: list[str] = field(default_factory=list)
    timed_out: bool = False

    @property
    def reduction_pct(self) -> float:
        if self.original_lines == 0:
            return 0.0
        return round((1 - self.kept_lines / self.original_lines) * 100, 1)

    def footer(self, raw_output_id: str | None = None) -> str:
        parts = [f"{self.kept_lines}/{self.original_lines} lines, -{self.reduction_pct}% tokens"]
        if raw_output_id:
            parts.append(f"raw_id={raw_output_id}")
            parts.append(f"fetch: ctxclp fetch {raw_output_id}")
        if self.truncated:
            parts.append("truncated")
        if self.timed_out:
            parts.append("timed-out")
        if self.filter_name:
            parts.append(f"filter={self.filter_name}")
        if self.batch_only_rules:
            parts.append(f"skipped-rules={','.join(self.batch_only_rules)}")
        return "[ctxclp: " + " | ".join(parts) + "]"


class StreamingFilter:
    """Stateful per-line filter.

    Call :meth:`feed` for each output line; collect the returned lines to emit.
    Call :meth:`flush` at end of stream to drain any buffered prefix-collapse state.
    """

    def __init__(self, rules: list[FilterRule]) -> None:
        self._keep_rules = sorted(
            [r for r in rules if r.type == "keep_matching"],
            key=lambda r: -r.priority,
        )
        self._drop_rules = sorted(
            [r for r in rules if r.type == "drop_matching"],
            key=lambda r: -r.priority,
        )
        self._replace_rules = [r for r in rules if r.type == "regex_replace"]
        self._section_rules = [r for r in rules if r.type == "keep_section"]
        self._prefix_rules = [r for r in rules if r.type == "prefix_collapse"]

        self._head_limit: int | None = None
        head_rules = [r for r in rules if r.type == "head"]
        if head_rules:
            self._head_limit = min(r.lines for r in head_rules)

        self._batch_only: list[str] = []
        if any(r.type == "tail" for r in rules):
            self._batch_only.append("tail")
        if any(r.type == "json_select" for r in rules):
            self._batch_only.append("json_select")

        # Mutable runtime state
        self._head_count: int = 0
        self._in_section: dict[int, bool] = {}
        self._prefix_rule: FilterRule | None = None
        self._prefix_pending: list[str] = []
        self._prev_line: str | None = None
        self._repeat_count: int = 0
        self._batch_notice_emitted: bool = False

    @property
    def batch_only_rules(self) -> list[str]:
        return list(self._batch_only)

    def feed(self, line: str) -> list[str]:
        """Process one line and return a (possibly empty) list of lines to emit."""
        output: list[str] = []

        # Emit a one-time notice about skipped batch-only rules
        if self._batch_only and not self._batch_notice_emitted:
            self._batch_notice_emitted = True
            output.append(
                f"  [ctxclp: streaming mode — {', '.join(self._batch_only)} rules require "
                "batch processing and are skipped]"
            )

        # Head limit
        if self._head_limit is not None:
            if self._head_count >= self._head_limit:
                return []
            self._head_count += 1

        # Prefix-collapse buffering
        if self._prefix_rules:
            matched_rule: FilterRule | None = None
            for r in self._prefix_rules:
                if r.prefix and line.startswith(r.prefix):
                    matched_rule = r
                    break

            if matched_rule is not None:
                if self._prefix_rule is matched_rule:
                    self._prefix_pending.append(line)
                    return []  # still accumulating; nothing to emit yet
                else:
                    output.extend(self._flush_prefix())
                    self._prefix_rule = matched_rule
                    self._prefix_pending = [line]
                    return self._dedup(output)
            else:
                output.extend(self._flush_prefix())

        # Regex replacement
        for r in self._replace_rules:
            if r._compiled and r.replacement is not None:
                line = r._compiled.sub(r.replacement, line)

        # keep_section state machine: lines outside all sections are dropped
        if self._section_rules:
            for i, r in enumerate(self._section_rules):
                if not (r._compiled_start and r._compiled_end):
                    continue
                if not self._in_section.get(i, False):
                    if r._compiled_start.search(line):
                        self._in_section[i] = True
                        output.append(line)
                        return self._dedup(output)
                else:
                    output.append(line)
                    if r._compiled_end.search(line):
                        self._in_section[i] = False
                    return self._dedup(output)
            # No section is active and line did not open any section — drop it
            return self._dedup(output)

        # keep_matching / drop_matching
        if self._keep_rules or self._drop_rules:
            keep_priority = -1
            for r in self._keep_rules:
                if r._compiled and r._compiled.search(line):
                    keep_priority = r.priority
                    break
            drop_priority = -1
            for r in self._drop_rules:
                if r._compiled and r._compiled.search(line):
                    drop_priority = r.priority
                    break
            if keep_priority >= 0 and keep_priority >= drop_priority:
                output.append(line)
            elif drop_priority >= 0:
                return self._dedup(output)  # line is dropped
            else:
                output.append(line)
        else:
            output.append(line)

        return self._dedup(output)

    def flush(self) -> list[str]:
        """Drain remaining buffered state at end of stream."""
        output = list(self._flush_prefix())
        if self._repeat_count > 0:
            output.append(f"  [above line repeated {self._repeat_count}×]")
            self._repeat_count = 0
        return output

    def _flush_prefix(self) -> list[str]:
        if not self._prefix_rule or not self._prefix_pending:
            return []
        r = self._prefix_rule
        pending = self._prefix_pending
        self._prefix_rule = None
        self._prefix_pending = []
        result = list(pending[: r.max_lines])
        extra = len(pending) - r.max_lines
        if extra > 0:
            result.append(f"  [+{extra} more lines with prefix {r.prefix!r}]")
        return result

    def _dedup(self, lines: list[str]) -> list[str]:
        """Streaming consecutive-duplicate suppression."""
        result: list[str] = []
        for line in lines:
            if line == self._prev_line:
                self._repeat_count += 1
            else:
                if self._repeat_count > 0:
                    result.append(f"  [above line repeated {self._repeat_count}×]")
                    self._repeat_count = 0
                result.append(line)
                self._prev_line = line
        return result


def run_streaming(
    command: str,
    flt: "CommandFilter | None",
    exit_code_ref: list[int],
    *,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> StreamStats:
    """Execute ``command`` and stream its output through the matching filter.

    Output lines are written to stdout immediately as the subprocess produces
    them — no full-output buffering.  The exit code is written into
    ``exit_code_ref[0]``.

    Args:
        command: Shell command to run.
        flt: Matched CommandFilter, or None for generic fallback.
        exit_code_ref: Single-element list; populated with the subprocess exit code.
        max_tokens: When set, stop emitting after this many approximate tokens
            (1 token ≈ 4 chars) and drain the rest silently.
        timeout: Kill the subprocess after this many seconds.

    Returns:
        :class:`StreamStats` with line counts, byte counts, and elapsed ms.
    """
    stats = StreamStats(filter_name=flt.name if flt else None)
    t0 = time.monotonic()

    env = os.environ.copy()
    env["CTXCLP_INTERNAL"] = "1"
    env.pop("CTXCLP_HOOK_ACTIVE", None)

    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )

    # Optional hard timeout: kill process after `timeout` seconds
    _kill_timer: threading.Timer | None = None
    if timeout and timeout > 0:
        def _kill() -> None:
            try:
                proc.kill()
            except OSError:
                pass
        _kill_timer = threading.Timer(timeout, _kill)
        _kill_timer.start()

    rules = _find_override(flt, command) if flt else None
    if rules is None and flt:
        rules = flt.rules
    sf = StreamingFilter(rules or [])
    stats.batch_only_rules = sf.batch_only_rules

    token_budget_chars = max_tokens * 4 if max_tokens else None
    chars_out = 0

    try:
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            raw_line = raw_line.rstrip("\n")
            stats.bytes_in += len(raw_line.encode("utf-8", errors="replace")) + 1
            stats.original_lines += 1

            clean = _strip_ansi(raw_line)
            clean = _truncate_line(clean)

            if token_budget_chars is not None and chars_out >= token_budget_chars:
                continue  # drain silently

            for out_line in sf.feed(clean):
                if token_budget_chars is not None and chars_out + len(out_line) + 1 > token_budget_chars:
                    stats.truncated = True
                    sys.stdout.write(
                        f"[ctxclp: streaming output truncated to ≤{max_tokens} tokens]\n"
                    )
                    sys.stdout.flush()
                    token_budget_chars = 0  # drain everything after this
                    break
                sys.stdout.write(out_line + "\n")
                sys.stdout.flush()
                out_bytes = len(out_line.encode("utf-8", errors="replace")) + 1
                stats.bytes_out += out_bytes
                chars_out += len(out_line) + 1
                stats.kept_lines += 1

        # Flush remaining buffered state (prefix collapse, repeat counters)
        for out_line in sf.flush():
            if token_budget_chars is None or chars_out + len(out_line) + 1 <= token_budget_chars:
                sys.stdout.write(out_line + "\n")
                sys.stdout.flush()
                stats.kept_lines += 1

    finally:
        if _kill_timer:
            _kill_timer.cancel()

    proc.wait()
    exit_code_ref[0] = proc.returncode

    if proc.returncode == -9 and timeout:
        stats.timed_out = True

    stats.elapsed_ms = round((time.monotonic() - t0) * 1000, 2)
    return stats
