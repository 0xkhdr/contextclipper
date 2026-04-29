"""Tests for per-project .ctxclp.toml config loader."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from contextclipper.engine.project_config import (
    ProjectConfig,
    _find_config_file,
    load_project_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Return a temp directory acting as a project root."""
    return tmp_path


def _write_config(directory: Path, content: str) -> Path:
    cfg = directory / ".ctxclp.toml"
    cfg.write_text(textwrap.dedent(content))
    return cfg


# ---------------------------------------------------------------------------
# _find_config_file
# ---------------------------------------------------------------------------


class TestFindConfigFile:
    def test_finds_file_in_start_dir(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[ctxclp]\n")
        found = _find_config_file(tmp_project)
        assert found == tmp_project / ".ctxclp.toml"

    def test_finds_file_in_parent(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[ctxclp]\n")
        subdir = tmp_project / "src" / "app"
        subdir.mkdir(parents=True)
        found = _find_config_file(subdir)
        assert found == tmp_project / ".ctxclp.toml"

    def test_returns_none_when_missing(self, tmp_project: Path) -> None:
        assert _find_config_file(tmp_project) is None

    def test_closest_file_wins(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[ctxclp]\ncompression = 'conservative'\n")
        sub = tmp_project / "sub"
        sub.mkdir()
        inner = _write_config(sub, "[ctxclp]\ncompression = 'aggressive'\n")
        found = _find_config_file(sub)
        assert found == inner


# ---------------------------------------------------------------------------
# load_project_config — defaults
# ---------------------------------------------------------------------------


class TestLoadProjectConfigDefaults:
    def test_returns_defaults_when_no_file(self, tmp_project: Path) -> None:
        cfg = load_project_config(tmp_project)
        assert not cfg.found
        assert cfg.max_tokens is None
        assert cfg.compression == "balanced"
        assert cfg.filter_dirs == []
        assert cfg.passthrough_commands == []
        assert cfg.disable_filters == []

    def test_found_flag(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[ctxclp]\n")
        cfg = load_project_config(tmp_project)
        assert cfg.found
        assert cfg.config_path == tmp_project / ".ctxclp.toml"


# ---------------------------------------------------------------------------
# load_project_config — field parsing
# ---------------------------------------------------------------------------


class TestLoadProjectConfigParsing:
    def test_max_tokens(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[ctxclp]\nmax_tokens = 2000\n")
        cfg = load_project_config(tmp_project)
        assert cfg.max_tokens == 2000

    def test_max_tokens_invalid_ignored(self, tmp_project: Path) -> None:
        _write_config(tmp_project, '[ctxclp]\nmax_tokens = "bad"\n')
        cfg = load_project_config(tmp_project)
        assert cfg.max_tokens is None

    def test_max_tokens_negative_ignored(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[ctxclp]\nmax_tokens = -1\n")
        cfg = load_project_config(tmp_project)
        assert cfg.max_tokens is None

    def test_compression_levels(self, tmp_project: Path) -> None:
        for level in ("conservative", "balanced", "aggressive"):
            _write_config(tmp_project, f"[ctxclp]\ncompression = '{level}'\n")
            cfg = load_project_config(tmp_project)
            assert cfg.compression == level

    def test_compression_invalid_defaults_to_balanced(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[ctxclp]\ncompression = 'turbo'\n")
        cfg = load_project_config(tmp_project)
        assert cfg.compression == "balanced"

    def test_filter_dirs_absolute(self, tmp_project: Path) -> None:
        extra_dir = tmp_project / "my-filters"
        extra_dir.mkdir()
        _write_config(tmp_project, f'[ctxclp]\nfilter_dirs = ["{extra_dir}"]\n')
        cfg = load_project_config(tmp_project)
        assert extra_dir in cfg.filter_dirs

    def test_filter_dirs_relative(self, tmp_project: Path) -> None:
        extra_dir = tmp_project / "tools" / "filters"
        extra_dir.mkdir(parents=True)
        _write_config(tmp_project, '[ctxclp]\nfilter_dirs = ["tools/filters"]\n')
        cfg = load_project_config(tmp_project)
        assert extra_dir.resolve() in cfg.filter_dirs

    def test_filter_dirs_nonexistent_skipped(self, tmp_project: Path) -> None:
        _write_config(tmp_project, '[ctxclp]\nfilter_dirs = ["nonexistent-dir"]\n')
        cfg = load_project_config(tmp_project)
        assert cfg.filter_dirs == []

    def test_passthrough_commands(self, tmp_project: Path) -> None:
        _write_config(
            tmp_project,
            '[ctxclp]\npassthrough_commands = ["my-tool", "^interactive"]\n',
        )
        cfg = load_project_config(tmp_project)
        assert "my-tool" in cfg.passthrough_commands
        assert "^interactive" in cfg.passthrough_commands

    def test_disable_filters(self, tmp_project: Path) -> None:
        _write_config(tmp_project, '[ctxclp]\ndisable_filters = ["docker", "node"]\n')
        cfg = load_project_config(tmp_project)
        assert "docker" in cfg.disable_filters
        assert "node" in cfg.disable_filters


# ---------------------------------------------------------------------------
# ProjectConfig helpers
# ---------------------------------------------------------------------------


class TestProjectConfigHelpers:
    def _cfg(self, **kwargs) -> ProjectConfig:  # type: ignore[no-untyped-def]
        return ProjectConfig(**kwargs)

    def test_should_passthrough_exact_match(self) -> None:
        cfg = self._cfg(passthrough_commands=["my-tool"])
        assert cfg.should_passthrough("my-tool arg1 arg2")

    def test_should_passthrough_regex_match(self) -> None:
        cfg = self._cfg(passthrough_commands=["^interactive"])
        assert cfg.should_passthrough("interactive-shell")

    def test_should_passthrough_no_match(self) -> None:
        cfg = self._cfg(passthrough_commands=["other-tool"])
        assert not cfg.should_passthrough("my-tool")

    def test_is_filter_disabled(self) -> None:
        cfg = self._cfg(disable_filters=["docker"])
        assert cfg.is_filter_disabled("docker")
        assert not cfg.is_filter_disabled("git")

    def test_found_false_when_no_path(self) -> None:
        cfg = ProjectConfig()
        assert not cfg.found

    def test_found_true_with_path(self, tmp_path: Path) -> None:
        cfg = ProjectConfig(config_path=tmp_path / ".ctxclp.toml")
        assert cfg.found


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_malformed_toml_returns_defaults(self, tmp_project: Path) -> None:
        (tmp_project / ".ctxclp.toml").write_text("this is not valid toml = = = [[[")
        cfg = load_project_config(tmp_project)
        # Should not raise; returns safe defaults with path set
        assert cfg.config_path is not None
        assert cfg.max_tokens is None

    def test_empty_toml_is_valid(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "")
        cfg = load_project_config(tmp_project)
        assert cfg.found
        assert cfg.compression == "balanced"

    def test_no_ctxclp_section_is_valid(self, tmp_project: Path) -> None:
        _write_config(tmp_project, "[other_tool]\nfoo = 'bar'\n")
        cfg = load_project_config(tmp_project)
        assert cfg.found
        assert cfg.max_tokens is None
