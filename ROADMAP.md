# ContextClipper Roadmap

> Last updated: 2026-04-30

This document tracks current milestones and future plans.  For feature
proposals, open an RFC in [`rfcs/`](rfcs/).

## v0.4.0 — Agent Integration & Streaming (current)

- [x] **Live streaming mode** (`ctxclp run --stream`) — RFC 001
- [x] **Built-in strategies** — `log`, `diff`, `table`, `json-fields`
- [x] **Machine-parseable footer** — `[CTXCLP:raw=<uuid>]` for agent regex parsing
- [x] **Agent adaptors** — Claude Code, Cursor, Aider (`contrib/`)
- [x] **Agent developer guide** — `docs/agent-developer-guide.md`
- [x] **Community filter registry** (`ctxclp registry install <name>`)
- [x] **Open governance** — GOVERNANCE.md, RFC template, RFC 001
- [x] **Telemetry / regret detection** — `ctxclp stats --suggestions`
- [x] **Filter health dashboard** — `ctxclp stats --dashboard`
- [x] **Benchmark suite** — `benchmarks/`
- [x] **Semantic Context Budget Manager (SCBM)** — replaces naïve tail-truncation with
  importance-scored greedy line selection; error/stack lines always preserved under budget
  (`src/contextclipper/engine/scbm.py`, 54 tests)
- [x] **Per-project config** (`.ctxclp.toml`) — upward-searching project config with
  `max_tokens`, `compression`, `filter_dirs`, `passthrough_commands`, `disable_filters`
  (`src/contextclipper/engine/project_config.py`, 30 tests)

## v0.5.0 — Semantic Quality & Independent Validation

- [ ] Public benchmark suite with reproducible results and GitHub Pages dashboard
- [ ] Third-party security audit (OSTIF application or private firm)
- [ ] Streaming `json_select` approximation (buffer up to N lines)
- [ ] PTY-based streaming option for subprocess line-buffering
- [ ] GitHub Actions CI for community filter registry
- [ ] p99 latency tracking in `stats.py` (reservoir sampler)
- [ ] Language-aware token estimator (replace `_CHARS_PER_TOKEN = 4` constant)
- [ ] `GET /health` endpoint on `ctxclp stats --dashboard`
- [ ] `inspect.signature` validation for registered strategies at registration time
- [ ] Streaming circular-buffer `tail` approximation

## v0.6.0 — Community & Ecosystem

- [ ] Community filter registry live at `contextclipper/contextclipper-filters`
- [ ] 10+ community-contributed filters (npm, pip, docker, kubectl, terraform…)
- [ ] `ctxclp registry publish` command
- [ ] First external security audit report published in `SECURITY.md`
- [ ] `ctxclp stats --dashboard` persistent config (disable filters from UI)
- [ ] OpenWebUI + Continue.dev agent adaptors
- [ ] Entry-point plugin discovery via `importlib.metadata` (`ctxclp.strategies`)
- [ ] Structured PII redaction (JSON field values: email, phone, SSN patterns)

## Backlog / Ideas

- KV cache / prefill prefix optimization for Anthropic and OpenAI APIs
- VSCode extension with real-time compression preview
- gRPC / HTTP transport for MCP server (in addition to stdio)
- WebAssembly build for browser-based compression preview
- Version-aware context (diff current vs last seen output for same command)
- Agentic context pruning via lightweight ONNX classifier
- Multi-modal blob summarization (image/binary output → text summary)

---

Open a GitHub Discussion or RFC if you'd like to champion a backlog item.

