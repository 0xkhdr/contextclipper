"""Code graph indexer: builds and queries a SQLite-backed symbol/dependency graph.

Supported languages (auto-detected by file extension):
  - PHP (.php)        — classes, interfaces, traits, methods, functions
  - Python (.py)      — classes, functions, methods, imports
  - TypeScript (.ts, .tsx) — classes, interfaces, functions, arrow functions, imports
  - JavaScript (.js, .jsx) — classes, functions, arrow functions

Each language parser uses tree-sitter for accurate, syntax-aware extraction.
Parsers are loaded lazily; missing grammars fall back to a regex-based extractor
so the graph continues to work even when optional tree-sitter packages are absent.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Literal

from .logging import get_logger

log = get_logger()

SKIP_DIRS = frozenset({
    "vendor", "node_modules", ".git", ".svn", "__pycache__",
    ".tox", "dist", "build", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "coverage", "target",
})

# Extension → language name
_LANGUAGE_MAP: dict[str, str] = {
    ".php": "php",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS files (
    id       INTEGER PRIMARY KEY,
    path     TEXT    NOT NULL UNIQUE,
    sha256   TEXT    NOT NULL,
    indexed  REAL    NOT NULL,
    language TEXT    NOT NULL DEFAULT 'unknown'
);

CREATE TABLE IF NOT EXISTS symbols (
    id       INTEGER PRIMARY KEY,
    file_id  INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind     TEXT    NOT NULL,
    name     TEXT    NOT NULL,
    fqn      TEXT    NOT NULL,
    parent   TEXT,
    line_start INTEGER,
    line_end   INTEGER,
    signature  TEXT,
    visibility TEXT,
    is_static  INTEGER DEFAULT 0,
    is_abstract INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dependencies (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,
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

_MIGRATIONS = [
    "ALTER TABLE files ADD COLUMN language TEXT NOT NULL DEFAULT 'unknown'",
]


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
    language: str
    symbols: list[Symbol]
    dependencies: list[tuple[str, str, str]]  # (kind, source_fqn, target_fqn)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── PHP parser ────────────────────────────────────────────────────────────────

def _parse_php(source: bytes, file_path: str) -> FileSummary:
    """Parse a PHP file using tree-sitter and extract symbols/deps."""
    try:
        import tree_sitter_php as ts_php
        from tree_sitter import Language, Parser

        PHP = Language(ts_php.language_php())
        parser = Parser(PHP)
    except Exception:
        return FileSummary(path=file_path, language="php", symbols=[], dependencies=[])

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
        return Symbol(
            kind="method",
            name=name,
            fqn=f"{parent_fqn}::{name}",
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
        for child in node.children:
            if child.type == "base_clause":
                for c in child.children:
                    if c.type in ("name", "qualified_name", "namespace_name"):
                        deps.append(("extends", class_fqn, node_text(c)))
            elif child.type == "class_interface_clause":
                for c in child.children:
                    if c.type in ("name", "qualified_name", "namespace_name"):
                        deps.append(("implements", class_fqn, node_text(c)))
            elif child.type == "base_interface_clause":
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
    return FileSummary(path=file_path, language="php", symbols=symbols, dependencies=deps)


# ── Python parser ─────────────────────────────────────────────────────────────

def _parse_python(source: bytes, file_path: str) -> FileSummary:
    """Parse a Python file using tree-sitter and extract symbols/deps."""
    try:
        import tree_sitter_python as ts_python
        from tree_sitter import Language, Parser

        PY = Language(ts_python.language())
        parser = Parser(PY)
    except Exception:
        return _parse_python_regex(source, file_path)

    tree = parser.parse(source)
    symbols: list[Symbol] = []
    deps: list[tuple[str, str, str]] = []

    def node_text(node) -> str:  # type: ignore[no-untyped-def]
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def extract_params(node) -> str:  # type: ignore[no-untyped-def]
        params_node = node.child_by_field_name("parameters")
        return node_text(params_node) if params_node else "()"

    def extract_return_type(node) -> str:  # type: ignore[no-untyped-def]
        rt = node.child_by_field_name("return_type")
        return (": " + node_text(rt)) if rt else ""

    def walk(node, parent_fqn: str = "") -> None:  # type: ignore[no-untyped-def]
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            name = node_text(name_node)
            fqn = f"{parent_fqn}.{name}" if parent_fqn else name
            # Extract superclasses
            args = node.child_by_field_name("superclasses")
            if args:
                for arg in args.children:
                    if arg.type in ("identifier", "attribute"):
                        deps.append(("extends", fqn, node_text(arg)))
            sym = Symbol(
                kind="class",
                name=name,
                fqn=fqn,
                parent=parent_fqn or None,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            )
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "function_definition":
                        mname_node = child.child_by_field_name("name")
                        if mname_node:
                            mname = node_text(mname_node)
                            params = extract_params(child)
                            rt = extract_return_type(child)
                            visibility = "private" if mname.startswith("__") and not mname.endswith("__") else "public"
                            sym.children.append(Symbol(
                                kind="method",
                                name=mname,
                                fqn=f"{fqn}.{mname}",
                                parent=fqn,
                                line_start=child.start_point[0] + 1,
                                line_end=child.end_point[0] + 1,
                                signature=f"{mname}{params}{rt}",
                                visibility=visibility,
                            ))
            symbols.append(sym)

        elif node.type == "function_definition" and not parent_fqn:
            # Top-level function
            name_node = node.child_by_field_name("name")
            if name_node:
                name = node_text(name_node)
                params = extract_params(node)
                rt = extract_return_type(node)
                symbols.append(Symbol(
                    kind="function",
                    name=name,
                    fqn=name,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    signature=f"{name}{params}{rt}",
                ))

        elif node.type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    deps.append(("import", "", node_text(child)))

        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            if module_node:
                deps.append(("import", "", node_text(module_node)))

        else:
            for child in node.children:
                walk(child, parent_fqn)

    for child in tree.root_node.children:
        walk(child)

    return FileSummary(path=file_path, language="python", symbols=symbols, dependencies=deps)


def _parse_python_regex(source: bytes, file_path: str) -> FileSummary:
    """Regex-based Python fallback when tree-sitter-python is not available."""
    text = source.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []
    deps: list[tuple[str, str, str]] = []

    class_re = re.compile(r"^class\s+(\w+)(?:\((.*?)\))?:", re.MULTILINE)
    func_re = re.compile(r"^def\s+(\w+)\s*(\([^)]*\))\s*(?:->.*?)?:", re.MULTILINE)
    import_re = re.compile(r"^(?:import|from)\s+([\w.]+)", re.MULTILINE)

    lines = text.splitlines()
    line_starts = [0]
    for ln in lines:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    def byte_to_line(pos: int) -> int:
        for i, start in enumerate(line_starts):
            if start > pos:
                return i
        return len(lines)

    for m in class_re.finditer(text):
        name = m.group(1)
        sym = Symbol(kind="class", name=name, fqn=name, line_start=byte_to_line(m.start()))
        if m.group(2):
            for base in m.group(2).split(","):
                b = base.strip()
                if b:
                    deps.append(("extends", name, b))
        symbols.append(sym)

    for m in func_re.finditer(text):
        name, params = m.group(1), m.group(2)
        symbols.append(Symbol(
            kind="function", name=name, fqn=name,
            line_start=byte_to_line(m.start()),
            signature=f"{name}{params}",
        ))

    for m in import_re.finditer(text):
        deps.append(("import", "", m.group(1)))

    return FileSummary(path=file_path, language="python", symbols=symbols, dependencies=deps)


# ── TypeScript / JavaScript parser ────────────────────────────────────────────

def _parse_typescript(source: bytes, file_path: str, language: str = "typescript") -> FileSummary:
    """Parse a TypeScript/JavaScript file using tree-sitter and extract symbols/deps."""
    try:
        if language == "typescript":
            import tree_sitter_typescript as ts_ts
            from tree_sitter import Language, Parser
            LANG = Language(ts_ts.language_typescript())
        else:
            import tree_sitter_javascript as ts_js  # type: ignore[import-not-found]
            from tree_sitter import Language, Parser
            LANG = Language(ts_js.language())
        parser = Parser(LANG)
    except Exception:
        return _parse_typescript_regex(source, file_path, language)

    tree = parser.parse(source)
    symbols: list[Symbol] = []
    deps: list[tuple[str, str, str]] = []

    def node_text(node) -> str:  # type: ignore[no-untyped-def]
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def walk(node, parent_fqn: str = "") -> None:  # type: ignore[no-untyped-def]
        if node.type in ("class_declaration", "abstract_class_declaration", "class"):
            name_node = node.child_by_field_name("name")
            name = node_text(name_node) if name_node else "<anonymous>"
            fqn = f"{parent_fqn}.{name}" if parent_fqn else name
            # Heritage (extends / implements)
            heritage = node.child_by_field_name("class_heritage")
            if heritage:
                for h in heritage.children:
                    if h.type in ("extends_clause", "implements_clause"):
                        for c in h.children:
                            if c.type == "type_identifier":
                                kind = "extends" if "extends" in node_text(h)[:10] else "implements"
                                deps.append((kind, fqn, node_text(c)))
            sym = Symbol(
                kind="class",
                name=name,
                fqn=fqn,
                parent=parent_fqn or None,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            )
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type in ("method_definition", "public_field_definition"):
                        mname_node = child.child_by_field_name("name")
                        if mname_node:
                            mname = node_text(mname_node)
                            params_node = child.child_by_field_name("parameters")
                            params = node_text(params_node) if params_node else "()"
                            sym.children.append(Symbol(
                                kind="method",
                                name=mname,
                                fqn=f"{fqn}.{mname}",
                                parent=fqn,
                                line_start=child.start_point[0] + 1,
                                line_end=child.end_point[0] + 1,
                                signature=f"{mname}{params}",
                            ))
            symbols.append(sym)

        elif node.type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = node_text(name_node)
                symbols.append(Symbol(
                    kind="interface",
                    name=name,
                    fqn=name,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))

        elif node.type in ("function_declaration", "function") and not parent_fqn:
            name_node = node.child_by_field_name("name")
            if name_node:
                name = node_text(name_node)
                params_node = node.child_by_field_name("parameters")
                params = node_text(params_node) if params_node else "()"
                symbols.append(Symbol(
                    kind="function",
                    name=name,
                    fqn=name,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    signature=f"{name}{params}",
                ))

        elif node.type == "import_statement":
            src_node = node.child_by_field_name("source")
            if src_node:
                deps.append(("import", "", node_text(src_node).strip("'\"")))

        elif node.type == "export_statement":
            for child in node.children:
                walk(child, parent_fqn)

        else:
            for child in node.children:
                walk(child, parent_fqn)

    for child in tree.root_node.children:
        walk(child)

    return FileSummary(path=file_path, language=language, symbols=symbols, dependencies=deps)


def _parse_typescript_regex(source: bytes, file_path: str, language: str) -> FileSummary:
    """Regex-based TypeScript/JavaScript fallback."""
    text = source.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []
    deps: list[tuple[str, str, str]] = []

    class_re = re.compile(r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE)
    iface_re = re.compile(r"(?:export\s+)?interface\s+(\w+)", re.MULTILINE)
    func_re = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(\([^)]*\))", re.MULTILINE)
    import_re = re.compile(r"""(?:import|from)\s+['"]([^'"]+)['"]""", re.MULTILINE)

    lines = text.splitlines()

    def byte_to_line(pos: int) -> int:
        return text[:pos].count("\n") + 1

    for m in class_re.finditer(text):
        symbols.append(Symbol(kind="class", name=m.group(1), fqn=m.group(1),
                              line_start=byte_to_line(m.start())))
    for m in iface_re.finditer(text):
        symbols.append(Symbol(kind="interface", name=m.group(1), fqn=m.group(1),
                              line_start=byte_to_line(m.start())))
    for m in func_re.finditer(text):
        name, params = m.group(1), m.group(2)
        symbols.append(Symbol(kind="function", name=name, fqn=name,
                              line_start=byte_to_line(m.start()),
                              signature=f"{name}{params}"))
    for m in import_re.finditer(text):
        deps.append(("import", "", m.group(1)))

    return FileSummary(path=file_path, language=language, symbols=symbols, dependencies=deps)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _parse_file(source: bytes, file_path: str, language: str) -> FileSummary:
    """Dispatch to the right parser based on language."""
    if language == "php":
        return _parse_php(source, file_path)
    elif language == "python":
        return _parse_python(source, file_path)
    elif language in ("typescript", "javascript"):
        return _parse_typescript(source, file_path, language)
    return FileSummary(path=file_path, language=language, symbols=[], dependencies=[])


# ── GraphDB ───────────────────────────────────────────────────────────────────

class GraphDB:
    """Persistent multi-language code graph backed by SQLite with WAL mode."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
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
        """Walk project_root and index all supported source files. Returns counts."""
        t0 = time.monotonic()
        counts: dict[str, int] = {"new": 0, "updated": 0, "skipped": 0, "total": 0}
        source_files = list(self._walk_sources(project_root))
        counts["total"] = len(source_files)
        for path, language in source_files:
            rel = str(path.relative_to(project_root))
            sha = _sha256(path)
            with self._tx() as cur:
                cur.execute("SELECT id, sha256 FROM files WHERE path = ?", (rel,))
                row = cur.fetchone()
                if row and row[1] == sha and not force:
                    counts["skipped"] += 1
                    continue
                source = path.read_bytes()
                summary = _parse_file(source, rel, language)
                if row:
                    file_id = row[0]
                    cur.execute(
                        "UPDATE files SET sha256=?, indexed=?, language=? WHERE id=?",
                        (sha, time.time(), language, file_id),
                    )
                    cur.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
                    cur.execute("DELETE FROM dependencies WHERE file_id=?", (file_id,))
                    counts["updated"] += 1
                else:
                    cur.execute(
                        "INSERT INTO files(path, sha256, indexed, language) VALUES(?,?,?,?)",
                        (rel, sha, time.time(), language),
                    )
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

    def _walk_sources(self, root: Path) -> Generator[tuple[Path, str], None, None]:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                lang = _LANGUAGE_MAP.get(ext)
                if lang:
                    yield Path(dirpath) / fname, lang

    def get_file_symbols(
        self,
        path: str,
        mode: Literal["summary_only", "full", "smart"] = "summary_only",
    ) -> str:
        """Return Markdown description of a file's symbols."""
        with self._tx() as cur:
            cur.execute("SELECT id, sha256, language FROM files WHERE path = ?", (path,))
            row = cur.fetchone()
            if not row:
                return f"File `{path}` not indexed. Run `ctxclp build` first."
            file_id, _, lang = row
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
        return self._format_file_symbols(path, lang, syms, deps, mode)

    def _format_file_symbols(
        self,
        path: str,
        language: str,
        syms: list,
        deps: list,
        mode: str,
    ) -> str:
        lines = [f"## `{path}` ({language})"]
        top_level = [s for s in syms if s[3] is None or ("::" not in s[2] and "." not in s[2].split(s[1])[0])]
        for s in top_level:
            kind, name, fqn, parent, ls, le, sig, vis, is_static, is_abstract = s
            if kind in ("class", "interface", "trait"):
                prefix = "abstract " if is_abstract else ""
                lines.append(f"\n### {prefix}{kind} `{name}` (line {ls}–{le})")
                children = [c for c in syms if c[3] == fqn]
                for c in children:
                    ck, cn, cfqn, cp, cls, cle, csig, cvis, cis_static, _ = c
                    static_kw = "static " if cis_static else ""
                    vis_prefix = f"{cvis} " if cvis != "public" else ""
                    if mode == "summary_only":
                        lines.append(f"  - `{vis_prefix}{static_kw}{csig or cn}` (line {cls})")
                    else:
                        lines.append(f"  - [{vis_prefix}{static_kw}{ck}] `{csig or cn}` lines {cls}–{cle}")
            elif kind == "function":
                lines.append(f"\n### function `{sig or name}` (line {ls}–{le})")
        if deps:
            lines.append("\n**Dependencies:**")
            for dk, src, tgt in deps[:20]:
                lines.append(f"  - {dk}: `{tgt}`")
            if len(deps) > 20:
                lines.append(f"  - … {len(deps) - 20} more")
        return "\n".join(lines)

    def search_symbols(self, query: str, kind: str | None = None) -> list[dict]:
        with self._tx() as cur:
            if kind:
                cur.execute(
                    "SELECT f.path, f.language, s.kind, s.name, s.fqn, s.line_start FROM symbols s "
                    "JOIN files f ON f.id=s.file_id WHERE s.kind=? AND (s.name LIKE ? OR s.fqn LIKE ?) LIMIT 50",
                    (kind, f"%{query}%", f"%{query}%"),
                )
            else:
                cur.execute(
                    "SELECT f.path, f.language, s.kind, s.name, s.fqn, s.line_start FROM symbols s "
                    "JOIN files f ON f.id=s.file_id WHERE s.name LIKE ? OR s.fqn LIKE ? LIMIT 50",
                    (f"%{query}%", f"%{query}%"),
                )
            return [
                {"path": r[0], "language": r[1], "kind": r[2], "name": r[3], "fqn": r[4], "line": r[5]}
                for r in cur.fetchall()
            ]

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
            cur.execute("SELECT COUNT(*) FROM symbols WHERE kind IN ('method', 'function')")
            method_count = cur.fetchone()[0]
            # Language breakdown
            cur.execute("SELECT language, COUNT(*) FROM files GROUP BY language ORDER BY COUNT(*) DESC")
            lang_counts = cur.fetchall()
            if detail == "compact":
                cur.execute(
                    "SELECT f.path, f.language, s.name, s.kind FROM files f "
                    "JOIN symbols s ON s.file_id=f.id AND s.kind IN ('class','interface','trait','function') "
                    "ORDER BY f.path LIMIT 200"
                )
                rows = cur.fetchall()

        lang_summary = ", ".join(f"{lang}: {n}" for lang, n in lang_counts)
        lines = [
            "# Project Overview",
            f"Files: {file_count} ({lang_summary}) | Classes: {class_count} | Methods/Functions: {method_count}",
            "",
        ]
        if detail == "compact":
            cur_file: str | None = None
            for path, lang, sname, skind in rows:
                if path != cur_file:
                    lines.append(f"\n**{path}** ({lang})")
                    cur_file = path
                lines.append(f"  - {skind} `{sname}`")
        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()
