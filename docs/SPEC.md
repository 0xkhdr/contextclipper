# ContextClipper — Technical Specification

## Overview

ContextClipper (ctxclp) is a universal, zero-configuration token optimizer for AI coding agents. It reduces LLM token usage by 80–95% on shell command output and code navigation without losing semantic information.

## Architecture

```
AI Agent (Claude, Cursor, …)
  ├─ Built-in Bash tool → (PreToolUse hook) → ctxclp run <cmd> → compressed output
  └─ MCP client → (stdio) → ContextClipper MCP Server
        ├─ Tools: get_file, run_shell, get_affected, search_symbols, rebuild_graph, get_raw_output
        └─ Resources: project://overview, project://stats
```

## Components

### ctxclp-engine (shared library)
- **graph.py** — SQLite-backed code graph indexer using tree-sitter
- **filters.py** — TOML-driven shell output compression engine
- **tee.py** — Raw output storage with 24h TTL for full recovery
- **stats.py** — Local analytics database

### ctxclp-mcp (MCP server)
- **server.py** — MCP stdio server
- **tools.py** — Tool implementations

### ctxclp-cli (entry point)
- **main.py** — CLI: `ctxclp run|build|install|serve|stats|filter|hook`
- **install.py** — Agent detection and hook injection

## SQLite Schema

### files table
| Column  | Type    | Description              |
|---------|---------|--------------------------|
| id      | INTEGER | Primary key              |
| path    | TEXT    | Relative path from root  |
| sha256  | TEXT    | File hash for incremental|
| indexed | REAL    | Unix timestamp           |

### symbols table
| Column      | Type    | Description                          |
|-------------|---------|--------------------------------------|
| id          | INTEGER | Primary key                          |
| file_id     | INTEGER | FK → files                           |
| kind        | TEXT    | class/interface/trait/method/function|
| name        | TEXT    | Short name                           |
| fqn         | TEXT    | Fully qualified name                 |
| parent      | TEXT    | Parent class FQN (for methods)       |
| line_start  | INTEGER | Start line                           |
| line_end    | INTEGER | End line                             |
| signature   | TEXT    | Method signature (no body)           |
| visibility  | TEXT    | public/protected/private             |
| is_static   | INTEGER | 0 or 1                               |
| is_abstract | INTEGER | 0 or 1                               |

### dependencies table
| Column     | Type    | Description                          |
|------------|---------|--------------------------------------|
| id         | INTEGER | Primary key                          |
| file_id    | INTEGER | FK → files                           |
| kind       | TEXT    | extends/implements/use/call/import   |
| source_fqn | TEXT    | Source symbol FQN                    |
| target_fqn | TEXT    | Target symbol FQN                    |

## Filter TOML Schema

```toml
[filter]
name = "my-filter"
description = "Human-readable description"
strategy = "name-of-registered-python-strategy"  # optional: bypass rule engine

[[filter.patterns]]
match_command = "^regex-to-match-command"

# Rule types: drop_matching | keep_matching | regex_replace |
#             head | tail | keep_section | prefix_collapse
[[filter.rules]]
type = "drop_matching"
pattern = "^regex"
priority = 0      # higher wins when keep and drop both match a line

[[filter.rules]]
type = "keep_section"
start_pattern = "^FAILURES!"
end_pattern   = "^$"

[[filter.rules]]
type = "prefix_collapse"
prefix = "INFO"
max_lines = 5

[[filter.command_overrides]]
match = "^specific-subcommand"
  [[filter.command_overrides.rules]]
  type = "keep_matching"
  pattern = "^(ERROR|FAIL)"
  priority = 10

# Rules that run only when exit_code != 0:
[filter.on_failure]
  [[filter.on_failure.rules]]
  type = "keep_section"
  start_pattern = "^FAILURES!"
  end_pattern = "^$"
```

Rule application is staged: `head`/`tail` slice the input, then
`regex_replace` substitutes, then `keep_section` selects regions, then
`prefix_collapse` coalesces, then per-line `keep_matching`/`drop_matching`
filter (priority-aware). After the regular rules, `on_failure` rules run a
second pass when the command exited non-zero.

## MCP Tools

| Tool | Description | Performance Target |
|------|-------------|-------------------|
| `get_file(path, mode)` | Symbol summary for a file | < 2ms |
| `search_symbols(query, kind?)` | Find symbols by name/FQN | < 5ms |
| `get_affected(files)` | Blast-radius analysis | < 10ms |
| `run_shell(command)` | Execute + compress shell command | < 10ms overhead |
| `get_raw_output(output_id)` | Retrieve full raw output | < 1ms |
| `rebuild_graph()` | Re-index project | < 5s for 10k files |

## MCP Resources

| URI | Description |
|-----|-------------|
| `project://overview` | Compact Markdown tree with symbols |
| `project://stats` | JSON token savings statistics |

## Agent Support Matrix

| Agent | Hook Type | MCP | Status |
|-------|-----------|-----|--------|
| Claude Code | PreToolUse JSON hook | ✓ | Phase 2 |
| Cursor | preToolUse hooks.json | ✓ | Phase 2 |
| Windsurf | .windsurfrules injection | ✓ | Phase 3 |
| Cline | .clinerules injection | ✓ | Phase 3 |
| Gemini CLI | BeforeTool hook | ✓ | Phase 3 |
| Codex | instructions.md injection | ✓ | Phase 3 |

## Engine API

```python
from ctxclp_engine import (
    compress_output, CompressionResult, FilterRegistry,
    register_strategy, unregister_strategy, get_registry,
    redact_text, redact_command,
)

# Core compression
result: CompressionResult = compress_output(
    command="git status",
    raw_output=output,
    exit_code=0,
    raw_output_id=None,         # optional id from tee store
    *,
    dry_run=False,              # populate result.removed_lines
    max_input_bytes=None,       # override 16 MiB default
    max_tokens=None,            # adaptive tail-truncation (1 token ≈ 4 chars)
    strategy=None,              # force a registered Python strategy
)
# result.compressed, result.original_lines, result.kept_lines,
# result.bytes_in, result.bytes_out, result.elapsed_ms,
# result.truncated, result.strategy_name, result.removed_lines, result.reduction_pct

# Custom Python strategies
register_strategy("my-redactor", lambda lines, cmd, ec: [...])

# Health-check
get_registry().validate()       # {"ok": True, "filters": 7, "problems": []}
```

## Security Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `CTXCLP_MAX_LINE_BYTES` | 65 536 | Per-line input cap (ReDoS bound) |
| `CTXCLP_MAX_INPUT_BYTES` | 16 777 216 | Total input cap (OOM bound) |
| `CTXCLP_TEE_REDACT` | `1` | Redact secrets before tee write |
| `CTXCLP_DISABLE_TEE` | unset | Skip on-disk tee writes |
| `CTXCLP_DISABLE_STATS` | unset | Skip analytics DB writes |
| `XDG_CONFIG_HOME`/`XDG_DATA_HOME` | XDG defaults | Override storage roots |

See [SECURITY.md](../SECURITY.md) for the full threat model.

## Performance Targets

| Operation | Target |
|-----------|--------|
| Shell hook overhead | < 10ms |
| Graph initial build (10k PHP files) | < 5s |
| Graph incremental update | < 500ms |
| `get_file` MCP response | < 2ms |
| `run_shell` MCP response | < 10ms overhead |
| CLI startup | < 50ms |
| Token reduction | 80–95% |
