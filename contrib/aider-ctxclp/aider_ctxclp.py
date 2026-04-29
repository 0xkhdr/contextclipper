#!/usr/bin/env python3
"""aider-ctxclp — ContextClipper wrapper for Aider.

Usage
-----
Replace direct aider invocations with this wrapper, or set the AIDER_SHELL_CMD
environment variable:

    export AIDER_SHELL_CMD="python /path/to/aider_ctxclp.py"

Or patch Aider's config to run commands through ctxclp:

    # ~/.aider.conf.yml
    shell_cmd: ctxclp run --

Alternatively, source the provided aider_ctxclp_activate.sh in your shell
profile to auto-wrap aider commands.

How it works
------------
This script wraps each shell command invocation with ``ctxclp run``.  The
compressed output is returned to Aider.  If Aider's response indicates it
needs more context (heuristically detected), the script automatically calls
``ctxclp fetch <uuid>`` and appends the full output.

Recovery protocol
-----------------
Every ctxclp-compressed output ends with ``[CTXCLP:raw=<uuid>]``.  Add this
to your Aider system prompt to teach it to recover::

    When you see [CTXCLP:raw=<uuid>] in tool output, run:
    ctxclp fetch <uuid>
    to retrieve the full uncompressed output.

Environment variables
---------------------
CTXCLP_BIN          Path to ctxclp binary (default: ctxclp in PATH)
CTXCLP_MAX_TOKENS   Maximum tokens for compressed output (default: 8000)
CTXCLP_AUTO_FETCH   Set to "1" to auto-fetch when output looks incomplete (default: 0)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

_CTXCLP_BIN = os.environ.get("CTXCLP_BIN", shutil.which("ctxclp") or "ctxclp")
_MAX_TOKENS = int(os.environ.get("CTXCLP_MAX_TOKENS", "8000"))
_AUTO_FETCH = os.environ.get("CTXCLP_AUTO_FETCH", "0") == "1"

MACHINE_FOOTER_RE = re.compile(r"\[CTXCLP:raw=([0-9a-f]+)\]")

# Phrases that suggest the agent wants more output
_NEED_MORE_PATTERNS = re.compile(
    r"\b(need more|show me more|full output|see the rest|truncated|more details"
    r"|can you show|what else|more information)\b",
    re.IGNORECASE,
)


def run_with_compression(command: str) -> tuple[str, int]:
    """Run command through ctxclp run and return (output, exit_code)."""
    ctxclp_cmd = [_CTXCLP_BIN, "run", "--max-tokens", str(_MAX_TOKENS), "--", command]
    result = subprocess.run(ctxclp_cmd, capture_output=True, text=True)
    output = result.stdout
    if result.stderr:
        output += result.stderr
    return output, result.returncode


def fetch_full(uuid: str) -> str:
    """Fetch the full uncompressed output for a given UUID."""
    result = subprocess.run(
        [_CTXCLP_BIN, "fetch", uuid],
        capture_output=True, text=True,
    )
    return result.stdout


def maybe_auto_fetch(output: str, agent_response: str = "") -> str:
    """If auto-fetch is enabled and the response asks for more, inject full output."""
    if not _AUTO_FETCH:
        return output
    m = MACHINE_FOOTER_RE.search(output)
    if not m:
        return output
    if _NEED_MORE_PATTERNS.search(agent_response):
        uuid = m.group(1)
        full = fetch_full(uuid)
        return output + "\n\n=== Full output (auto-fetched) ===\n" + full
    return output


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command>", file=sys.stderr)
        sys.exit(1)

    command = " ".join(sys.argv[1:])
    output, code = run_with_compression(command)
    sys.stdout.write(output)
    sys.exit(code)


if __name__ == "__main__":
    main()
