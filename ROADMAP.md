# ContextClipper Roadmap

> Last updated: 2026-04-29

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

## v0.5.0 — Independent Validation

- [ ] Public benchmark suite with reproducible results and GitHub Pages dashboard
- [ ] Third-party security audit (OSTIF application or private firm)
- [ ] Streaming `json_select` approximation (buffer up to N lines)
- [ ] PTY-based streaming option for subprocess line-buffering
- [ ] GitHub Actions CI for community filter registry

## v0.6.0 — Community & Ecosystem

- [ ] Community filter registry live at `contextclipper/contextclipper-filters`
- [ ] 10+ community-contributed filters (npm, pip, docker, kubectl, terraform…)
- [ ] `ctxclp registry publish` command
- [ ] First external security audit report published in `SECURITY.md`
- [ ] `ctxclp stats --dashboard` persistent config (disable filters from UI)

## Backlog / Ideas

- Streaming `tail` approximation via circular buffer
- VSCode extension with real-time compression preview
- Per-project filter config (`.ctxclp.toml` in project root)
- gRPC / HTTP transport for MCP server (in addition to stdio)
- WebAssembly build for browser-based compression preview

---

Open a GitHub Discussion or RFC if you'd like to champion a backlog item.
