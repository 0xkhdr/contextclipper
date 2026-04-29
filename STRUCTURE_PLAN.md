# ContextClipper Modular Architecture Plan

## 1. Package Restructuring

### 1.1 Target Structure

```
contextclipper/
├── src/
│   └── contextclipper/
│       ├── __init__.py                    # Package version, public API re-exports
│       ├── _detect.py                     # Subsystem availability detection
│       │
│       ├── core/                          # Always installed, zero heavy deps
│       │   ├── __init__.py
│       │   ├── config.py                  # TOML config loading, paths, env vars
│       │   ├── redact.py                  # Secret redaction (moved from engine/)
│       │   ├── stats.py                   # Local analytics DB (moved from engine/)
│       │   ├── tee.py                     # Raw output persistence (moved from engine/)
│       │   ├── types.py                   # Shared dataclasses (CompressionResult, etc.)
│       │   └── exceptions.py             # Unified exception hierarchy
│       │
│       ├── shell/                         # Filter engine subsystem
│       │   ├── __init__.py
│       │   ├── engine.py                  # Core compression pipeline (was filters.py)
│       │   ├── filters/                   # Built-in TOML filter definitions
│       │   │   ├── git/
│       │   │   │   └── git.toml
│       │   │   ├── python/
│       │   │   │   ├── pytest.toml
│       │   │   │   ├── ruff.toml
│       │   │   │   └── ...
│       │   │   └── ...                    # Existing filter directories
│       │   ├── loader.py                  # TOML filter discovery & validation
│       │   ├── matchers.py               # Regex matching utilities
│       │   ├── rules.py                   # Individual rule type implementations
│       │   ├── strategies.py             # Custom Python strategy registration
│       │   └── streaming.py              # Streaming mode processing
│       │
│       ├── graph/                         # Code intelligence subsystem
│       │   ├── __init__.py
│       │   ├── builder.py                 # Graph construction (was graph.py)
│       │   ├── query.py                   # Symbol search, dependency queries
│       │   ├── parser.py                  # Tree-sitter + regex fallback parser
│       │   ├── schema.sql                 # SQLite schema (migrations-ready)
│       │   ├── languages.py              # Supported language registry
│       │   └── indexer.py                # File-watching, incremental updates
│       │
│       ├── mcp/                           # MCP server subsystem (requires shell+graph)
│       │   ├── __init__.py
│       │   ├── server.py                  # MCP stdio server setup
│       │   ├── tools/
│       │   │   ├── __init__.py
│       │   │   ├── shell_tools.py         # run_shell, get_raw_output
│       │   │   ├── graph_tools.py         # get_file, search_symbols, get_affected
│       │   │   ├── admin_tools.py         # rebuild_graph, get_stats, get_overview
│       │   │   └── context_tools.py       # NEW: enhanced context tools
│       │   ├── resources/
│       │   │   ├── __init__.py
│       │   │   ├── workspace_hotspots.py  # NEW: workspace heatmap resource
│       │   │   ├── filter_catalog.py      # Available filters listing
│       │   │   └── session_context.py     # NEW: session-long memory resource
│       │   └── middleware/
│       │       ├── __init__.py
│       │       ├── context_injection.py   # NEW: implicit dependency hints
│       │       └── session_cache.py       # NEW: cross-tool-call cache
│       │
│       ├── cli/                           # Unified CLI layer
│       │   ├── __init__.py
│       │   ├── main.py                    # Click app with subsystem-aware commands
│       │   ├── shell_cmd.py               # run, filter subcommands
│       │   ├── graph_cmd.py               # build, query subcommands
│       │   ├── mcp_cmd.py                 # serve subcommand
│       │   ├── admin_cmd.py               # stats, audit, doctor, validate
│       │   └── install_cmd.py             # Agent detection & hook injection
│       │
│       └── hooks/                         # Shell scripts for agent injection
│           ├── claude_code_pretooluse.sh
│           ├── cursor_pretooluse.sh
│           └── common.sh                  # Shared hook utilities
│
├── tests/
│   ├── unit/
│   │   ├── core/                          # Redaction, tee, stats tests
│   │   ├── shell/                         # Filter engine unit tests
│   │   ├── graph/                         # Graph builder, query tests
│   │   └── mcp/                           # Tool/resource unit tests
│   ├── integration/
│   │   ├── test_shell_with_graph.py       # Context-aware compression tests
│   │   ├── test_mcp_full.py              # End-to-end MCP server tests
│   │   └── test_agent_flow.py            # Realistic agent interaction flows
│   └── fixtures/
│       ├── sample_repos/                  # Small test repos in various languages
│       └── command_outputs/               # Sample shell outputs with expected results
│
├── docs/
│   ├── architecture.md                    # THIS DOCUMENT
│   ├── filter-authoring.md               # How to write custom TOML filters
│   ├── graph-querying.md                 # Code graph query patterns for agents
│   ├── agent-integration.md              # Agent developer integration guide
│   └── contributing.md                   # Development setup, conventions
│
├── pyproject.toml                         # Updated with optional dependencies
├── README.md
└── SPEC.md
```

