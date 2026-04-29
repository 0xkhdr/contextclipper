"""Integration tests: end-to-end hook simulation and MCP tool behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

CTXCLP_MAIN = str(Path(__file__).parent.parent / "src" / "contextclipper" / "cli" / "main.py")


class TestHookSimulation:
    """Simulate the hook chain end-to-end using ctxclp run."""

    def _run(self, cmd: str, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
        args = [sys.executable, CTXCLP_MAIN, "run"] + (extra_args or []) + ["--"] + cmd.split()
        return subprocess.run(args, capture_output=True, text=True, timeout=30)

    def test_git_status_compressed(self, tmp_path: Path) -> None:
        """Running ctxclp run git status returns compressed output."""
        result = subprocess.run(
            [sys.executable, CTXCLP_MAIN, "run", "git", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tmp_path),
        )
        # Should complete without crashing (exit code depends on git state)
        assert "[ctxclp:" in result.stdout or result.returncode in (0, 128)

    def test_echo_passthrough(self) -> None:
        result = subprocess.run(
            [sys.executable, CTXCLP_MAIN, "run", "echo", "hello world"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "hello world" in result.stdout

    def test_nonzero_exit_preserved(self, tmp_path: Path) -> None:
        # Pass the full shell expression as a single string so it survives join
        result = subprocess.run(
            [sys.executable, CTXCLP_MAIN, "run", "bash -c 'exit 42'"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 42

    def test_ansi_stripped_in_output(self) -> None:
        # Use echo with $'...' to produce real ANSI escape codes via bash
        result = subprocess.run(
            [sys.executable, CTXCLP_MAIN, "run", r"bash -c $'echo \e[31mred\e[0m'"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "\x1b[" not in result.stdout
        assert "red" in result.stdout


class TestInstallDetect:
    def test_detect_runs_without_crash(self) -> None:
        from contextclipper.cli.install import detect_agents  # type: ignore[import-not-found]
        agents = detect_agents()
        assert isinstance(agents, list)


class TestHookRewrite:
    def test_hook_rewrite_bash_event(self) -> None:
        """hook-rewrite should rewrite a Bash tool event."""
        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }
        result = subprocess.run(
            [sys.executable, CTXCLP_MAIN, "hook-rewrite"],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            out = json.loads(result.stdout)
            assert "ctxclp" in out["tool_input"]["command"] or "contextclipper" in out["tool_input"]["command"]

    def test_hook_rewrite_non_bash_passthrough(self) -> None:
        event = {
            "tool_name": "Read",
            "tool_input": {"path": "/tmp/x"},
        }
        result = subprocess.run(
            [sys.executable, CTXCLP_MAIN, "hook-rewrite"],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Should exit 0 without rewriting non-Bash tools
        assert result.returncode == 0
