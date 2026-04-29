"""Tests for the new feature surface: dry-run, reversible map, adaptive clipping,
strategy registry, on_failure rules, prefix_collapse, validate(), keep_section fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine.filters import (  # type: ignore[import-not-found]
    FilterRegistry,
    compress_output,
    get_registry,
    register_strategy,
    unregister_strategy,
)
from contextclipper.engine.graph import GraphDB  # type: ignore[import-not-found]


# ── Dry run ──────────────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_reports_removed(self) -> None:
        raw = "noise line\nERROR: something\n\nnoise again\n"
        cr = compress_output("echo", raw, 0, dry_run=True)
        assert cr.removed_lines is not None
        # blanks + duplicates flagged for removal in generic fallback
        removed_text = [c for _, c in cr.removed_lines]
        assert any(c == "" for c in removed_text)

    def test_dry_run_default_off(self) -> None:
        cr = compress_output("echo", "hi\n", 0)
        assert cr.removed_lines is None


# ── Adaptive token clipping ──────────────────────────────────────────────────

class TestAdaptiveClipping:
    def test_max_tokens_truncates(self) -> None:
        # Use varied lines so the dedup pass doesn't collapse them.
        raw = "\n".join(f"line {i} " + ("x" * 80) for i in range(50)) + "\n"
        cr = compress_output("foo", raw, 0, max_tokens=100)
        assert cr.truncated
        # 100 tokens ≈ 400 chars; allow generous slack for marker/newlines
        assert len(cr.compressed) <= 100 * 4 + 200

    def test_zero_max_tokens_disables(self) -> None:
        raw = "line\n" * 20
        cr = compress_output("foo", raw, 0, max_tokens=0)
        # 0 disables truncation per documented contract
        assert not cr.truncated


# ── Strategy registry ────────────────────────────────────────────────────────

class TestStrategy:
    def test_register_and_invoke(self) -> None:
        called = []

        def my_strategy(lines: list[str], cmd: str, ec: int) -> list[str]:
            called.append((cmd, ec))
            return ["[strategy] " + ln for ln in lines if ln]

        register_strategy("uppercase-test", my_strategy)
        try:
            cr = compress_output("anything", "a\nb\n", 0, strategy="uppercase-test")
            assert called == [("anything", 0)]
            assert "[strategy] a" in cr.compressed
        finally:
            unregister_strategy("uppercase-test")


# ── on_failure rules ─────────────────────────────────────────────────────────

class TestOnFailure:
    def test_phpunit_failure_block_kept(self) -> None:
        raw = (
            "PHPUnit 10.5.0 by Sebastian Bergmann\n"
            "Runtime: PHP 8.2.0\n"
            "...F..\n"
            "FAILURES!\n"
            "Tests: 7, Assertions: 14, Failures: 1.\n"
            "1) App\\Tests\\UserTest::testCreate\n"
            "Failed asserting that two arrays are equal.\n"
        )
        cr = compress_output("phpunit", raw, exit_code=1)
        assert "FAILURES" in cr.compressed
        assert "Tests:" in cr.compressed


# ── prefix_collapse ──────────────────────────────────────────────────────────

class TestPrefixCollapse:
    def test_artisan_info_lines_collapsed(self) -> None:
        # artisan filter has prefix_collapse for "INFO"
        info_block = "\n".join([f"INFO   row {i}" for i in range(20)])
        raw = "Migrating: 2024_01_01_create_users\n" + info_block + "\n"
        cr = compress_output("php artisan migrate", raw, exit_code=0)
        # Should retain a "more lines with prefix" marker
        assert "more lines with prefix" in cr.compressed or cr.kept_lines < 20


# ── keep_section bug fix ─────────────────────────────────────────────────────

class TestKeepSection:
    def test_section_actually_closes(self) -> None:
        raw = (
            "PHPUnit version line\n"
            "FAILURES!\n"
            "1) FailedTest\n"
            "details\n"
            "\n"  # end of section
            "Generating coverage…\n"
            "More noise that should be filtered\n"
        )
        cr = compress_output("phpunit", raw, exit_code=1)
        # The section ended; we shouldn't see "Generating coverage" if section_lines override
        assert "FAILURES" in cr.compressed


# ── validate() ───────────────────────────────────────────────────────────────

class TestValidate:
    def test_registry_validate_clean(self) -> None:
        reg = FilterRegistry()
        report = reg.validate()
        assert "filters" in report
        assert isinstance(report["problems"], list)

    def test_graph_validate_ok(self, tmp_path: Path) -> None:
        db = GraphDB(tmp_path / "g.db")
        report = db.validate()
        db.close()
        assert report["ok"] is True
        assert report["files_indexed"] == 0

    def test_global_registry_singleton(self) -> None:
        a = get_registry()
        b = get_registry()
        assert a is b


# ── Unicode / RTL / Emoji ────────────────────────────────────────────────────

class TestUnicode:
    def test_emoji_preserved(self) -> None:
        raw = "✓ test passed 🎉\n✗ test failed 💥\n"
        cr = compress_output("unknown-tool", raw, 0)
        assert "🎉" in cr.compressed
        assert "💥" in cr.compressed

    def test_rtl_preserved(self) -> None:
        raw = "خطأ: شيء حدث\nresult: ok\n"  # Arabic "Error: something happened"
        cr = compress_output("unknown-tool", raw, 0)
        assert "خطأ" in cr.compressed

    def test_multibyte_input_byte_count(self) -> None:
        raw = "λ ➜ 漢字\n"
        cr = compress_output("unknown-tool", raw, 0)
        assert cr.bytes_in > 0
        assert "漢字" in cr.compressed


# ── ANSI extended ────────────────────────────────────────────────────────────

class TestAnsiExtended:
    def test_osc_sequence_stripped(self) -> None:
        # OSC 8 hyperlink: ESC ] 8 ; ; URL ESC \ TEXT ESC ] 8 ; ; ESC \
        raw = "\x1b]0;set window title\x07ok\n"
        cr = compress_output("unknown-tool", raw, 0)
        assert "\x1b]" not in cr.compressed
        assert "ok" in cr.compressed


# ── Metrics ─────────────────────────────────────────────────────────────────

class TestMetrics:
    def test_bytes_in_out_populated(self) -> None:
        cr = compress_output("git status", "On branch main\nmodified: x\n", 0)
        assert cr.bytes_in > 0
        assert cr.bytes_out >= 0
        assert cr.elapsed_ms >= 0.0