### 1.2 Dependency Matrix

```toml
[project]
name = "contextclipper"
# ... existing metadata ...

[project.optional-dependencies]
# Shell subsystem: only toml parsing + regex
shell = [
    "tomli>=2.0; python_version < '3.11'",
    "click>=8.0",
]

# Graph subsystem: tree-sitter + language grammars
graph = [
    "tree-sitter>=0.21.0",
    "tree-sitter-python>=0.21.0",
    "tree-sitter-javascript>=0.21.0",
    "tree-sitter-typescript>=0.21.0",
    "tree-sitter-rust>=0.21.0",
    "tree-sitter-go>=0.21.0",
    "tree-sitter-java>=0.21.0",
    "click>=8.0",
]

# MCP subsystem: requires fastmcp + both above
mcp = [
    "contextclipper[shell,graph]",
    "mcp>=1.0.0",
    "click>=8.0",
]

# Full installation: everything
full = [
    "contextclipper[shell,graph,mcp]",
]

# Development extras
dev = [
    "contextclipper[full]",
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.10",
]
```

### 1.3 Subsystem Detection (`_detect.py`)

```python
"""Subsystem availability detection with graceful degradation."""

from __future__ import annotations

import functools
from typing import Protocol


class SubsystemAvailability:
    """Detects which subsystems are installed."""

    @functools.cached_property
    def has_shell(self) -> bool:
        try:
            import contextclipper.shell  # noqa: F401
            return True
        except ImportError:
            return False

    @functools.cached_property
    def has_graph(self) -> bool:
        try:
            import contextclipper.graph  # noqa: F401
            return True
        except ImportError:
            return False

    @functools.cached_property
    def has_mcp(self) -> bool:
        try:
            import contextclipper.mcp  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def can_serve(self) -> bool:
        """Full MCP server requires both shell and graph."""
        return self.has_shell and self.has_graph and self.has_mcp


# Singleton
availability = SubsystemAvailability()
```

---

## 2. Internal API Boundaries

### 2.1 Core → Shell/Graph (Public Interfaces)

```python
# contextclipper/core/types.py

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompressionResult:
    """Returned by the compression pipeline."""
    compressed: str
    original_bytes: int
    compressed_bytes: int
    savings_ratio: float
    raw_id: Optional[str] = None       # Tee store ID
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SymbolSummary:
    """Lightweight symbol representation for agents."""
    name: str
    kind: str                          # "class", "method", "function"
    file_path: str
    line_start: int
    line_end: int
    signature: Optional[str] = None
    docstring: Optional[str] = None
    visibility: str = "public"         # public, private, protected
```

### 2.2 Shell Public API

```python
# contextclipper/shell/__init__.py

from contextclipper.core.types import CompressionResult
from contextclipper.shell.engine import FilterEngine
from contextclipper.shell.strategies import register_strategy

# Public factory
def create_engine(*, config_path: str | None = None) -> FilterEngine:
    """Create a configured filter engine."""
    ...

# Convenience function
def compress_command(
    command: str,
    output: str,
    exit_code: int,
    *,
    graph_context: dict | None = None,  # NEW: context-aware compression
) -> CompressionResult:
    """Compress shell command output. One-shot, no engine management."""
    ...
```

### 2.3 Graph Public API

