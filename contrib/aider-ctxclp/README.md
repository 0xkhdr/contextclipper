# aider-ctxclp

ContextClipper wrapper for **Aider**.

## What it does

Wraps Aider's shell command execution with `ctxclp run` to reduce token usage
by 80–95%.  Optionally auto-fetches the full output when Aider's response
indicates it needs more context.

## Installation

### Option 1 — Shell alias (simplest)

```bash
alias aider-compressed='AIDER_SHELL_CMD="ctxclp run --" aider'
```

### Option 2 — Aider config

```yaml
# ~/.aider.conf.yml
shell_cmd: "ctxclp run --"
```

### Option 3 — Wrapper script

```bash
cp aider_ctxclp.py /usr/local/bin/aider-ctxclp
chmod +x /usr/local/bin/aider-ctxclp
```

Then use `aider-ctxclp <command>` in place of direct shell calls.

## Recovery protocol

Add to your Aider system prompt:

```
When you see [CTXCLP:raw=<uuid>] in command output, run:
  ctxclp fetch <uuid>
to get the full uncompressed output.
```

## Auto-fetch mode

Set `CTXCLP_AUTO_FETCH=1` to automatically fetch the full output when Aider
responds with phrases like "need more", "full output", etc.:

```bash
CTXCLP_AUTO_FETCH=1 aider-ctxclp npm test
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `CTXCLP_BIN` | `ctxclp` | Path to ctxclp binary |
| `CTXCLP_MAX_TOKENS` | `8000` | Max tokens per compressed output |
| `CTXCLP_AUTO_FETCH` | `0` | Set to `1` for automatic recovery |
