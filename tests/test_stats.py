"""Tests for the local analytics stats store."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine.stats import StatsDB  # type: ignore[import-not-found]


class TestStatsDB:
    def _db(self, tmp_path: Path) -> StatsDB:
        return StatsDB(db_path=tmp_path / "stats.db")

    def test_record_and_summary(self, tmp_path: Path) -> None:
        db = self._db(tmp_path)
        db.record("git status", original_lines=20, kept_lines=5, exit_code=0)
        db.record("composer install", original_lines=50, kept_lines=3, exit_code=0)
        s = db.summary(days=7)
        db.close()
        assert s["total_commands"] == 2
        assert s["total_original_lines"] == 70
        assert s["total_kept_lines"] == 8
        assert s["reduction_pct"] > 0

    def test_top_commands(self, tmp_path: Path) -> None:
        db = self._db(tmp_path)
        for _ in range(5):
            db.record("git status", 10, 3)
        db.record("composer install", 50, 5)
        s = db.summary()
        db.close()
        assert s["top_commands"][0]["command"] == "git status"
        assert s["top_commands"][0]["count"] == 5

    def test_empty_db(self, tmp_path: Path) -> None:
        db = self._db(tmp_path)
        s = db.summary()
        db.close()
        assert s["total_commands"] == 0
        assert s["reduction_pct"] == 0.0