```python
# contextclipper/graph/__init__.py

from contextclipper.core.types import SymbolSummary
from contextclipper.graph.query import GraphQuery


def build_graph(project_root: str, *, incremental: bool = True) -> GraphQuery:
    """Build or update the code graph index. Returns a query interface."""
    ...


class GraphQuery:
    """Stateless query interface over the indexed graph."""

    def search_symbols(self, query: str, *, limit: int = 20) -> list[SymbolSummary]:
        """Fuzzy-search symbols by name."""
        ...

    def get_file_symbols(self, file_path: str) -> list[SymbolSummary]:
        """Get all top-level symbols in a file."""
        ...

    def get_affected(self, changed_files: list[str]) -> list[str]:
        """Find files that depend on the given changed files."""
        ...

    def get_dependents(self, symbol_name: str) -> list[str]:
        """Find all files that reference a specific symbol."""
        ...

    # NEW: Enhanced queries
    def get_hotspots(self, *, limit: int = 10) -> list[SymbolSummary]:
        """Return most-frequently-accessed symbols."""
        ...

    def get_working_set(self, file_paths: list[str]) -> list[SymbolSummary]:
        """Build a minimal symbol map covering the given files."""
        ...

    def find_related(self, symbol_name: str, depth: int = 1) -> list[SymbolSummary]:
        """Find symbols related by import/call graph (BFS up to depth)."""
        ...
```

---

## 3. Enhanced Features Architecture

### 3.1 Context-Aware Compression

```python
# contextclipper/mcp/middleware/context_injection.py

"""
Context injection middleware bridges shell and graph subsystems.

When an agent runs a shell command, we:
1. Check what files the agent has recently accessed (session cache)
2. Query the code graph for symbols in those files
3. Use that symbol knowledge to boost relevance in compression
4. Append machine-parseable hints about affected dependencies
"""

from __future__ import annotations

from dataclasses import dataclass, field
from contextclipper.core.types import CompressionResult


@dataclass
class ContextHint:
    """Machine-parseable hint appended to compressed output."""
    affected_files: list[str] = field(default_factory=list)
    relevant_symbols: list[str] = field(default_factory=list)
    suggested_files: list[str] = field(default_factory=list)


class ContextInjector:
    """Enriches compression with code-graph context."""

    def __init__(self, graph_query: "GraphQuery", session_cache: "SessionCache"):
        self._graph = graph_query
        self._cache = session_cache

    def enrich(
        self,
        result: CompressionResult,
        command: str,
    ) -> CompressionResult:
        """
        Post-process a CompressionResult with graph context.

        - If pytest failed, find affected dependencies
        - If git diff shows changed files, warn about dependents
        - Append [CTXCLP:hints=...] footer
        """
        hints = self._compute_hints(command)
        if hints:
            result.metadata["context_hints"] = hints
            result.compressed = self._append_hints(result.compressed, hints)
        return result

    def _compute_hints(self, command: str) -> ContextHint | None:
        """Analyze command and recent context for relevant hints."""
        ...
```

### 3.2 Session-Long Symbol Memory

```python
# contextclipper/mcp/middleware/session_cache.py

"""
Persistent cache across tool calls within one MCP session.

Problem: Agent queries get_file("auth.py"), gets symbols. Next call,
it runs a shell command referencing those symbols. Without cache,
we re-compute everything.

Solution: In-process LRU cache with TTL that survives individual
tool calls but clears on session end.
"""

from __future__ import annotations

import time
from functools import lru_cache
from contextclipper.core.types import SymbolSummary


class SessionCache:
    """
    Session-scoped cache for symbol lookups and file summaries.

    Lives for the duration of one MCP server process (one agent session).
    """

    def __init__(self, max_size: int = 512, ttl_seconds: int = 300):
        self._symbol_cache: dict[str, tuple[float, list[SymbolSummary]]] = {}
        self._file_cache: dict[str, tuple[float, list[SymbolSummary]]] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds

    def get_symbols(self, file_path: str) -> list[SymbolSummary] | None:
        """Retrieve cached file symbols if fresh."""
        ...

    def set_symbols(self, file_path: str, symbols: list[SymbolSummary]) -> None:
        """Cache file symbols with TTL."""
        ...

    def get_accessed_files(self) -> list[str]:
        """Return recently accessed files for context injection."""
        ...

    def prune(self) -> None:
        """Evict expired entries."""
        ...
```

### 3.3 Workspace Heatmap Resource

```python
# contextclipper/mcp/resources/workspace_hotspots.py

"""
MCP Resource: ctxclp://workspace/hotspots

Returns the N most frequently accessed or critical symbols in the workspace.
Agents can read this at session start to pre-warm their context.

Implementation tracks:
- File access frequency (per tool call)
- Dependency count (highly-depended-upon symbols are "hot")
- Recent git changes (churn-based hotspots)
"""

from contextclipper.graph.query import GraphQuery


class HotspotResource:
    """Provides workspace heatmap data to MCP clients."""

    def __init__(self, graph: GraphQuery):
        self._graph = graph
        self._access_counts: dict[str, int] = {}

    def record_access(self, file_path: str) -> None:
        """Called by tools whenever a file is accessed."""
        self._access_counts[file_path] = self._access_counts.get(file_path, 0) + 1

    def get_hotspots(self, limit: int = 10) -> list[dict]:
        """
        Compute hotspots as weighted combination of:
        - Access frequency (recent agent interest)
        - Dependency fan-in (architectural importance)
        - Recency of changes (active development area)
        """
        ...
```

