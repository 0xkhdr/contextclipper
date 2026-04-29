"""Best-effort redaction of secrets in command text and command output.

Redaction is a defense-in-depth measure for the local stats DB and the on-disk tee
store. It is NOT a security boundary — patterns evolve and false negatives are expected.
Callers that handle highly sensitive material should disable persistence entirely
(``CTXCLP_DISABLE_TEE=1``, ``CTXCLP_DISABLE_STATS=1``).

Two complementary layers:
1. Pattern-based: named secrets (flags, env vars, auth headers, known token prefixes).
2. Entropy-based: high-entropy tokens (≥32 chars, Shannon entropy > 4.5 bits/char)
   that are likely random secrets regardless of context.
"""

from __future__ import annotations

import math
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

# Entropy-based: tokens of 32+ chars that are mostly base64/hex alphabet
_HIGH_ENTROPY_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9+/=_\-])"
    r"([A-Za-z0-9+/=_\-]{32,})"
    r"(?![A-Za-z0-9+/=_\-])",
)

REDACTED = "[REDACTED]"

# Threshold: Shannon entropy > 4.5 bits/char is indicative of a random secret.
# Typical English text is ~3.5–4.0; Base64-encoded secrets are 5.5–6.0.
_ENTROPY_THRESHOLD = 4.5


def _shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of ``s`` in bits per character."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((f / n) * math.log2(f / n) for f in freq.values())


def _redact_high_entropy(text: str) -> str:
    """Redact tokens that are long and have high Shannon entropy."""
    def maybe_redact(m: re.Match) -> str:
        token = m.group(1)
        if _shannon_entropy(token) > _ENTROPY_THRESHOLD:
            return REDACTED
        return token
    return _HIGH_ENTROPY_CANDIDATE.sub(maybe_redact, text)


def redact_text(text: str) -> str:
    """Return ``text`` with high-confidence secrets masked.

    Applies pattern-based rules first, then entropy-based detection as a
    catch-all for secrets that don't match a known format. Intentionally errs
    on the side of false-positives — a few innocent base64 blobs may be
    redacted, but no known secret format should pass through unmasked.
    That trade-off is acceptable for stats / tee storage which is
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
    # Entropy-based pass: catches anything the pattern layer missed.
    text = _redact_high_entropy(text)
    return text


def redact_command(command: str) -> str:
    """Redact a command string before persisting it. Same rules as :func:`redact_text`."""
    return redact_text(command)
