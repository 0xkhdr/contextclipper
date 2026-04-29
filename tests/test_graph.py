"""Unit tests for the code graph indexer."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine.graph import GraphDB, _parse_php  # type: ignore[import-not-found]

SAMPLE_PHP = b"""<?php

namespace App\\Controllers;

use App\\Models\\User;
use App\\Services\\AuthService;

class UserController extends BaseController implements ContainerAwareInterface
{
    private AuthService $auth;

    public function __construct(AuthService $auth)
    {
        $this->auth = $auth;
    }

    public function index(): void
    {
        $users = User::all();
    }

    protected static function getStaticHelper(): string
    {
        return 'helper';
    }

    private function _internal(): void {}
}

interface ContainerAwareInterface
{
    public function setContainer($container): void;
}

trait Timestampable
{
    public function touch(): void {}
}

function globalHelper(string $name): string
{
    return strtolower($name);
}
"""


class TestPhpParser:
    def test_extracts_class(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        kinds = {s.kind for s in summary.symbols}
        assert "class" in kinds

    def test_extracts_interface(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        kinds = {s.kind for s in summary.symbols}
        assert "interface" in kinds

    def test_extracts_trait(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        kinds = {s.kind for s in summary.symbols}
        assert "trait" in kinds

    def test_extracts_methods(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        classes = [s for s in summary.symbols if s.kind == "class"]
        assert len(classes) > 0
        methods = classes[0].children
        method_names = {m.name for m in methods}
        assert "index" in method_names
        assert "__construct" in method_names

    def test_extracts_extends_dep(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        dep_kinds = {d[0] for d in summary.dependencies}
        assert "extends" in dep_kinds

    def test_extracts_implements_dep(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        dep_kinds = {d[0] for d in summary.dependencies}
        assert "implements" in dep_kinds

    def test_method_visibility(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        classes = [s for s in summary.symbols if s.kind == "class" and s.name == "UserController"]
        assert classes
        methods = {m.name: m for m in classes[0].children}
        assert methods["index"].visibility == "public"
        assert methods["_internal"].visibility == "private"
        assert methods["getStaticHelper"].is_static

    def test_namespace_in_fqn(self) -> None:
        summary = _parse_php(SAMPLE_PHP, "app/Controllers/UserController.php")
        classes = [s for s in summary.symbols if s.kind == "class"]
        assert any("Controllers" in c.fqn or "UserController" in c.fqn for c in classes)

    def test_empty_file(self) -> None:
        summary = _parse_php(b"<?php\n// empty\n", "empty.php")
        assert summary.symbols == []
        assert summary.dependencies == []


class TestGraphDB:
    def _make_db(self, tmp_path: Path) -> GraphDB:
        return GraphDB(tmp_path / "test.db")

    def _write_php(self, root: Path, rel: str, content: bytes = SAMPLE_PHP) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    def test_build_indexes_files(self, tmp_path: Path) -> None:
        self._write_php(tmp_path, "src/UserController.php")
        db = self._make_db(tmp_path)
        counts = db.build(tmp_path)
        db.close()
        assert counts["new"] == 1
        assert counts["total"] == 1

    def test_incremental_skips_unchanged(self, tmp_path: Path) -> None:
        self._write_php(tmp_path, "src/UserController.php")
        db = self._make_db(tmp_path)
        db.build(tmp_path)
        counts2 = db.build(tmp_path)
        db.close()
        assert counts2["skipped"] == 1
        assert counts2["new"] == 0

    def test_get_file_symbols_returns_markdown(self, tmp_path: Path) -> None:
        self._write_php(tmp_path, "src/UserController.php")
        db = self._make_db(tmp_path)
        db.build(tmp_path)
        result = db.get_file_symbols("src/UserController.php")
        db.close()
        assert "UserController" in result
        assert "##" in result  # markdown heading

    def test_get_file_not_indexed(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        result = db.get_file_symbols("nonexistent.php")
        db.close()
        assert "not indexed" in result.lower()

    def test_search_symbols(self, tmp_path: Path) -> None:
        self._write_php(tmp_path, "src/UserController.php")
        db = self._make_db(tmp_path)
        db.build(tmp_path)
        results = db.search_symbols("User")
        db.close()
        assert len(results) > 0
        assert any("User" in r["name"] for r in results)

    def test_get_affected(self, tmp_path: Path) -> None:
        self._write_php(tmp_path, "src/UserController.php")
        self._write_php(tmp_path, "src/OrderController.php", content=b"""<?php
namespace App\\Controllers;
use App\\Controllers\\UserController;
class OrderController extends UserController {}
""")
        db = self._make_db(tmp_path)
        db.build(tmp_path)
        result = db.get_affected(["src/UserController.php"])
        db.close()
        assert "src/UserController.php" in result["direct_files"]
        # OrderController extends UserController, so it should be affected
        assert any("OrderController" in f or "Order" in f for f in result["affected_files"])

    def test_overview_returns_markdown(self, tmp_path: Path) -> None:
        self._write_php(tmp_path, "src/UserController.php")
        db = self._make_db(tmp_path)
        db.build(tmp_path)
        overview = db.get_overview()
        db.close()
        assert "Project Overview" in overview
        assert "Files indexed:" in overview

    def test_skip_vendor(self, tmp_path: Path) -> None:
        self._write_php(tmp_path, "src/UserController.php")
        self._write_php(tmp_path, "vendor/laravel/SomeFile.php")
        db = self._make_db(tmp_path)
        counts = db.build(tmp_path)
        db.close()
        assert counts["total"] == 1  # vendor file skipped