---

## 4. Migration Strategy

### 4.1 Phase 1: Internal Restructure (No Breaking Changes)
- Move files to new locations
- Add re-export shims at old locations with deprecation warnings
- All existing imports continue working
- `ctxclp` CLI unchanged

```python
# contextclipper/engine/filters.py  (OLD LOCATION — shim)
import warnings
from contextclipper.shell.engine import FilterEngine  # NEW LOCATION

warnings.warn(
    "contextclipper.engine.filters is deprecated, use contextclipper.shell.engine",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["FilterEngine"]
```

### 4.2 Phase 2: Optional Dependencies
- Update `pyproject.toml` with `[project.optional-dependencies]`
- `pip install contextclipper` installs `[full]` by default (no change for users)
- Power users can `pip install contextclipper[shell]` for minimal installs

### 4.3 Phase 3: Enhanced Features
- Add context-aware compression behind a feature flag (`--context-aware` initially)
- Ship session cache as opt-in (`CTXCLP_SESSION_CACHE=1`)
- Graduate to default-on after stabilization

### 4.4 Phase 4: Deprecation Cleanup
- Remove old shims after 2 minor versions
- Clean break documented in CHANGELOG

---

## 5. Testing Strategy

### 5.1 Unit Tests per Subsystem

```
tests/unit/
├── core/
│   ├── test_redact.py        # Secret redaction edge cases
│   ├── test_tee.py           # TTL, eviction, concurrency
│   └── test_stats.py         # Aggregation accuracy
├── shell/
│   ├── test_engine.py        # Filter loading, rule application
│   ├── test_rules.py         # Each rule type in isolation
│   ├── test_matchers.py      # Regex edge cases, ReDoS prevention
│   └── test_streaming.py     # Memory bounds, chunking
├── graph/
│   ├── test_parser.py        # Tree-sitter vs regex parity
│   ├── test_query.py         # Search accuracy, edge cases
│   └── test_indexer.py       # Incremental updates, file watching
└── mcp/
    ├── test_shell_tools.py   # Tool contracts
    ├── test_graph_tools.py   # Tool contracts
    └── test_context_injection.py  # Hint computation logic
```

### 5.2 Integration Tests

```python
# tests/integration/test_agent_flow.py

async def test_full_agent_session():
    """
    Simulate a realistic agent flow:
    1. Read workspace hotspots
    2. Search for a symbol
    3. Get file summary
    4. Run a shell command (context-aware compression)
    5. Fetch raw output
    6. Verify hints in compressed output
    """
    ...

async def test_context_aware_compression():
    """
    Agent accesses auth.py → runs pytest → compressed output
    keeps auth-related failures, drops unrelated noise.
    """
    ...
```

---

## 6. CI/CD Considerations

### 6.1 Matrix Testing
```yaml
# .github/workflows/test.yml
strategy:
  matrix:
    python-version: ["3.12", "3.13"]
    install-type: ["full", "shell-only", "shell+graph", "graph-only"]
```

### 6.2 Build Artifacts
- Single wheel: `contextclipper-{version}-py3-none-any.whl`
- `[full]` is the default extra
- Platform-specific wheels unnecessary (pure Python + tree-sitter bindings)

---

## 7. Documentation Updates

| Document | Purpose | Priority |
|---|---|---|
| `docs/architecture.md` | This modular architecture reference | High |
| `docs/filter-authoring.md` | Guide for writing TOML filters | High |
| `docs/graph-querying.md` | How agents should use graph tools | Medium |
| `docs/agent-integration.md` | Integration patterns for agent devs | High |
| `docs/context-aware.md` | Deep dive on context injection | Low (post-implementation) |
| `docs/contributing.md` | Dev setup, conventions, testing | Medium |

---

## Summary

**Single package, internally modularized, with optional dependencies.** This gives you:
1. One brand, one install command, one MCP server
2. Flexibility for power users to install only what they need
3. Clean internal boundaries that enable context-aware features
4. Graceful degradation when subsystems are missing
5. A migration path with zero breaking changes
