"""Path-traversal regression tests for the MCP get_file tool."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from contextclipper.engine.graph import GraphDB  # type: ignore[import-not-found]
from contextclipper.mcp.tools import tool_get_file  # type: ignore[import-not-found]


def test_traversal_in_relative_path_rejected(tmp_path: Path) -> None:
    db = GraphDB(tmp_path / "g.db")
    out = tool_get_file(db, "../../../etc/passwd", project_root=str(tmp_path))
    db.close()
    assert "invalid" in out.lower() or "outside" in out.lower()


def test_absolute_path_outside_root_rejected(tmp_path: Path) -> None:
    db = GraphDB(tmp_path / "g.db")
    out = tool_get_file(db, "/etc/passwd", project_root=str(tmp_path))
    db.close()
    assert "outside" in out.lower() or "invalid" in out.lower()


def test_normal_relative_path_works(tmp_path: Path) -> None:
    db = GraphDB(tmp_path / "g.db")
    out = tool_get_file(db, "src/X.php", project_root=str(tmp_path))
    db.close()
    # File not indexed → benign error message, but no traversal complaint
    assert "not indexed" in out.lower() or "##" in out
