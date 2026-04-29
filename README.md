# ContextClipper (ctxclp)

**Universal token optimizer for AI coding agents.**

ContextClipper reduces LLM token usage by **80–95%** on shell output and code navigation without losing semantic information. It transparently intercepts shell commands and provides a high-performance code-graph index via MCP.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

---

## 🚀 Quick Start

```bash
# 1. Install (requires Python 3.12+)
curl -fsSL https://get.contextclipper.dev | sh
# Or via uv: uv tool install contextclipper

# 2. Index your project (creates .ctxclp.db)
ctxclp build

# 3. Auto-detect and install hooks for your AI agents (Claude Code, Cursor, etc.)
ctxclp install

# 4. Verify installation
ctxclp validate
```

**Restart your AI tool.** Now, whenever the agent runs a shell command, the output is automatically compressed before it reaches the LLM.

---

## 🧠 How it Works

ContextClipper operates as a **hybrid optimization layer**:

1.  **Transparent Shell Hooks:** Intercepts bash/zsh commands run by agents (e.g., `npm test`, `git status`) and pipes them through a high-speed TOML-driven compression engine.
2.  **Code-Graph MCP Server:** Provides tools like `get_file_symbols` and `project://overview` so agents can understand your codebase structure without reading thousands of lines of source code.
3.  **Smart Recovery (Tee Store):** If an agent needs the full raw output (e.g., to see a specific long stack trace), it can pull it from the local Tee Store using a unique ID provided in the compressed output.

### Architecture

```text
AI Agent (Claude, Cursor, etc.)
  │
  ├─ Shell Tool ────▶ [ Hook Injection ] ──▶ ctxclp run <cmd> ──▶ [ Filter Engine ] ──▶ Compressed Output
  │                                                                    │
  └─ MCP Client ────▶ [ MCP Server ] ◀─────────────────────────────────┘
                          │
                          ├─ Code Graph (SQLite/Tree-sitter)
                          └─ Tee Store (Raw Output Persistence)
```

## 📂 Directory Structure

- `src/contextclipper/engine/`: Core logic (Filtering, Code Graph, Redaction, Tee Store).
- `src/contextclipper/mcp/`: MCP server implementation (stdio transport, tools, resources).
- `src/contextclipper/cli/`: CLI entry points (`main.py`) and agent hook installer (`install.py`).
- `src/contextclipper/filters/`: Built-in TOML definitions for common tools (Git, NPM, PHP, Python, etc.).
- `src/contextclipper/hooks/`: Raw shell hook scripts used during injection.
- `docs/`: Technical specifications and deeper documentation.
- `tests/`: Comprehensive test suite.

---

## 🛠 Features

- **80-95% Token Reduction:** Purpose-built filters for `git`, `composer`, `npm`, `pytest`, `docker`, and more.
- **Zero Configuration:** Auto-detects project type and applies relevant filters.
- **Security Hardened:** Best-effort secret redaction (API keys, tokens) before any data is persisted or sent to the LLM.
- **Developer Friendly:** 
  - Add custom filters via simple TOML files.
  - Write custom Python strategies for complex logic.
  - `ctxclp stats` to see your actual savings.
- **Agent Agnostic:** Supports Claude Code, Cursor, Windsurf, Cline, Gemini CLI, and others.

---

## 📖 Extension Guide

### Filter Anatomy (TOML)

Filters are defined in TOML and matched against the command line.

```toml
[filter]
name = "my-tool"
description = "Handles my custom build tool"

[[filter.patterns]]
match_command = "^my-tool\\b"

[[filter.rules]]
type = "drop_matching"
pattern = "^DEBUG:"
priority = 1

[[filter.rules]]
type = "keep_matching"
pattern = "^FATAL:"
priority = 10  # Higher priority wins

[[filter.rules]]
type = "regex_replace"
pattern = "0x[0-9a-fA-F]+"
replacement = "[ADDR]"

[[filter.rules]]
type = "keep_section"
start_pattern = "^STACK TRACE:"
end_pattern = "^END STACK TRACE"

[[filter.rules]]
type = "prefix_collapse"
prefix = "  at "
max_lines = 3  # Collapse long stack traces
```

