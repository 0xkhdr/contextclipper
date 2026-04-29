"""Code graph indexer: builds and queries a SQLite-backed symbol/dependency graph."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Literal

from .logging import get_logger

log = get_logger()

SKIP_DIRS = frozenset({"vendor", "node_modules", ".git", ".svn", "__pycache__", ".tox", "dist", "build"})

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS files (
    id       INTEGER PRIMARY KEY,
    path     TEXT    NOT NULL UNIQUE,
    sha256   TEXT    NOT NULL,
    indexed  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id       INTEGER PRIMARY KEY,
    file_id  INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind     TEXT    NOT NULL,  -- class|interface|trait|method|function|property|constant
    name     TEXT    NOT NULL,
    fqn      TEXT    NOT NULL,  -- fully qualified name
    parent   TEXT,              -- parent class/interface FQN (for methods/properties)
    line_start INTEGER,
    line_end   INTEGER,
    signature  TEXT,            -- signature only, no body
    visibility TEXT,            -- public|protected|private
    is_static  INTEGER DEFAULT 0,
    is_abstract INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dependencies (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,  -- extends|implements|use|call|import
    source_fqn TEXT    NOT NULL,
    target_fqn TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_fqn  ON symbols(fqn);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_deps_source  ON dependencies(source_fqn);
CREATE INDEX IF NOT EXISTS idx_deps_target  ON dependencies(target_fqn);
CREATE INDEX IF NOT EXISTS idx_files_path   ON files(path);
"""


@dataclass
class Symbol:
    kind: str
    name: str
    fqn: str
    parent: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    signature: str | None = None
    visibility: str = "public"
    is_static: bool = False
    is_abstract: bool = False
    children: list["Symbol"] = field(default_factory=list)


@dataclass
class FileSummary:
    path: str
    symbols: list[Symbol]
    dependencies: list[tuple[str, str, str]]  # (kind, source_fqn, target_fqn)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_php(source: bytes, file_path: str) -> FileSummary:
    """Parse a PHP file using tree-sitter and extract symbols/deps."""
    try:
        import tree_sitter_php as ts_php
        from tree_sitter import Language, Parser

        # tree-sitter-php 0.23+ exposes language_php() for the PHP grammar
        PHP = Language(ts_php.language_php())
        parser = Parser(PHP)
    except Exception:
        return FileSummary(path=file_path, symbols=[], dependencies=[])

    tree = parser.parse(source)
    symbols: list[Symbol] = []
    deps: list[tuple[str, str, str]] = []
    namespace = ""

    def fqn(name: str) -> str:
        return f"\\{namespace}\\{name}".replace("\\\\", "\\") if namespace else f"\\{name}"

    def node_text(node) -> str:  # type: ignore[no-untyped-def]
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def find_children_by_type(node, *types):  # type: ignore[no-untyped-def]
        return [c for c in node.children if c.type in types]

    def extract_namespace(node) -> str:  # type: ignore[no-untyped-def]
        # namespace_definition has a "name" field
        name_node = node.child_by_field_name("name")
        if name_node:
            return node_text(name_node)
        for child in node.children:
            if child.type in ("namespace_name", "qualified_name"):
                return node_text(child)
        return ""

    def extract_method_signature(node, parent_fqn: str) -> Symbol:  # type: ignore[no-untyped-def]
        name_node = node.child_by_field_name("name")
        name = node_text(name_node) if name_node else "<anonymous>"
        visibility = "public"
        is_static = False
        is_abstract = False
        for mod in find_children_by_type(node, "visibility_modifier", "static_modifier", "abstract_modifier"):
            t = node_text(mod)
            if t in ("public", "protected", "private"):
                visibility = t
            elif t == "static":
                is_static = True
            elif t == "abstract":
                is_abstract = True
        params_node = node.child_by_field_name("parameters")
        params = node_text(params_node) if params_node else "()"
        return_type_node = node.child_by_field_name("return_type")
        return_type = node_text(return_type_node) if return_type_node else ""
        sig = f"{name}{params}{': ' + return_type if return_type else ''}"
        child_fqn = f"{parent_fqn}::{name}"
        return Symbol(
            kind="method",
            name=name,
            fqn=child_fqn,
            parent=parent_fqn,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig,
            visibility=visibility,
            is_static=is_static,
            is_abstract=is_abstract,
        )

    def extract_class_like(node, kind: str) -> Symbol | None:  # type: ignore[no-untyped-def]
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = node_text(name_node)
        class_fqn = fqn(name)

        # `base_clause` is a named child (not a field) on class_declaration
        for child in node.children:
            if child.type == "base_clause":
                for c in child.children:
                    if c.type in ("name", "qualified_name", "namespace_name"):
                        deps.append(("extends", class_fqn, node_text(c)))
            elif child.type == "class_interface_clause":
                # implements FooInterface, BarInterface
                for c in child.children:
                    if c.type in ("name", "qualified_name", "namespace_name"):
                        deps.append(("implements", class_fqn, node_text(c)))
            elif child.type == "base_interface_clause":
                # interface extends AnotherInterface
                for c in child.children:
                    if c.type in ("name", "qualified_name", "namespace_name"):
                        deps.append(("extends", class_fqn, node_text(c)))

        sym = Symbol(
            kind=kind,
            name=name,
            fqn=class_fqn,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
        )

        # body — field name "body" returns the declaration_list
        body = node.child_by_field_name("body")
        if body:
            for member in body.children:
                if member.type == "method_declaration":
                    sym.children.append(extract_method_signature(member, class_fqn))
                elif member.type == "use_declaration":
                    for trait in member.children:
                        if trait.type in ("qualified_name", "name", "namespace_name"):
                            deps.append(("use", class_fqn, node_text(trait)))
        return sym

    def walk(node) -> None:  # type: ignore[no-untyped-def]
        nonlocal namespace
        if node.type == "namespace_definition":
            namespace = extract_namespace(node)
            for child in node.children:
                walk(child)
        elif node.type in ("class_declaration", "abstract_class_declaration"):
            sym = extract_class_like(node, "class")
            if sym:
                symbols.append(sym)
        elif node.type == "interface_declaration":
            sym = extract_class_like(node, "interface")
            if sym:
                symbols.append(sym)
        elif node.type == "trait_declaration":
            sym = extract_class_like(node, "trait")
            if sym:
                symbols.append(sym)
        elif node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = node_text(name_node)
                symbols.append(Symbol(
                    kind="function",
                    name=name,
                    fqn=fqn(name),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))
        elif node.type == "use_declaration":
            for child in node.children:
                if child.type in ("qualified_name", "name"):
                    deps.append(("import", "", node_text(child)))
        else:
            for child in node.children:
                walk(child)

    walk(tree.root_node)
    return FileSummary(path=file_path, symbols=symbols, dependencies=deps)


