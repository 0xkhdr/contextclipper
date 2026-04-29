"""Tests for the raw output tee store."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine import tee as tee_module
from contextclipper.engine.tee import get_raw, save_raw  # type: ignore[import-not-found]


class TestTeeStore:
    def test_save_and_retrieve(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        uid = save_raw("git status", "some output", 0)
        assert uid
        content = get_raw(uid)
        assert content is not None
        assert "some output" in content

    def test_missing_id_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        result = get_raw("nonexistent_id_xyz")
        assert result is None

    def test_ttl_expiry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        monkeypatch.setattr(tee_module, "TTL_SECONDS", -1)  # immediately expired
        uid = save_raw("git status", "some output", 0)
        result = get_raw(uid)
        assert result is None

    def test_unique_ids(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        id1 = save_raw("cmd1", "out1", 1)
        id2 = save_raw("cmd2", "out2", 1)
        assert id1 != id2
