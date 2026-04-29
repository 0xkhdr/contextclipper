"""Raw output tee storage: saves command output for later full retrieval.

Storage hardening:

- Tee directory is created with mode 0o700; files are written with mode 0o600
  to prevent other local users from reading captured output.
- Command + output are redacted by :mod:`contextclipper.core.redact` before being
  written, masking common tokens / passwords. Disable redaction with
  ``CTXCLP_TEE_REDACT=0`` (NOT recommended). Disable persistence entirely with
  ``CTXCLP_DISABLE_TEE=1``.
- Output IDs are generated from a cryptographic random source rather than a
  predictable hash of (command, time).
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from .logging import get_logger
from .redact import redact_command, redact_text

log = get_logger()


def _xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


TEE_DIR = _xdg_data_home() / "contextclipper" / "tee"
TTL_SECONDS = int(os.environ.get("CTXCLP_TEE_TTL", 86400))
MAX_SIZE_BYTES = int(os.environ.get("CTXCLP_TEE_MAX_BYTES", 100 * 1024 * 1024))


def _is_disabled() -> bool:
    return os.environ.get("CTXCLP_DISABLE_TEE") == "1"


def _redaction_enabled() -> bool:
    return os.environ.get("CTXCLP_TEE_REDACT", "1") != "0"


def _tee_dir() -> Path:
    TEE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(TEE_DIR, 0o700)
    except OSError:
        pass
    return TEE_DIR


def save_raw(command: str, raw_output: str, exit_code: int) -> str | None:
    """Save raw output to disk. Returns the output_id, or ``None`` if disabled."""
    if _is_disabled():
        return None
    uid = secrets.token_hex(8)
    path = _tee_dir() / f"{uid}.log"
    if _redaction_enabled():
        command = redact_command(command)
        raw_output = redact_text(raw_output)
    body = (
        f"# command: {command}\n"
        f"# exit_code: {exit_code}\n"
        f"# saved: {time.time()}\n"
        f"\n{raw_output}"
    )
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
    except Exception as e:
        log.warning("Failed to write tee file: %s", e)
        return None
    _cleanup()
    return uid


def get_raw(output_id: str) -> str | None:
    """Retrieve raw output by ID. Returns None if expired or not found.

    The ID is validated to avoid path-traversal: only hex IDs are accepted.
    """
    if not output_id or not all(c in "0123456789abcdef" for c in output_id):
        return None
    path = _tee_dir() / f"{output_id}.log"
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > TTL_SECONDS:
        path.unlink(missing_ok=True)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("Failed to read tee file %s: %s", path, e)
        return None


def _cleanup() -> None:
    """Remove expired files and enforce total size cap."""
    tee = _tee_dir()
    now = time.time()
    files = sorted(tee.glob("*.log"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files:
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age > TTL_SECONDS or total > MAX_SIZE_BYTES:
            try:
                total -= p.stat().st_size
            except OSError:
                pass
            p.unlink(missing_ok=True)