class GraphDB:
    """Persistent code graph backed by SQLite with WAL mode."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._write_lock = threading.RLock()

    def validate(self) -> dict[str, Any]:
        """Health-check: schema present, integrity check passes, db reachable."""
        problems: list[str] = []
        try:
            cur = self._conn.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            if not row or row[0] != "ok":
                problems.append(f"integrity_check: {row}")
        except sqlite3.Error as e:
            problems.append(f"integrity_check failed: {e}")
        try:
            cur = self._conn.execute("SELECT COUNT(*) FROM files")
            files = cur.fetchone()[0]
        except sqlite3.Error as e:
            problems.append(f"files table missing: {e}")
            files = 0
        return {"ok": not problems, "files_indexed": files, "problems": problems}

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Cursor, None, None]:
        with self._write_lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def build(self, project_root: Path, force: bool = False) -> dict[str, int]:
        """Walk project_root and index all PHP files. Returns counts."""
        t0 = time.monotonic()
        counts = {"new": 0, "updated": 0, "skipped": 0, "total": 0}
        php_files = list(self._walk_php(project_root))
        counts["total"] = len(php_files)
        for path in php_files:
            rel = str(path.relative_to(project_root))
            sha = _sha256(path)
            with self._tx() as cur:
                cur.execute("SELECT id, sha256 FROM files WHERE path = ?", (rel,))
                row = cur.fetchone()
                if row and row[1] == sha and not force:
                    counts["skipped"] += 1
                    continue
                source = path.read_bytes()
                summary = _parse_php(source, rel)
                if row:
                    file_id = row[0]
                    cur.execute("UPDATE files SET sha256=?, indexed=? WHERE id=?", (sha, time.time(), file_id))
                    cur.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
                    cur.execute("DELETE FROM dependencies WHERE file_id=?", (file_id,))
                    counts["updated"] += 1
                else:
                    cur.execute("INSERT INTO files(path, sha256, indexed) VALUES(?,?,?)", (rel, sha, time.time()))
                    file_id = cur.lastrowid
                    counts["new"] += 1
                self._insert_file_data(cur, file_id, summary)
        counts["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
        return counts

    def _insert_file_data(self, cur: sqlite3.Cursor, file_id: int, summary: FileSummary) -> None:
        def insert_sym(sym: Symbol) -> None:
            cur.execute(
                "INSERT INTO symbols(file_id,kind,name,fqn,parent,line_start,line_end,signature,visibility,is_static,is_abstract) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (file_id, sym.kind, sym.name, sym.fqn, sym.parent, sym.line_start, sym.line_end,
                 sym.signature, sym.visibility, int(sym.is_static), int(sym.is_abstract)),
            )
            for child in sym.children:
                insert_sym(child)

        for sym in summary.symbols:
            insert_sym(sym)
        for kind, src, tgt in summary.dependencies:
            cur.execute(
                "INSERT INTO dependencies(file_id, kind, source_fqn, target_fqn) VALUES(?,?,?,?)",
                (file_id, kind, src, tgt),
            )

    def _walk_php(self, root: Path) -> Generator[Path, None, None]:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                if fname.endswith(".php"):
                    yield Path(dirpath) / fname

    def get_file_symbols(
        self,
        path: str,
        mode: Literal["summary_only", "full", "smart"] = "summary_only",
    ) -> str:
        """Return Markdown description of a file's symbols."""
        with self._tx() as cur:
            cur.execute("SELECT id, sha256 FROM files WHERE path = ?", (path,))
            row = cur.fetchone()
            if not row:
                return f"File `{path}` not indexed. Run `ctxclp build` first."
            file_id = row[0]
            cur.execute(
                "SELECT kind, name, fqn, parent, line_start, line_end, signature, visibility, is_static, is_abstract "
                "FROM symbols WHERE file_id=? ORDER BY line_start",
                (file_id,),
            )
            syms = cur.fetchall()
            cur.execute(
                "SELECT kind, source_fqn, target_fqn FROM dependencies WHERE file_id=?",
                (file_id,),
            )
            deps = cur.fetchall()
        return self._format_file_symbols(path, syms, deps, mode)

    def _format_file_symbols(
        self,
        path: str,
        syms: list,
        deps: list,
        mode: str,
    ) -> str:
        lines = [f"## `{path}`"]
        top_level = [s for s in syms if s[3] is None or "::" not in s[2]]
        for s in top_level:
            kind, name, fqn, parent, ls, le, sig, vis, is_static, is_abstract = s
            if kind in ("class", "interface", "trait"):
                prefix = "abstract " if is_abstract else ""
                lines.append(f"\n### {prefix}{kind} `{name}` (line {ls}–{le})")
                # children
                children = [c for c in syms if c[3] == fqn]
                for c in children:
                    ck, cn, cfqn, cp, cls, cle, csig, cvis, cis_static, _ = c
                    static_kw = "static " if cis_static else ""
                    if mode == "summary_only":
                        lines.append(f"  - `{cvis} {static_kw}{csig or cn}` (line {cls})")
                    else:
                        lines.append(f"  - [{cvis} {static_kw}{ck}] `{csig or cn}` lines {cls}–{cle}")
            elif kind == "function":
                lines.append(f"\n### function `{name}` (line {ls}–{le})")
        if deps:
            lines.append("\n**Dependencies:**")
            for dk, src, tgt in deps:
                lines.append(f"  - {dk}: `{tgt}`")
        return "\n".join(lines)

    def search_symbols(self, query: str, kind: str | None = None) -> list[dict]:
        with self._tx() as cur:
            if kind:
                cur.execute(
                    "SELECT f.path, s.kind, s.name, s.fqn, s.line_start FROM symbols s "
                    "JOIN files f ON f.id=s.file_id WHERE s.kind=? AND (s.name LIKE ? OR s.fqn LIKE ?) LIMIT 50",
                    (kind, f"%{query}%", f"%{query}%"),
                )
            else:
                cur.execute(
                    "SELECT f.path, s.kind, s.name, s.fqn, s.line_start FROM symbols s "
                    "JOIN files f ON f.id=s.file_id WHERE s.name LIKE ? OR s.fqn LIKE ? LIMIT 50",
                    (f"%{query}%", f"%{query}%"),
                )
            return [{"path": r[0], "kind": r[1], "name": r[2], "fqn": r[3], "line": r[4]} for r in cur.fetchall()]

    def get_affected(self, files: list[str]) -> dict[str, list[str]]:
        """Return files/symbols that depend on the given set of files."""
        affected_files: set[str] = set()
        with self._tx() as cur:
            for f in files:
                cur.execute("SELECT id FROM files WHERE path=?", (f,))
                row = cur.fetchone()
                if not row:
                    continue
                file_id = row[0]
                # Get both short name and FQN for matching — deps may store either
                cur.execute(
                    "SELECT name, fqn FROM symbols WHERE file_id=? AND kind IN ('class','interface','trait')",
                    (file_id,),
                )
                name_fqn_pairs = cur.fetchall()
                for name, fqn in name_fqn_pairs:
                    cur.execute(
                        "SELECT DISTINCT f.path FROM dependencies d JOIN files f ON f.id=d.file_id "
                        "WHERE d.target_fqn=? OR d.target_fqn=?",
                        (fqn, name),
                    )
                    for r in cur.fetchall():
                        if r[0] not in files:
                            affected_files.add(r[0])
        return {"direct_files": files, "affected_files": sorted(affected_files)}

    def get_overview(self, detail: Literal["compact", "full"] = "compact") -> str:
        """Return a compact Markdown project tree with symbol summary."""
        with self._tx() as cur:
            cur.execute("SELECT COUNT(*) FROM files")
            file_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM symbols WHERE kind='class'")
            class_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM symbols WHERE kind='method'")
            method_count = cur.fetchone()[0]
            if detail == "compact":
                cur.execute(
                    "SELECT f.path, s.name, s.kind FROM files f "
                    "JOIN symbols s ON s.file_id=f.id AND s.kind IN ('class','interface','trait') "
                    "ORDER BY f.path LIMIT 200"
                )
                rows = cur.fetchall()
        lines = [
            "# Project Overview",
            f"Files indexed: {file_count} | Classes: {class_count} | Methods: {method_count}",
            "",
        ]
        if detail == "compact":
            cur_file = None
            for path, sname, skind in rows:
                if path != cur_file:
                    lines.append(f"\n**{path}**")
                    cur_file = path
                lines.append(f"  - {skind} `{sname}`")
        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()
