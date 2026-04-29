# ContextClipper Governance

ContextClipper is an open-source project that welcomes contributions from the
community.  This document describes how decisions are made and how to
participate.

## Principles

1. **Transparency** — all design discussions happen in public GitHub issues/PRs.
2. **Privacy-first** — no telemetry leaves the user's machine; all data is local.
3. **Minimal footprint** — we avoid unnecessary dependencies and keep the core small.
4. **Agent-safety** — filters must never silently drop error signals by default.

## Roles

### Maintainers

Maintainers have write access to the repository and are responsible for
reviewing pull requests, triaging issues, and cutting releases.

Current maintainers are listed in [`CODEOWNERS`](CODEOWNERS) (to be added).

### Contributors

Anyone who submits a pull request that is merged becomes a contributor.
Contributors are listed in [`CONTRIBUTORS.md`](CONTRIBUTORS.md).

## Decision-making

We use **lazy consensus**: a proposal is accepted if no maintainer objects
within 5 business days of the PR being marked `ready for review`.

For significant changes (new rule types, breaking API changes, security
architecture), open an RFC (see below) before writing code.

## RFC process

1. Copy [`rfcs/000-template.md`](rfcs/000-template.md) to `rfcs/NNN-short-title.md`.
2. Fill in the template and open a PR.
3. Discuss in the PR comments for at least 5 business days.
4. After consensus, merge the RFC and proceed with implementation.

Active and accepted RFCs are listed in [`rfcs/`](rfcs/).

## Versioning

We follow [Semantic Versioning](https://semver.org/):

- **Patch** (0.x.Y) — bug fixes, filter additions, documentation.
- **Minor** (0.X.0) — new features, new rule types, new CLI commands.
- **Major** (X.0.0) — breaking changes to the public API or filter format.

## Security

See [`SECURITY.md`](SECURITY.md) for the vulnerability disclosure policy.

## Code of Conduct

Be respectful and constructive.  We follow the
[Contributor Covenant](https://www.contributor-covenant.org/) v2.1.
