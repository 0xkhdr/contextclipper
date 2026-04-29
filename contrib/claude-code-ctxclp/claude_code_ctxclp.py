#!/usr/bin/env python3
"""claude-code-ctxclp — transparent ContextClipper adaptor for Claude Code.

This script is a drop-in pre-tool-use hook for Claude Code.  It intercepts
every Bash tool call, wraps the command with ``ctxclp run``, and silently
passes the result back.  If the compressed output contains a
``[CTXCLP:raw=<uuid>]`` footer and Claude Code later asks a clarifying question
that implies it needs more context, the hook can be configured to
automatically call ``ctxclp fetch <uuid>`` and re-inject the full output.

Installation
------------
    # After running `ctxclp install`, this hook is already active.
    # For manual installation, copy the hook to your Claude Code hooks directory:
    cp claude_code_ctxclp.py ~/.config/claude-code/hooks/pre_tool_use.py

Usage
-----
The hook reads a JSON event from stdin and writes the (possibly rewritten)
event to stdout.  No arguments needed; it is invoked by Claude Code automatically.

Recovery protocol
-----------------
Every ``ctxclp run`` output ends with:
    [CTXCLP:raw=<uuid>]

Claude Code can call ``ctxclp fetch <uuid>`` to retrieve the full uncompressed
output.  Example agent instruction::

    If you need full output, run: ctxclp fetch <uuid from [CTXCLP:raw=...]>

Environment variables
---------------------
CTXCLP_BIN          Path to ctxclp binary (default: ctxclp in PATH)
CTXCLP_STREAM       Set to "1" to use streaming mode (--stream flag)
CTXCLP_MAX_TOKENS   Maximum tokens for compressed output
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys

_CTXCLP_BIN = os.environ.get("CTXCLP_BIN", shutil.which("ctxclp") or "ctxclp")
_STREAM = os.environ.get("CTXCLP_STREAM", "0") == "1"
_MAX_TOKENS = os.environ.get("CTXCLP_MAX_TOKENS", "")

# Regex to extract the machine footer UUID
MACHINE_FOOTER_RE = re.compile(r"\[CTXCLP:raw=([0-9a-f]+)\]")


def rewrite_event(event: dict) -> dict:
    """Rewrite a Bash tool call to use ctxclp run."""
    tool = event.get("tool_name", event.get("tool", ""))
    if tool not in ("Bash", "bash", "shell", "run_command"):
        return event

    inp = event.get("tool_input", event.get("input", {}))
    cmd = inp.get("command", inp.get("cmd", ""))

    if not cmd:
        return event

    # Already wrapped — don't double-wrap
    if "ctxclp run" in cmd or os.environ.get("CTXCLP_INTERNAL") == "1":
        return event

    parts = [_CTXCLP_BIN, "run"]
    if _STREAM:
        parts.append("--stream")
    if _MAX_TOKENS:
        parts.extend(["--max-tokens", _MAX_TOKENS])
    parts.extend(["--", cmd])

    inp["command"] = " ".join(parts)
    event["tool_input"] = inp
    return event


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    rewritten = rewrite_event(event)
    sys.stdout.write(json.dumps(rewritten))


if __name__ == "__main__":
    main()
