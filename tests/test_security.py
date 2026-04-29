"""Security-focused tests: ReDoS bounds, path traversal, redacted persistence."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine import filters as filters_mod  # type: ignore[import-not-found]
from contextclipper.engine import tee as tee_module  # type: ignore[import-not-found]
from contextclipper.engine.filters import compress_output  # type: ignore[import-not-found]
from contextclipper.engine.stats import StatsDB  # type: ignore[import-not-found]
from contextclipper.engine.tee import get_raw, save_raw  # type: ignore[import-not-found]


class TestInputBounds:
    def test_oversize_input_truncated(self) -> None:
        big = "a" * (filters_mod.MAX_INPUT_BYTES + 100)
        cr = compress_output("echo", big, 0)
        assert cr.truncated
        assert "truncated" in str(cr).lower()

    def test_long_line_truncated_to_max_line_bytes(self) -> None:
        long_line = "x" * (filters_mod.MAX_LINE_BYTES + 1000)
        cr = compress_output("echo", long_line, 0)
        # Should not hang; line gets capped before regex runs
        assert "[line truncated]" in cr.compressed

    def test_huge_input_is_bounded_not_hung(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Total input size is capped; engine returns quickly even on a >cap blob."""
        monkeypatch.setattr(filters_mod, "MAX_INPUT_BYTES", 1024)
        big = "loud line of output\n" * 1000  # ~20 KB, well over 1 KB cap
        t0 = time.monotonic()
        cr = compress_output("unknown-tool", big, 0)
        assert time.monotonic() - t0 < 5.0
        assert cr.truncated

    def test_redos_pattern_load_does_not_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A user-supplied catastrophically-backtracking pattern loads cleanly.

        Catastrophic *evaluation* of such a pattern on long input is the user's
        responsibility (Python's stdlib `re` has no timeout). Our defense is the
        per-line and total-input byte caps in :data:`MAX_LINE_BYTES` /
        :data:`MAX_INPUT_BYTES`. This test only verifies the engine accepts the
        pattern without crashing and that ``find()`` returns it for the matching
        command.
        """
        user_dir = tmp_path / "contextclipper" / "filters"
        user_dir.mkdir(parents=True)
        (user_dir / "evil.toml").write_text(
            '[filter]\nname = "evil"\ndescription = "ReDoS test"\n\n'
            '[[filter.patterns]]\nmatch_command = "^redos-test"\n\n'
            '[[filter.rules]]\ntype = "drop_matching"\npattern = "^(a+)+$"\n'
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        reg = filters_mod.FilterRegistry()
        names = {f.name for f in reg.all_filters()}
        assert "evil" in names
        assert reg.find("redos-test foo") is not None


class TestTeeHardening:
    def test_tee_dir_perms_restricted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        save_raw("ls", "hello", 0)
        mode = (tmp_path / "tee").stat().st_mode & 0o777
        assert mode == 0o700

    def test_tee_file_perms_restricted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        uid = save_raw("ls", "hello", 0)
        assert uid is not None
        f = tmp_path / "tee" / f"{uid}.log"
        mode = f.stat().st_mode & 0o777
        assert mode == 0o600

    def test_tee_redacts_secrets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        uid = save_raw("curl --token=verysecretvalue x", "API_TOKEN=verysecretvalue\nresponse=ok\n", 0)
        assert uid is not None
        content = get_raw(uid)
        assert content is not None
        assert "verysecretvalue" not in content
        assert "[REDACTED]" in content

    def test_tee_disabled_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        monkeypatch.setenv("CTXCLP_DISABLE_TEE", "1")
        assert save_raw("any", "out", 0) is None

    def test_tee_get_raw_rejects_traversal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tee_module, "TEE_DIR", tmp_path / "tee")
        assert get_raw("../etc/passwd") is None
        assert get_raw("not-hex!") is None


class TestStatsRedaction:
    def test_command_with_token_is_redacted(self, tmp_path: Path) -> None:
        db = StatsDB(db_path=tmp_path / "s.db")
        db.record("curl --token=mysecret123 example.com", 5, 1)
        s = db.summary(days=1)
        db.close()
        top = s["top_commands"][0]["command"]
        assert "mysecret123" not in top
        assert "[REDACTED]" in top

    def test_disabled_stats_no_writes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CTXCLP_DISABLE_STATS", "1")
        db = StatsDB(db_path=tmp_path / "s.db")
        db.record("git status", 5, 1)
        s = db.summary()
        db.close()
        assert s["total_commands"] == 0
