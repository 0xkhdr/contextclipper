"""Tests for the streaming filter engine (Phase 1.1)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from contextclipper.engine.filters import FilterRule  # type: ignore[import-not-found]
from contextclipper.engine.streaming import StreamingFilter, StreamStats  # type: ignore[import-not-found]


def _make_rule(**kwargs) -> FilterRule:
    return FilterRule(**kwargs)


# ── StreamingFilter unit tests ────────────────────────────────────────────────

class TestStreamingFilterDropKeep:
    def test_drop_matching_removes_line(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="drop_matching", pattern=r"^DEBUG"),
        ])
        assert sf.feed("DEBUG: verbose stuff") == []
        assert sf.feed("INFO: important") == ["INFO: important"]

    def test_keep_matching_wins_over_drop(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="drop_matching", pattern=r"^.+", priority=0),
            _make_rule(type="keep_matching", pattern=r"ERROR", priority=10),
        ])
        kept = sf.feed("ERROR: something bad")
        assert kept == ["ERROR: something bad"]
        assert sf.feed("noise line") == []

    def test_no_rules_passes_all(self) -> None:
        sf = StreamingFilter([])
        assert sf.feed("hello world") == ["hello world"]

    def test_regex_replace_applied(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="regex_replace", pattern=r"\d+", replacement="N"),
        ])
        result = sf.feed("Run 42 tests in 5.3s")
        assert result == ["Run N tests in N.Ns"]


class TestStreamingFilterHead:
    def test_head_limit_respected(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="head", lines=3),
        ])
        out = []
        for i in range(10):
            out.extend(sf.feed(f"line {i}"))
        # Should only get first 3
        assert len(out) == 3
        assert out[0] == "line 0"
        assert out[2] == "line 2"

    def test_head_limit_zero_passes_nothing(self) -> None:
        sf = StreamingFilter([_make_rule(type="head", lines=0)])
        assert sf.feed("anything") == []


class TestStreamingFilterSection:
    def test_keep_section_captured(self) -> None:
        sf = StreamingFilter([
            _make_rule(
                type="keep_section",
                start_pattern=r"^START",
                end_pattern=r"^END",
            )
        ])
        assert sf.feed("before section") == []
        start_out = sf.feed("START here")
        assert start_out == ["START here"]
        mid = sf.feed("middle content")
        assert mid == ["middle content"]
        end_out = sf.feed("END marker")
        assert end_out == ["END marker"]
        assert sf.feed("after section") == []

    def test_section_closes_properly(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="keep_section", start_pattern=r"FAIL", end_pattern=r"^$"),
        ])
        sf.feed("PASS test1")
        in_sec = sf.feed("FAIL test2")
        assert "FAIL test2" in in_sec
        sf.feed("details")
        sf.feed("")  # closes section
        assert sf.feed("noise after") == []


class TestStreamingFilterPrefixCollapse:
    def test_prefix_collapse_short_block_emits_all(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="prefix_collapse", prefix="INFO  ", max_lines=5),
        ])
        for i in range(3):
            sf.feed(f"INFO  line {i}")
        flushed = sf.flush()
        # All 3 lines should appear (under max_lines=5)
        assert len(flushed) == 3

    def test_prefix_collapse_long_block_emits_summary(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="prefix_collapse", prefix="INFO  ", max_lines=3),
        ])
        for i in range(10):
            sf.feed(f"INFO  line {i}")
        flushed = sf.flush()
        # First 3 lines + summary marker
        assert len(flushed) == 4
        assert "+7 more lines" in flushed[-1]

    def test_prefix_collapse_non_matching_flushes_block(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="prefix_collapse", prefix="LOG ", max_lines=2),
        ])
        sf.feed("LOG line 1")
        sf.feed("LOG line 2")
        sf.feed("LOG line 3")
        # Non-prefix line should flush pending block
        out = sf.feed("ERROR: something")
        assert any("LOG" in l for l in out)
        assert "ERROR: something" in out


class TestStreamingFilterDedup:
    def test_consecutive_duplicates_collapsed(self) -> None:
        sf = StreamingFilter([])
        out = []
        for _ in range(5):
            out.extend(sf.feed("same line"))
        # First occurrence emitted, rest buffered; flush emits repeat marker
        out.extend(sf.flush())
        assert any("repeated" in l for l in out)
        assert out[0] == "same line"


class TestStreamingFilterBatchOnly:
    def test_tail_rule_noted_as_batch_only(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="tail", lines=10),
        ])
        assert "tail" in sf.batch_only_rules

    def test_json_select_noted_as_batch_only(self) -> None:
        sf = StreamingFilter([
            _make_rule(type="json_select", fields=[".status"]),
        ])
        assert "json_select" in sf.batch_only_rules

    def test_batch_notice_emitted_once(self) -> None:
        sf = StreamingFilter([_make_rule(type="tail", lines=5)])
        first = sf.feed("line1")
        second = sf.feed("line2")
        notices = [l for l in first + second if "streaming mode" in l]
        assert len(notices) == 1  # emitted exactly once


# ── Integration: streaming with a real subprocess ────────────────────────────

class TestRunStreaming:
    def test_echo_passthrough(self, capsys) -> None:
        from contextclipper.engine.streaming import run_streaming

        exit_code_ref = [0]
        stats = run_streaming("echo 'hello streaming'", None, exit_code_ref)
        captured = capsys.readouterr()
        assert "hello streaming" in captured.out
        assert exit_code_ref[0] == 0
        assert stats.kept_lines >= 1

    def test_stats_populated(self, capsys) -> None:
        from contextclipper.engine.streaming import run_streaming

        exit_code_ref = [0]
        stats = run_streaming(
            "printf 'a\\nb\\nc\\n'", None, exit_code_ref
        )
        assert stats.original_lines == 3
        assert stats.kept_lines == 3
        assert stats.bytes_in > 0
        assert stats.elapsed_ms >= 0

    def test_exit_code_propagated(self, capsys) -> None:
        from contextclipper.engine.streaming import run_streaming

        exit_code_ref = [0]
        run_streaming("bash -c 'exit 42'", None, exit_code_ref)
        assert exit_code_ref[0] == 42

    def test_100k_lines_constant_memory(self, capsys) -> None:
        """Streaming 100k lines should complete without OOM; memory usage is bounded."""
        import tracemalloc
        from contextclipper.engine.streaming import run_streaming

        tracemalloc.start()
        exit_code_ref = [0]
        # Generate 100k lines via python in subprocess
        run_streaming(
            "python3 -c \"for i in range(100000): print(f'line {i}')\"",
            None,
            exit_code_ref,
        )
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        # Peak memory for the filter state should be well under 10 MB
        assert peak < 10 * 1024 * 1024, f"Peak memory {peak // 1024} KiB exceeds 10 MiB"
        assert exit_code_ref[0] == 0

    def test_max_tokens_budget_applied(self, capsys) -> None:
        from contextclipper.engine.streaming import run_streaming

        exit_code_ref = [0]
        stats = run_streaming(
            "python3 -c \"for i in range(1000): print('x' * 50)\"",
            None,
            exit_code_ref,
            max_tokens=100,
        )
        captured = capsys.readouterr()
        # With max_tokens=100 (≈400 chars), output should be truncated
        assert stats.truncated or stats.kept_lines < 1000
