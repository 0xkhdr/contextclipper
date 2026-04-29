# ContextClipper for Agent Developers

This guide explains how to integrate ContextClipper into agent workflows,
parse the recovery footer, and write custom filters.

## Overview

ContextClipper (`ctxclp`) intercepts shell command output and compresses it
before it reaches the agent's context window.  A typical workflow:

```
Agent calls Bash("npm test")
  → ctxclp intercepts
  → runs npm test
  → compresses 3000-line output to 80 lines
  → returns compressed output + recovery footer
Agent reads compressed output (80 tokens instead of 3000)
Agent optionally calls ctxclp fetch <uuid> for full output
```

---

## Invoking ctxclp from an agent

### Basic usage

```bash
ctxclp run -- <command>
```

Examples:
```bash
ctxclp run -- npm test
ctxclp run -- git diff HEAD~1
ctxclp run -- docker ps
ctxclp run --stream -- kubectl logs -f my-pod   # streaming mode
ctxclp run --max-tokens 2000 -- ./long_script.sh
```

### Options

| Flag | Description |
|---|---|
| `--stream` | Stream output line-by-line (constant memory, <100ms latency) |
| `--max-tokens N` | Hard token budget; tail-keeps the most recent output |
| `--dry-run` | Show which lines would be removed without actually running |
| `--raw` | Bypass compression entirely |
| `--enable-telemetry` | Enable regret-detection for this run |

---

## Parsing the recovery footer

Every compressed output ends with two footer lines:

```
[ctxclp: 42/850 lines, -95% tokens | raw_id=abc123def456 | fetch: ctxclp fetch abc123def456 | filter=npm]
[CTXCLP:raw=abc123def456]
```

The **second line** is machine-parseable.  Use this regex to extract the UUID:

```python
import re
CTXCLP_RE = re.compile(r'\[CTXCLP:raw=([0-9a-f]+)\]')

match = CTXCLP_RE.search(output)
if match:
    uuid = match.group(1)
    # Call: ctxclp fetch <uuid>
```

```javascript
const CTXCLP_RE = /\[CTXCLP:raw=([0-9a-f]+)\]/;
const match = output.match(CTXCLP_RE);
if (match) {
  const uuid = match[1];
  // Call: ctxclp fetch <uuid>
}
```

### Fetching full output

```bash
ctxclp fetch <uuid>
```

The tee store keeps outputs for 24 hours (configurable via `CTXCLP_TEE_TTL`).

---

## Agent system prompt snippet

Add this to your agent's system prompt or `CLAUDE.md`:

```markdown
Shell commands are run through ContextClipper (ctxclp) which compresses output
to save tokens.  Every compressed response ends with:
  [CTXCLP:raw=<uuid>]

If you need the full uncompressed output, run:
  ctxclp fetch <uuid>

This gives you the complete original output (available for 24h).
```

---

## Writing custom filters

Filters are TOML files in `~/.config/contextclipper/filters/`.

### Scaffold a new filter

```bash
ctxclp filter new my-tool "my-tool"
```

This creates `~/.config/contextclipper/filters/my-tool.toml`.

### Filter structure

```toml
[filter]
name = "my-tool"
description = "Compress output from my-tool"

[[filter.patterns]]
match_command = "my-tool"        # regex matched against the full command

[[filter.rules]]
description = "Drop verbose progress lines"
type = "drop_matching"
pattern = "^\\[progress\\]"

[[filter.rules]]
description = "Always keep error lines"
type = "keep_matching"
pattern = "^(ERROR|FATAL|FAIL)"
priority = 10                    # higher priority wins over drop rules

[filter.on_failure]
[[filter.on_failure.rules]]
description = "On failure, keep everything"
type = "keep_matching"
pattern = "."
priority = 5
```

### Rule types

| Type | Description |
|---|---|
| `drop_matching` | Drop lines matching a regex |
| `keep_matching` | Keep lines matching a regex (priority wins over drops) |
| `regex_replace` | Substitute matches with `replacement` |
| `head` | Keep only the first N lines |
| `tail` | Keep only the last N lines |
| `keep_section` | Keep lines between `start_pattern` and `end_pattern` |
| `prefix_collapse` | Coalesce runs of lines with a common prefix |
| `json_select` | Extract specific fields from JSON output |

### Built-in strategies

For common output types, use `strategy = "<name>"` in the filter header:

```toml
[filter]
name = "my-logs"
strategy = "log"          # log, diff, table, json-fields
```

| Strategy | Best for |
|---|---|
| `log` | Log files — keeps errors + head/tail + level summary |
| `diff` | `git diff` output — keeps hunks + 3 context lines |
| `table` | `docker ps`, `kubectl get` — keeps non-healthy rows |
| `json-fields` | NDJSON logs — keeps message, level, time, error |

### Testing your filter

```bash
ctxclp filter test my-tool              # run the command and show safety analysis
echo "sample output" | ctxclp filter test --no-run my-tool
```

---

## Streaming mode

For long-running commands, use `--stream` to see output as it arrives:

```bash
ctxclp run --stream -- pytest -x --tb=short
```

**Streaming mode applies rules line-by-line**.  Rules that require the full
output (`tail`, `json_select`) are noted and skipped; use batch mode for those.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CTXCLP_DISABLE_STATS` | `0` | Disable local usage stats |
| `CTXCLP_DISABLE_TEE` | `0` | Disable tee store (no recovery possible) |
| `CTXCLP_TELEMETRY` | `0` | Enable regret-detection telemetry |
| `CTXCLP_TEE_TTL` | `86400` | Tee store TTL in seconds |
| `CTXCLP_MAX_INPUT_BYTES` | `16MiB` | Cap on raw input size |
| `CTXCLP_INCLUDE_MACHINE_FOOTER` | `1` | Append `[CTXCLP:raw=...]` footer |
| `CTXCLP_COMMAND_TIMEOUT` | `300` | Default command timeout (seconds) |
