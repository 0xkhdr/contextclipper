# claude-code-ctxclp

Transparent ContextClipper adaptor for **Claude Code**.

## What it does

This hook intercepts every Bash tool call that Claude Code makes, wraps it
with `ctxclp run`, and returns the compressed output.  The agent sees 80–95%
fewer tokens from shell commands without changing its workflow.

Every compressed response ends with a **machine-parseable footer**:

```
[CTXCLP:raw=<uuid>]
```

Claude Code can call `ctxclp fetch <uuid>` at any time to retrieve the full
uncompressed output.

## Installation

```bash
# Automatic (recommended)
ctxclp install

# Manual
cp claude_code_ctxclp.py ~/.config/claude-code/hooks/pre_tool_use.py
```

## Recovery protocol

Add this to your Claude Code system prompt or `CLAUDE.md`:

```
When a shell command output ends with [CTXCLP:raw=<uuid>], you can run:
  ctxclp fetch <uuid>
to retrieve the complete uncompressed output if needed.
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `CTXCLP_BIN` | `ctxclp` | Path to ctxclp binary |
| `CTXCLP_STREAM` | `0` | Set to `1` for streaming mode |
| `CTXCLP_MAX_TOKENS` | *(unset)* | Max tokens per output |

## Example

```bash
# Claude Code runs: git log --oneline -20
# Hook rewrites to: ctxclp run -- git log --oneline -20
# Agent receives: compressed log + [CTXCLP:raw=abc123]
# If needed: ctxclp fetch abc123  →  full 500-line log
```
