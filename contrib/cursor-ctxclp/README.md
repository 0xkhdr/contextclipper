# cursor-ctxclp

ContextClipper custom tool definitions for **Cursor**.

## What it does

Two JSON tool definitions let Cursor's AI use `ctxclp run` instead of raw
shell execution, and `ctxclp fetch` to recover full output when needed.

## Installation

1. Copy `cursor_tool.json` and `cursor_fetch_tool.json` to your Cursor
   custom tools directory (usually `~/.cursor/tools/`).
2. Restart Cursor.

```bash
mkdir -p ~/.cursor/tools
cp cursor_tool.json cursor_fetch_tool.json ~/.cursor/tools/
```

## Tools

### `ctxclp_run`

Executes a shell command and returns compressed output.

```json
{
  "command": "npm test",
  "max_tokens": 4000
}
```

Response includes `[CTXCLP:raw=<uuid>]` for recovery.

### `ctxclp_fetch`

Retrieves the full uncompressed output.

```json
{
  "uuid": "abc123def456"
}
```

## Recovery workflow

1. Agent calls `ctxclp_run` with `"npm test"`
2. Gets back compressed test output + `[CTXCLP:raw=abc123]`
3. If the agent needs the full failure details: `ctxclp_fetch` with `"uuid": "abc123"`
4. Receives the complete uncompressed output

## Configuration

Set `CTXCLP_BIN` in your environment if `ctxclp` is not in `$PATH`.
