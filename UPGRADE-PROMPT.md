- All telemetry must be opt‑in; add `--enable-telemetry` flag and an environment variable.
- Privacy: never collect command arguments, only the command base name and filter name.
- **Tests:**  
- Simulate multiple runs and fetches; verify suggestions appear.
- **Documentation:**  
- Explain the feature in the privacy section of README, clarifying that no data leaves the machine.

### 5.2 Filter Health Dashboard
- **Task:**  
- `ctxclp stats --dashboard` starts a local web server (Flask or simple HTTP) that displays:
- Table of all commands, showing runs, avg compression %, regret rate (fetch count / runs).
- Highlight filters with regret > 30%.
- Allow user to disable a filter directly from the dashboard (writes to local config).
- Keep the dashboard simple and self‑contained (no external dependencies beyond the standard library if possible).
- **Acceptance criteria:**  
- Dashboard is usable and correctly reflects local usage data.

---

## Final Verification Checklist
After implementing all phases, run through these checks:
- [ ] All unit, integration, and benchmark tests pass (`pytest`, `./run_benchmarks.sh`).
- [ ] Streaming mode works for a 100,000‑line command without memory growth.
- [ ] Every new strategy and adaptor has a clear README and example.
- [ ] Protocol footer appears correctly and can be parsed by a regex.
- [ ] Filter registry CI validates contributed filters.
- [ ] Governance docs (GOVERNANCE.md, RFC template) are present.
- [ ] Benchmark suite generates a dashboard with token‑savings data.
- [ ] Audit scope is documented and ready for submission.
- [ ] `--suggestions` and dashboard work with local stats.
- [ ] No gaps remain when comparing with `IMPROVEMENT_PLAN.md` (the plan you began with).

**Once all checks pass, commit all changes, push the branch, and create a pull request with a summary of what was implemented, referencing the improvement plan.**

---

## AI Instructions (how to use this prompt)
- Work through each phase in order. Do not skip tasks.
- For every code change, write the exact implementation, not just a description.
- Include detailed tests (using `pytest` where applicable) that exercise edge cases.
- When a task involves creating files (e.g., adaptors, registry), generate the complete file contents.
- If you encounter platform‑specific details, assume Linux/macOS compatibility.
- At the end, produce a `SUMMARY.md` that explains the implemented changes and confirms no gaps remain.

> **Begin now with Phase 1.1: Live Streaming Mode.**
