# RFC 001 — Streaming Engine

- **Status**: accepted
- **Author(s)**: contextclipper maintainers
- **Created**: 2026-04-29
- **Implemented in**: v0.4.0

## Summary

Add a `--stream` flag to `ctxclp run` that processes subprocess output
line-by-line without buffering the full output, keeping memory usage constant
for arbitrarily long commands.

## Motivation

The existing batch engine collects the full output before compressing it.
This causes:

1. **Latency**: For long-running commands (test suites, builds, log tailing),
   the agent sees no output until the command finishes.
2. **Memory pressure**: Very long outputs (>16 MiB default cap) are truncated.
3. **Unhelpful for streaming use cases**: `kubectl logs -f` and similar
   tail-following commands never terminate.

## Detailed design

### CLI interface

```bash
ctxclp run --stream -- kubectl logs -f my-pod
ctxclp run --stream --max-tokens 4000 -- pytest -x
```

A `streaming = true` option in the filter TOML is also supported (future).

### StreamingFilter class

`StreamingFilter` in `engine/streaming.py` is a stateful, per-line filter:

```
for each line from subprocess stdout:
    kept_lines = sf.feed(line)    # returns [] (dropped) or [line, ...]
    write kept_lines to stdout immediately

# at end:
for line in sf.flush():
    write to stdout
```

State maintained:
- `head_count` — tracks lines remaining in a `head` rule budget
- `in_section[i]` — whether we're inside section rule `i`'s start/end window
- `prefix_pending` / `prefix_rule` — accumulate prefix-collapse blocks
- `prev_line` / `repeat_count` — dedup consecutive identical lines

### Rule compatibility in streaming mode

| Rule type | Streaming support |
|---|---|
| `drop_matching` | Full |
| `keep_matching` | Full |
| `regex_replace` | Full |
| `head` | Full (tracks count) |
| `keep_section` | Full (stateful start/end) |
| `prefix_collapse` | Partial (buffered up to max_lines) |
| `tail` | **Not supported** — emits a notice, rule is skipped |
| `json_select` | **Not supported** — requires full buffer; rule is skipped |

### Memory bound

The only in-memory buffer is the prefix-collapse window (max_lines, default 10).
All other lines are processed and immediately discarded.

### Subprocess interaction

Uses `subprocess.Popen(stdout=PIPE, stderr=STDOUT, bufsize=1, text=True)`.
Timeout is implemented via a `threading.Timer` that calls `proc.kill()`.

### Metrics

`StreamStats` reports `original_lines`, `kept_lines`, `bytes_in`, `bytes_out`,
`elapsed_ms`, `timed_out`, and `truncated`.  Footer is printed to stderr.

## Drawbacks

- Buffered subprocesses (non-TTY) may not flush line-by-line; behaviour depends
  on the subprocess's own buffering (use `stdbuf -oL` for GNU tools if needed).
- `tail` and `json_select` rules are silently skipped with a notice line.
- Tee store in streaming mode stores a placeholder, so `ctxclp fetch` is not
  useful after a `--stream` run.

## Alternatives considered

**Full buffering with early flushing**: Would require detecting natural flush
points in the output, which is command-specific and unreliable.

**PTY-based approach**: Using a pseudo-terminal would force line-buffering in
subprocesses, but adds platform complexity and is out of scope for v0.4.0.

## Unresolved questions

- Should `json_select` be approximated in streaming mode by buffering up to a
  configurable number of lines?  (Deferred to v0.5.0.)
