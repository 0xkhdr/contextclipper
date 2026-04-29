"""Tests for telemetry / regret detection (Phase 5.1).

Tests verify that:
- suggestions() returns entries when fetch rate exceeds threshold
- suggestions() is empty when telemetry is disabled
- record_raw_pull() updates had_raw_pull when telemetry is enabled
- machine footer appears in compressed output
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from contextclipper.engine.stats import StatsDB  # type: ignore[import-not-found]


def _db(tmp_path: Path, telemetry: bool = False) -> StatsDB:
    env_val = "1" if telemetry else "0"
    os.environ["CTXCLP_TELEMETRY"] = env_val
    db = StatsDB(db_path=tmp_path / "stats.db")
    return db


class TestSuggestions:
    def test_suggestions_empty_without_telemetry(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=False)
        for _ in range(5):
            db.record("git status", 20, 5, raw_output_id="abc123")
        db.record_raw_pull("abc123")
        sug = db.suggestions()
        db.close()
        assert sug == []

    def test_suggestions_populated_with_telemetry(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=True)
        # 5 runs where 3 were followed by a raw pull (60% regret)
        for i in range(5):
            raw_id = f"uuid{i:04d}abcd"
            db.record("git log", 100, 10, filter_name="git", raw_output_id=raw_id)
            if i < 3:
                db.record_raw_pull(raw_id)

        sug = db.suggestions(threshold=0.3, min_runs=3)
        db.close()

        assert len(sug) > 0
        assert sug[0]["command_base"] == "git"
        assert sug[0]["fetch_rate_pct"] >= 30.0

    def test_suggestions_respect_threshold(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=True)
        # 10 runs, only 1 fetch (10% regret — below 30% threshold)
        for i in range(10):
            raw_id = f"lowregret{i:04d}"
            db.record("docker ps", 20, 5, filter_name="docker", raw_output_id=raw_id)
        db.record_raw_pull("lowregret0000")

        sug = db.suggestions(threshold=0.3, min_runs=3)
        db.close()
        assert sug == []

    def test_suggestions_respect_min_runs(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=True)
        # Only 2 runs with 100% regret — below min_runs=3
        for i in range(2):
            raw_id = f"fewruns{i:04d}"
            db.record("cargo build", 50, 50, filter_name="cargo", raw_output_id=raw_id)
            db.record_raw_pull(raw_id)

        sug = db.suggestions(threshold=0.3, min_runs=3)
        db.close()
        assert sug == []

    def test_had_raw_pull_updated_on_fetch(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=True)
        raw_id = "testid1234abcd"
        db.record("npm test", 80, 10, raw_output_id=raw_id)
        db.record_raw_pull(raw_id)

        # Check that had_raw_pull was set to 1 via audit
        records = db.audit(days=1)
        db.close()

        assert len(records) == 1
        assert records[0]["had_raw_pull"] is True

    def test_had_raw_pull_not_updated_without_telemetry(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=False)
        db.record("npm test", 80, 10, raw_output_id="no-telemetry-id")
        db.record_raw_pull("no-telemetry-id")

        records = db.audit(days=1)
        db.close()
        # raw_output_id was not stored (telemetry off), so had_raw_pull stays 0
        assert records[0]["had_raw_pull"] is False

    def test_suggestions_sorted_by_rate(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=True)
        # Command A: 40% regret
        for i in range(10):
            raw_id = f"cmda{i:04d}"
            db.record("pytest", 100, 20, filter_name="python", raw_output_id=raw_id)
            if i < 4:
                db.record_raw_pull(raw_id)

        # Command B: 80% regret
        for i in range(10):
            raw_id = f"cmdb{i:04d}"
            db.record("go test", 100, 20, filter_name="go", raw_output_id=raw_id)
            if i < 8:
                db.record_raw_pull(raw_id)

        sug = db.suggestions(threshold=0.3, min_runs=3)
        db.close()

        # Higher regret rate should appear first
        assert len(sug) >= 2
        assert sug[0]["fetch_rate_pct"] >= sug[1]["fetch_rate_pct"]


class TestMachineFooter:
    def test_machine_footer_in_output(self) -> None:
        import os
        os.environ["CTXCLP_INCLUDE_MACHINE_FOOTER"] = "1"
        from contextclipper.engine.filters import compress_output

        cr = compress_output("echo hi", "hello\n", 0, raw_output_id="deadbeef1234cafe")
        out = str(cr)
        import re
        m = re.search(r'\[CTXCLP:raw=([0-9a-f]+)\]', out)
        assert m is not None, f"Machine footer not found in: {out!r}"
        assert m.group(1) == "deadbeef1234cafe"

    def test_machine_footer_parseable(self) -> None:
        import re
        from contextclipper.engine.filters import CompressionResult

        cr = CompressionResult(
            compressed="output",
            original_lines=10,
            kept_lines=5,
            raw_output_id="deadbeef1234cafe",
        )
        footer = cr.machine_footer_line()
        assert footer is not None
        m = re.fullmatch(r'\[CTXCLP:raw=([0-9a-f]+)\]', footer)
        assert m is not None
        assert m.group(1) == "deadbeef1234cafe"

    def test_machine_footer_absent_without_uuid(self) -> None:
        from contextclipper.engine.filters import CompressionResult

        cr = CompressionResult(compressed="output", original_lines=1, kept_lines=1)
        assert cr.machine_footer_line() is None


class TestAllCommandStats:
    def test_returns_rows(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=False)
        for i in range(5):
            db.record("git status", 20, 5, filter_name="git")
        db.record("docker ps", 10, 3, filter_name="docker")
        rows = db.all_command_stats(days=7)
        db.close()
        names = [r["command_base"] for r in rows]
        assert "git" in names
        assert "docker" in names

    def test_high_regret_flagged(self, tmp_path: Path) -> None:
        db = _db(tmp_path, telemetry=True)
        for i in range(5):
            raw_id = f"fregret{i:04d}"
            db.record("terraform", 200, 20, filter_name="terraform", raw_output_id=raw_id)
            db.record_raw_pull(raw_id)
        rows = db.all_command_stats(days=7)
        db.close()
        tf_rows = [r for r in rows if r["command_base"] == "terraform"]
        assert any(r["high_regret"] for r in tf_rows)