**Supported Rule Types:**
- `drop_matching`: Remove lines matching regex.
- `keep_matching`: Keep lines matching regex (overrides drop if higher priority).
- `regex_replace`: Replace text within lines.
- `keep_section`: Only keep blocks between start/end patterns.
- `prefix_collapse`: Coalesce consecutive lines starting with a prefix.
- `head` / `tail`: Keep only the first/last N lines.

### Custom Python Strategies

For logic too complex for regex, register a Python function:

```python
from ctxclp_engine import register_strategy

def my_smart_filter(lines, command, exit_code):
    # lines is a list of strings (raw output)
    # return a list of strings (compressed output)
    return [ln for ln in lines if "IMPORTANT" in ln]

register_strategy("my-strategy", my_smart_filter)
```

Then reference it in your TOML:
```toml
[filter]
strategy = "my-strategy"
```

---

## 🔍 Code Graph & MCP

ContextClipper uses **Tree-sitter** to parse your source code and build a lightweight SQLite-backed graph.

- **`ctxclp build`**: Scans the project, extracts classes, methods, and signatures, and stores them in `.ctxclp.db`.
- **`project://overview`**: A resource provided to the agent that shows a compact tree of the whole project with important symbols.
- **`get_file_symbols`**: An MCP tool that returns a summary of a file (signatures only) instead of the full source.

---

## 📖 Detailed Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CTXCLP_LOG_LEVEL` | `WARNING` | Logging verbosity (DEBUG/INFO/WARNING/ERROR) |
| `CTXCLP_MAX_INPUT_BYTES` | `16 MiB` | Input cap per command to prevent OOM |
| `CTXCLP_MAX_LINE_BYTES` | `64 KiB` | Per-line cap to prevent ReDoS |
| `CTXCLP_TEE_TTL` | `86400` | Seconds to keep raw output (default 24h) |
| `CTXCLP_DISABLE_TEE` | `0` | Set to `1` to disable raw output persistence |
| `CTXCLP_DISABLE_STATS` | `0` | Set to `1` to disable analytics |

### Custom Filters

User filters live in `~/.config/contextclipper/filters/*.toml`.

```toml
[filter]
name = "my-custom-tool"
description = "Compresses my internal build tool output"

[[filter.patterns]]
match_command = "^my-tool"

[[filter.rules]]
type = "drop_matching"
pattern = "^INFO:"  # Drop all info lines
priority = 1

[[filter.rules]]
type = "keep_section"
start_pattern = "^ERRORS FOUND:"
end_pattern = "^$"
```

---

## ⌨️ CLI Reference

| Command | Usage |
|---------|-------|
| `ctxclp run <cmd>` | Execute and compress a command manually |
| `ctxclp build` | Rebuild the code-graph index |
| `ctxclp install` | Inject hooks into detected AI agents |
| `ctxclp stats` | Show token savings and performance metrics |
| `ctxclp serve` | Start the MCP server (usually managed by the agent) |
| `ctxclp validate` | Check health of registry and database |

---

## 🧑‍💻 Development

### Setup

```bash
git clone https://github.com/contextclipper/contextclipper
cd contextclipper
uv sync --all-extras
```

### Running Tests

```bash
uv run pytest
uv run pytest --benchmark-only
```

### Contribution Guide

1.  **Add a Filter:** Place a new TOML in `filters/`.
2.  **Add a Strategy:** Register in `ctxclp_engine/filters.py`.
3.  **Update Agent Support:** Modify `ctxclp_cli/install.py`.

See [docs/SPEC.md](docs/SPEC.md) for the full technical specification.

---

## 🔒 Security

ContextClipper is designed with a "Local First" and "Privacy First" mindset.
- **Redaction:** Automatic scrubbing of credentials (AWS keys, JWTs, etc.).
- **Permissions:** Restricted file permissions (`0o600`) for all cached data.
- **No Telemetry:** We don't phone home. All stats and data stay on your machine.

See [SECURITY.md](SECURITY.md) for details.

---

## 📄 License

MIT © [ContextClipper Contributors](LICENSE)
