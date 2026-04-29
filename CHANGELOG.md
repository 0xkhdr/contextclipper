# Changelog

All notable changes to ContextClipper are documented here. The project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/).

## [0.2.0] – 2026-04-29

A hardening, performance, and feature release. **Fully backwards compatible** at
the public API surface (`compress_output`, `FilterRegistry`, `GraphDB`,
`StatsDB`, `save_raw`/`get_raw`, MCP tool names) — new behavior is opt-in via
keyword arguments and environment variables.

### Added

- **Dry-run mode** (`compress_output(..., dry_run=True)` / `ctxclp run --dry-run`):
  returns the list of removed lines so users can audit what compression
  discarded. ([`CompressionResult.removed_lines`])
- **Adaptive token clipping** (`compress_output(..., max_tokens=N)` /
  `ctxclp run --max-tokens N`): tail-truncates kept output so total approximate
  tokens ≤ N. The `run_shell` MCP tool also accepts `max_tokens` and the
  `aggressive` compression level applies a 2 000-token default.
- **Pluggable compression strategies** —
  `register_strategy(name, fn) / unregister_strategy(name)` allows installing a
  Python compressor that bypasses the TOML rule engine. Filters can declare
  `strategy = "<name>"` to opt in.
- **`ctxclp validate`** CLI command and `FilterRegistry.validate()` /
  `GraphDB.validate()` self-check methods. Used as health-check endpoints by
  embedders / oncall.
- **Per-rule priority** is now honored: a higher-priority `keep_matching` rule
  overrides a lower-priority `drop_matching`, and vice versa.
- **`prefix_collapse` rule type** is now implemented (was silently no-op);
  used by the `artisan` filter to coalesce repeated `INFO` lines.
- **`[filter.on_failure]` rules** now actually run after the regular rules when
  the command exited non-zero (the `phpunit` filter relies on this).
- **`head` rule type** for symmetry with `tail`.
- **OSC ANSI sequence stripping** — terminal title / hyperlink escapes
  (`ESC ] … BEL`) are now stripped along with the standard CSI escapes.
- **Metrics** in `CompressionResult`: `bytes_in`, `bytes_out`, `elapsed_ms`,
  `truncated`, `strategy_name`. The stats DB records bytes saved and
  per-command average latency; `ctxclp stats` exposes them.
- **XDG Base Directory** support — `XDG_CONFIG_HOME` and `XDG_DATA_HOME` are
  honored when computing user-filter, tee, and stats locations.
- **Structured logging** via the `ctxclp` logger (level configured by
  `CTXCLP_LOG_LEVEL`). Replaces silent `except Exception` swallowing across the
  codebase.
- **Documentation**: `SECURITY.md` (threat model, redaction policy), this
  CHANGELOG, expanded `README.md`, expanded `docs/SPEC.md`.

### Changed

- **Hard input bounds** on `compress_output`: each line is capped at
  `CTXCLP_MAX_LINE_BYTES` (default 64 KiB) before any regex evaluation, and the
  total input is capped at `CTXCLP_MAX_INPUT_BYTES` (default 16 MiB). This
  mitigates ReDoS exploits in user-supplied filter regexes (Python's `re`
  module has no native timeout). When the input is truncated, the
  `CompressionResult.truncated` flag is set and a marker line is appended.
- **`FilterRegistry` is now thread-safe** — load and reload are guarded by a
  reentrant lock. `GraphDB` write transactions are also lock-guarded.
- **Tee storage hardening** — directory is created with mode `0o700`, files
  with mode `0o600`. IDs are generated via `secrets.token_hex` instead of
  `sha256(command + time)` (which was predictable). Output and command text
  are run through the new redaction module before persistence (disable with
  `CTXCLP_TEE_REDACT=0`). Tee is fully disable-able via `CTXCLP_DISABLE_TEE=1`.
  `get_raw` validates that the supplied ID is hex-only — defense against
  path-traversal IDs.
- **Stats DB redacts** the `command` column on every insert. Disable
  persistence with `CTXCLP_DISABLE_STATS=1`.
- **`tool_get_file`** path-traversal hardening — absolute paths must resolve
  inside `project_root`; relative paths containing `..` are rejected.
- **`keep_section` end logic** — sections now correctly close on the
  `end_pattern` match alone (the previous AND-with-blank-line condition meant
  most sections were kept all the way to EOF).
- **TOML loader** — parse failures now log at WARNING (with the offending
  filename and exception) instead of being silently swallowed.

### Fixed

- `_apply_rules` returned `lines` unchanged when only `regex_replace` /
  `tail` / `head` rules existed and there were no `keep`/`drop` rules — fine,
  but combined with the section bug above, multi-phase rule sets behaved
  inconsistently. Phase ordering is now explicit and documented.
- `cmd_run` no longer swallows stats-recording errors silently.
- The MCP `tool_run_shell` no longer leaks raw exception messages — error
  responses now include the exception class name and a one-line description.

### Security

- ReDoS mitigation via per-line and total-input byte caps (see _Changed_).
- Secret redaction in tee + stats persistence, with high-confidence patterns
  for: `--token=`/`--password=` flags, `Authorization:` headers,
  `*_TOKEN=`/`*_PASSWORD=` env-style assignments, AWS access-key prefixes,
  GitHub PATs (`ghp_…`, `gho_…`, `glpat_…`), Slack tokens (`xox[bapr]…`),
  and JSON `"token": "..."` fields. See `SECURITY.md`.
- Restrictive filesystem permissions on persisted artifacts.
- Cryptographic-quality tee IDs (`secrets.token_hex`).

### Deprecated

None. All prior public APIs retain the same signatures and semantics.

## [0.1.0] – 2026-04-25

Initial release. Filter engine, code graph indexer, MCP server, CLI wrapper,
shell hooks for Claude Code / Cursor / Windsurf / Cline / Gemini CLI / Codex.
