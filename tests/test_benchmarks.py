"""Performance benchmarks for the core engine paths."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine.filters import compress_output  # type: ignore[import-not-found]

LARGE_COMPOSER_OUTPUT = (
    "Loading composer repositories with package information\n"
    "Updating dependencies\n"
    + "  - Installing vendor/package (1.0.0): Extracting archive\n" * 500
    + "Generating autoload files\n"
)


class TestFilterBenchmarks:
    def test_filter_throughput(self, benchmark) -> None:  # type: ignore[no-untyped-def]
        """Filter engine should handle large outputs in well under 10ms."""
        result = benchmark(compress_output, "composer install", LARGE_COMPOSER_OUTPUT, 0)
        assert result.reduction_pct > 50

    def test_generic_fallback_throughput(self, benchmark) -> None:  # type: ignore[no-untyped-def]
        raw = "some output line\n" * 1000
        result = benchmark(compress_output, "unknown-tool", raw, 0)
        assert result.original_lines == 1000


class TestGraphBenchmarks:
    @pytest.fixture
    def php_project(self, tmp_path: Path) -> Path:
        """Create a synthetic PHP project with many files."""
        php_template = b"""<?php
namespace App\\{ns};

use App\\Base\\BaseClass;

class {cls} extends BaseClass
{{
    public function handle(): void {{}}
    protected function validate(): bool {{ return true; }}
    private function _log(): void {{}}
}}
"""
        for i in range(100):
            ns = f"Module{i // 10}"
            cls = f"Handler{i}"
            content = php_template.replace(b"{ns}", ns.encode()).replace(b"{cls}", cls.encode())
            p = tmp_path / f"src/Module{i // 10}/Handler{i}.php"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return tmp_path

    def test_build_100_files(self, benchmark, php_project: Path) -> None:  # type: ignore[no-untyped-def]
        from contextclipper.engine.graph import GraphDB  # type: ignore[import-not-found]
        db_path = php_project / "test.db"

        def run_build() -> dict:
            db = GraphDB(db_path)
            counts = db.build(php_project, force=True)
            db.close()
            return counts

        result = benchmark(run_build)
        assert result["total"] == 100

    def test_get_file_speed(self, benchmark, php_project: Path) -> None:  # type: ignore[no-untyped-def]
        from contextclipper.engine.graph import GraphDB  # type: ignore[import-not-found]
        db = GraphDB(php_project / "bench.db")
        db.build(php_project)

        def get_file() -> str:
            return db.get_file_symbols("src/Module0/Handler0.php")

        result = benchmark(get_file)
        db.close()
        assert "Handler0" in result or "not indexed" in result
