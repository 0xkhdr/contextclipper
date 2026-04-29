"""Tests for built-in PluggableStrategy implementations (Phase 1.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import strategies to ensure they register
import contextclipper.engine.strategies  # noqa: F401  # type: ignore[import-not-found]

from contextclipper.engine.filters import (  # type: ignore[import-not-found]
    compress_output,
    get_registry,
    register_strategy,
    unregister_strategy,
    _get_strategy,
)


# ── log strategy ─────────────────────────────────────────────────────────────

class TestLogStrategy:
    def _compress(self, output: str, exit_code: int = 0) -> str:
        from contextclipper.engine.strategies import _strategy_log
        return "\n".join(_strategy_log(output.splitlines(), "app.log", exit_code))

    def test_error_lines_preserved(self) -> None:
        log = "\n".join([
            "INFO  starting server",
            "DEBUG connection pool size: 10",
            "INFO  request received",
            "ERROR database connection failed: timeout",
            "INFO  retrying...",
        ])
        result = self._compress(log)
        assert "ERROR database connection failed" in result

    def test_head_lines_preserved(self) -> None:
        lines = [f"INFO  line {i}" for i in range(50)]
        result = self._compress("\n".join(lines))
        assert "INFO  line 0" in result
        assert "INFO  line 9" in result

    def test_tail_lines_preserved(self) -> None:
        lines = [f"INFO  line {i}" for i in range(50)]
        result = self._compress("\n".join(lines))
        assert "INFO  line 49" in result

    def test_level_summary_appended(self) -> None:
        log = "ERROR: fail\nWARNING: watch out\nINFO: ok\nDEBUG: verbose"
        result = self._compress(log)
        assert "ctxclp log summary" in result

    def test_empty_input(self) -> None:
        from contextclipper.engine.strategies import _strategy_log
        assert _strategy_log([], "cmd", 0) == []

    def test_at_least_30pct_reduction(self) -> None:
        lines = [f"DEBUG this is verbose line {i}" for i in range(100)]
        original = "\n".join(lines)
        from contextclipper.engine.strategies import _strategy_log
        result = _strategy_log(lines, "app", 0)
        assert len(result) < len(lines) * 0.7

    def test_registered(self) -> None:
        assert _get_strategy("log") is not None


# ── diff strategy ─────────────────────────────────────────────────────────────

class TestDiffStrategy:
    def _compress(self, output: str) -> str:
        from contextclipper.engine.strategies import _strategy_diff
        return "\n".join(_strategy_diff(output.splitlines(), "git diff", 0))

    def test_hunk_header_preserved(self) -> None:
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "index abc..def 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,5 +1,5 @@\n"
            " unchanged line 1\n"
            " unchanged line 2\n"
            "-old line\n"
            "+new line\n"
            " unchanged line 3\n"
        )
        result = self._compress(diff)
        assert "@@ -1,5 +1,5 @@" in result
        assert "-old line" in result
        assert "+new line" in result

    def test_file_headers_preserved(self) -> None:
        diff = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -10,3 +10,3 @@\n"
            "+changed\n"
        )
        result = self._compress(diff)
        assert "diff --git" in result
        assert "--- a/x.py" in result

    def test_unchanged_context_omitted(self) -> None:
        unchanged_lines = [f" context line {i}" for i in range(20)]
        diff_body = "\n".join(unchanged_lines)
        diff = f"@@ -1,20 +1,21 @@\n{diff_body}\n+added line\n"
        result = self._compress(diff)
        assert "omitted" in result

    def test_at_least_30pct_reduction_large_diff(self) -> None:
        lines = ["@@ -1,100 +1,101 @@\n"]
        lines += [f" unchanged context {i}" for i in range(100)]
        lines += ["+added line"]
        from contextclipper.engine.strategies import _strategy_diff
        result = _strategy_diff(lines, "git diff", 0)
        assert len(result) < len(lines) * 0.7

    def test_registered(self) -> None:
        assert _get_strategy("diff") is not None


# ── table strategy ────────────────────────────────────────────────────────────

class TestTableStrategy:
    def _compress(self, output: str) -> str:
        from contextclipper.engine.strategies import _strategy_table
        return "\n".join(_strategy_table(output.splitlines(), "docker ps", 0))

    def test_header_always_kept(self) -> None:
        table = (
            "CONTAINER ID   IMAGE    STATUS\n"
            "abc123         nginx    Up 2 hours\n"
        )
        result = self._compress(table)
        assert "CONTAINER ID" in result

    def test_failed_row_kept(self) -> None:
        table = (
            "CONTAINER ID   IMAGE    STATUS\n"
            "abc123         nginx    Up 2 hours\n"
            "def456         app      Exited (1) 5 minutes ago\n"
        )
        result = self._compress(table)
        assert "Exited (1)" in result

    def test_all_healthy_summary(self) -> None:
        table = (
            "CONTAINER ID   IMAGE    STATUS\n"
            "abc123         nginx    Up 2 hours\n"
            "def456         redis    Up 1 hour\n"
            "ghi789         app      running\n"
        )
        result = self._compress(table)
        assert "healthy" in result.lower() or "running" in result.lower()

    def test_mixed_keeps_failures(self) -> None:
        table = (
            "NAME     STATUS    RESTARTS\n"
            "web-1    Running   0\n"
            "web-2    Failed    3\n"
            "web-3    Running   0\n"
        )
        result = self._compress(table)
        assert "Failed" in result

    def test_registered(self) -> None:
        assert _get_strategy("table") is not None


# ── json-fields strategy ──────────────────────────────────────────────────────

class TestJsonFieldsStrategy:
    def _compress(self, output: str) -> str:
        from contextclipper.engine.strategies import _strategy_json_fields
        return "\n".join(_strategy_json_fields(output.splitlines(), "service", 0))

    def test_extracts_message_and_level(self) -> None:
        line = json.dumps({
            "ts": "2026-04-29T10:00:00Z",
            "level": "error",
            "message": "database timeout",
            "request_id": "abc123",
            "duration_ms": 5000,
            "user_agent": "Mozilla/5.0 ...",
        })
        result = self._compress(line)
        parsed = json.loads(result)
        assert parsed["level"] == "error"
        assert parsed["message"] == "database timeout"
        assert "request_id" not in parsed
        assert "user_agent" not in parsed

    def test_non_json_lines_preserved(self) -> None:
        result = self._compress("plain text line")
        assert result == "plain text line"

    def test_mixed_json_and_text(self) -> None:
        lines = [
            'Starting service...',
            json.dumps({"level": "info", "msg": "ready", "pid": 12345, "version": "1.0"}),
            'Shutting down...',
        ]
        result = self._compress("\n".join(lines))
        assert "Starting service" in result
        assert "Shutting down" in result
        output_lines = result.splitlines()
        json_lines = [l for l in output_lines if l.startswith("{")]
        assert len(json_lines) == 1
        parsed = json.loads(json_lines[0])
        assert "pid" not in parsed

    def test_registered(self) -> None:
        assert _get_strategy("json-fields") is not None


# ── Integration: strategies via compress_output ────────────────────────────

class TestStrategiesViaCompressOutput:
    def test_log_strategy_invokable(self) -> None:
        output = "\n".join([f"DEBUG line {i}" for i in range(30)] + ["ERROR critical failure"])
        cr = compress_output("app.log", output, 0, strategy="log")
        assert "ERROR critical failure" in cr.compressed

    def test_diff_strategy_invokable(self) -> None:
        diff = (
            "diff --git a/x.py b/x.py\n"
            "@@ -1,5 +1,6 @@\n"
            " context\n"
            "+new line\n"
        )
        cr = compress_output("git diff", diff, 0, strategy="diff")
        assert "+new line" in cr.compressed

    def test_unknown_strategy_falls_back(self) -> None:
        cr = compress_output("cmd", "output\n", 0, strategy="nonexistent-strategy")
        assert "output" in cr.compressed

    def test_strategy_token_reduction(self) -> None:
        import contextclipper.engine.strategies  # noqa: F401
        lines = [f"DEBUG verbose debug line number {i} with extra data" for i in range(100)]
        output = "\n".join(lines)
        cr = compress_output("cmd", output, 0, strategy="log")
        assert cr.reduction_pct >= 30
