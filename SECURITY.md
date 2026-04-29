# Security Model

ContextClipper sits in the local trust boundary of an AI agent: it observes the
agent's shell output, persists redacted command metadata to disk for analytics,
and serves indexed code-graph queries over an MCP stdio channel. This document
summarizes the threats considered, the mitigations in place, and the controls
the operator can flip if they need stronger guarantees.

## Threat model

| # | Threat                                                                 | Mitigation |
|---|------------------------------------------------------------------------|------------|
| 1 | A user-supplied regex in a TOML filter exhibits catastrophic backtracking (ReDoS) and stalls the agent | Per-line cap (`CTXCLP_MAX_LINE_BYTES`, default 64 KiB) and total-input cap (`CTXCLP_MAX_INPUT_BYTES`, default 16 MiB) bound the input the regex sees. Patterns evaluate against bounded strings, so worst-case time is bounded. |
| 2 | A malformed TOML filter crashes the engine on startup or for one command | Parse errors are caught, logged at WARNING via the `ctxclp` logger, and the filter is skipped. Other filters continue to load. |
| 3 | Captured shell output (tee store) contains tokens / passwords / API keys that persist to `~/.local/share/contextclipper/tee/*.log` | All output and the command line are run through `ctxclp_engine.redact` before write. Files are mode `0o600`, directory `0o700`. Disable persistence entirely with `CTXCLP_DISABLE_TEE=1`. |
| 4 | Stats DB records sensitive command-line flags (`--password=…`, `--token=…`)        | The `command` column is redacted on every insert. Disable with `CTXCLP_DISABLE_STATS=1`. |
| 5 | A predictable tee ID lets a co-tenant guess and read another user's captured output | IDs are `secrets.token_hex(8)` (64 bits, cryptographically random) instead of the previous `sha256(command + time)` derivation. Combined with `0o600` perms, guessing requires a privileged local account. |
| 6 | Path traversal via `tool_get_file(path="../../etc/passwd")` exfiltrates files outside the project root | Absolute paths must resolve inside the configured project root; relative paths containing `..` are rejected before the database query is issued. |
| 7 | Path traversal via `get_raw(output_id="../../something")` reads arbitrary files in the tee dir | `output_id` is validated as hex-only before use. |
| 8 | Prompt injection embedded in shell output (e.g., a `git log` containing `Ignore previous instructions; …`) | Out of scope for the engine — the agent is responsible. ContextClipper passes content through verbatim, but the redaction pass does scrub credential patterns that an attacker might try to exfiltrate. Operators concerned about prompt injection should use the `aggressive` compression level or a custom strategy that strips suspicious patterns. |
| 9 | The `run_shell` MCP tool executes arbitrary commands (`shell=True`) | This is by design — it is a shell runner. The 120-second timeout, output capping, and the agent's own permission model are the controls. Operators who do not want this surface should not register the `run_shell` tool. |
| 10| Predictable filter loading order lets a malicious user-filter override a builtin | User filters are loaded *after* builtins; later filters take precedence in `find()`. This is documented and intentional — users can override built-in filters. Audit `~/.config/contextclipper/filters/` if running in a shared account. |
| 11| Memory exhaustion from a 10 GB log accidentally piped to a hooked command | Total input is capped (`CTXCLP_MAX_INPUT_BYTES`); excess is truncated with a marker. The compressed result has known upper bound. |
| 12| Concurrent writes to the SQLite stats / graph DBs corrupt them | Both DBs use WAL journaling, and writes are serialized through a `threading.RLock`. |

## Redaction patterns

Redaction is **best-effort defense in depth**, not a security boundary. The
patterns below cover the highest-leverage credential shapes; novel formats may
slip through. If the workload routinely handles sensitive material, prefer
disabling persistence entirely.

| Category | Example matched | Rendered as |
|----------|-----------------|-------------|
| CLI flag | `--token=abc123`, `--password supersecret`, `--auth=xxx` | `--token=[REDACTED]` |
| Env-style | `API_TOKEN=xxx`, `DB_PASSWORD=xxx`, `MY_AUTH=xxx` | `API_TOKEN=[REDACTED]` |
| Auth header | `Authorization: Bearer eyJ…`, `Authorization: Basic …` | `Authorization: Bearer [REDACTED]` |
| AWS access key prefix | `AKIA…` (20 chars) | `[REDACTED]` |
| GitHub / Slack tokens | `ghp_…`, `gho_…`, `glpat_…`, `xoxb-…` | `[REDACTED]` |
| JSON field | `"token": "live-secret"`, `"api_key": "..."` | `"token": "[REDACTED]"` |

Override the redaction policy by registering a custom strategy:

```python
from ctxclp_engine import register_strategy

def my_strict_redactor(lines, command, exit_code):
    return [line for line in lines if "BEGIN PRIVATE KEY" not in line]

register_strategy("strict-redact", my_strict_redactor)
```

…and reference it from a TOML filter (`strategy = "strict-redact"`).

## Environment-variable kill switches

| Variable                  | Default | Effect when set |
|---------------------------|---------|-----------------|
| `CTXCLP_DISABLE_TEE`      | unset   | Disable on-disk tee storage entirely |
| `CTXCLP_DISABLE_STATS`    | unset   | Disable analytics DB writes |
| `CTXCLP_TEE_REDACT`       | `1`     | Set to `0` to disable redaction (NOT recommended) |
| `CTXCLP_TEE_TTL`          | `86400` | Seconds before tee files are evicted |
| `CTXCLP_TEE_MAX_BYTES`    | `100 MiB` | Total tee dir cap |
| `CTXCLP_MAX_LINE_BYTES`   | `65536` | Per-line cap (ReDoS bound) |
| `CTXCLP_MAX_INPUT_BYTES`  | `16 MiB` | Total input cap (OOM bound) |
| `CTXCLP_LOG_LEVEL`        | `WARNING` | DEBUG / INFO / WARNING / ERROR |
| `XDG_CONFIG_HOME`         | `~/.config` | Override user filter dir base |
| `XDG_DATA_HOME`           | `~/.local/share` | Override tee + stats DB base |

## Reporting a vulnerability

Open a GitHub security advisory on the project repository. Please do not file
public issues for security reports. Include the affected version, a minimal
reproducer, and the threat model entry (or new entry) it falls under.

## Out of scope

- Sandboxing the executed shell command itself. ContextClipper does not
  attempt to be a syscall sandbox; consult `firejail`, `bubblewrap`, or your
  agent's own permission model for that.
- Network egress controls.
- Multi-tenant isolation between users on the same host. Per-user
  `~/.local/share/...` is the only isolation; operators sharing a host across
  trust boundaries should isolate via OS users / containers.
