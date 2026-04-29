"""Best-effort redaction of secrets in command text and command output.

Redaction is a defense-in-depth measure for the local stats DB and the on-disk tee
store. It is NOT a security boundary — patterns evolve and false negatives are expected.
Callers that handle highly sensitive material should disable persistence entirely
(`CTXCLP_DISABLE_TEE=1`, `CTXCLP_DISABLE_STATS=1`).
"""

from __future__ import annotations

import re
from typing import Final

# CLI-flag style: --token=xxx, --password xxx, -p=xxx
_FLAG_SECRET = re.compile(
    r"(?P<flag>(?:--?|/)(?:token|password|passwd|pwd|secret|api[-_]?key|access[-_]?key|auth)[\w-]*)"
    r"(?P<sep>[=\s]+)"
    r"(?P<val>[^\s'\"]+)",
    re.IGNORECASE,
)

# KEY=value env-style for sensitive-looking names
_ENV_SECRET = re.compile(
    r"(?P<key>(?:[A-Z_][A-Z0-9_]*_)?(?:TOKEN|PASSWORD|SECRET|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|AUTH))"
    r"(?P<sep>=)"
    r"(?P<val>[^\s'\"]+)",
)

# Authorization header: "Authorization: Bearer xxx" / "Basic xxx"
_AUTH_HEADER = re.compile(
    r"(?P<prefix>Authorization\s*:\s*(?:Bearer|Basic|Token)\s+)"
    r"(?P<val>[A-Za-z0-9+/=._\-]+)",
    re.IGNORECASE,
)

# AWS-style access keys
_AWS_KEY = re.compile(r"\b(?P<val>AKIA[0-9A-Z]{16})\b")
_AWS_SECRET = re.compile(r"(?<![A-Za-z0-9+/])(?P<val>[A-Za-z0-9+/]{40})(?![A-Za-z0-9+/=])")

# GitHub / generic PAT prefixes
_TOKEN_PREFIXES: Final = re.compile(
    r"\b(?P<val>(?:ghp|gho|ghu|ghs|ghr|github_pat|glpat|xox[baprs])[_A-Za-z0-9-]{16,})\b",
)

# JSON-ish "field": "value" for sensitive field names
_JSON_SECRET = re.compile(
    r"(?P<key>\"(?:token|password|secret|api[_-]?key|access[_-]?key|auth)\"\s*:\s*)"
    r"(?P<quote>\")(?P<val>[^\"]+)(?P<endq>\")",
    re.IGNORECASE,
)

REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """Return ``text`` with high-confidence secrets masked.

    Patterns intentionally err on the side of false-positives — never on
    false-negatives — so that a few unrelated 40-char base64 strings may also be
    redacted. That is acceptable for stats / tee storage which is
    diagnostic-only.
    """
    if not text:
        return text
    text = _FLAG_SECRET.sub(lambda m: f"{m.group('flag')}{m.group('sep')}{REDACTED}", text)
    text = _ENV_SECRET.sub(lambda m: f"{m.group('key')}{m.group('sep')}{REDACTED}", text)
    text = _AUTH_HEADER.sub(lambda m: f"{m.group('prefix')}{REDACTED}", text)
    text = _JSON_SECRET.sub(
        lambda m: f"{m.group('key')}{m.group('quote')}{REDACTED}{m.group('endq')}", text,
    )
    text = _TOKEN_PREFIXES.sub(REDACTED, text)
    text = _AWS_KEY.sub(REDACTED, text)
    return text


def redact_command(command: str) -> str:
    """Redact a command string before persisting it. Same rules as :func:`redact_text`."""
    return redact_text(command)
