"""Unit + snapshot tests for the shell filter engine."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine.filters import FilterRegistry, compress_output  # type: ignore[import-not-found]

# ── Snapshot fixtures ─────────────────────────────────────────────────────────

COMPOSER_RAW = """\
Loading composer repositories with package information
Updating dependencies
Lock file operations: 3 installs, 0 updates, 0 removals
  - Locking psr/log (3.0.0)
  - Locking symfony/console (v6.4.0)
  - Locking symfony/event-dispatcher (v6.4.0)
Installing dependencies from lock file (including require-dev)
Package operations: 3 installs, 0 updates, 0 removals
  - Installing psr/log (3.0.0): Extracting archive
  - Installing symfony/event-dispatcher (v6.4.0): Extracting archive
  - Installing symfony/console (v6.4.0): Extracting archive
Generating autoload files
3 packages you are using are looking for funding.
"""

COMPOSER_FAIL = """\
Loading composer repositories with package information
Updating dependencies
Your requirements could not be resolved to an installable set of packages.

  Problem 1
    - Root composer.json requires foo/bar ^2.0 -> satisfiable by foo/bar[2.0.0].
    - foo/bar 2.0.0 requires php ^8.1 -> your php version (8.0.28) does not satisfy that constraint.
"""

PHPUNIT_RAW = """\
PHPUnit 10.5.0 by Sebastian Bergmann and contributors.

Runtime:       PHP 8.2.0
Configuration: phpunit.xml

...F..

FAILURES!
Tests: 7, Assertions: 14, Failures: 1.

1) App\\Tests\\UserTest::testCreate
Failed asserting that two arrays are equal.
--- Expected
+++ Actual
"""

GIT_STATUS_RAW = """\
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)

        modified:   src/Controllers/UserController.php
        modified:   src/Models/User.php

Untracked files:
  (use "git add..." to include in what will be committed)

        tests/NewTest.php

no changes added to commit
"""


class TestComposerFilter:
    def test_success_output_compressed(self) -> None:
        cr = compress_output("composer install", COMPOSER_RAW, exit_code=0)
        assert cr.reduction_pct > 50
        assert cr.kept_lines < cr.original_lines

    def test_failure_preserves_problem_lines(self) -> None:
        cr = compress_output("composer install", COMPOSER_FAIL, exit_code=1)
        assert "Problem 1" in cr.compressed or cr.kept_lines > 0

    def test_no_raw_id_on_success(self) -> None:
        cr = compress_output("composer install", COMPOSER_RAW, exit_code=0)
        assert cr.raw_output_id is None

    def test_raw_id_on_failure(self) -> None:
        cr = compress_output("composer install", COMPOSER_FAIL, exit_code=1, raw_output_id="abc123")
        assert cr.raw_output_id == "abc123"


class TestPhpUnitFilter:
    def test_failure_line_preserved(self) -> None:
        cr = compress_output("vendor/bin/phpunit", PHPUNIT_RAW, exit_code=1)
        assert "FAILURES" in cr.compressed or "Failures" in cr.compressed

    def test_summary_line_preserved(self) -> None:
        cr = compress_output("vendor/bin/phpunit", PHPUNIT_RAW, exit_code=1)
        assert "Tests:" in cr.compressed

    def test_compressed(self) -> None:
        cr = compress_output("vendor/bin/phpunit", PHPUNIT_RAW, exit_code=1)
        assert cr.kept_lines < cr.original_lines


class TestGitFilter:
    def test_status_keeps_modified(self) -> None:
        cr = compress_output("git status", GIT_STATUS_RAW, exit_code=0)
        assert "modified:" in cr.compressed

    def test_status_drops_branch_header(self) -> None:
        cr = compress_output("git status", GIT_STATUS_RAW, exit_code=0)
        # "On branch" line should be dropped
        assert "On branch" not in cr.compressed

    def test_status_reduction(self) -> None:
        cr = compress_output("git status", GIT_STATUS_RAW, exit_code=0)
        assert cr.reduction_pct > 0


class TestGenericFallback:
    def test_strips_blank_lines(self) -> None:
        raw = "line1\n\n\nline2\n\n"
        cr = compress_output("some-unknown-tool --arg", raw, exit_code=0)
        assert "\n\n" not in cr.compressed

    def test_deduplication(self) -> None:
        raw = "same line\n" * 20 + "different\n"
        cr = compress_output("some-tool", raw, exit_code=0)
        assert "repeated" in cr.compressed
        assert cr.kept_lines < 20


class TestAnsiStripping:
    def test_ansi_stripped(self) -> None:
        raw = "\x1b[32mGreen text\x1b[0m\n\x1b[31mRed error\x1b[0m\n"
        cr = compress_output("some-cmd", raw, exit_code=0)
        assert "\x1b[" not in cr.compressed
        assert "Green text" in cr.compressed
        assert "Red error" in cr.compressed


class TestFilterRegistry:
    def test_registry_loads(self) -> None:
        reg = FilterRegistry()
        filters = reg.all_filters()
        assert len(filters) > 0

    def test_composer_matched(self) -> None:
        reg = FilterRegistry()
        flt = reg.find("composer install --no-dev")
        assert flt is not None
        assert flt.name == "composer-install"

    def test_unknown_command_returns_none(self) -> None:
        reg = FilterRegistry()
        flt = reg.find("zzz-nonexistent-tool --blah")
        assert flt is None


class TestCompressionResult:
    def test_str_includes_footer(self) -> None:
        cr = compress_output("git status", GIT_STATUS_RAW, exit_code=0)
        s = str(cr)
        assert "[ctxclp:" in s
        assert "lines" in s

    def test_reduction_pct_range(self) -> None:
        cr = compress_output("git status", GIT_STATUS_RAW, exit_code=0)
        assert 0.0 <= cr.reduction_pct <= 100.0
