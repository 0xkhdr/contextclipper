#!/usr/bin/env bash
# ContextClipper PreToolUse hook for Claude Code.
# Rewrites Bash tool commands to pipe through ctxclp run.
# This script is invoked by Claude Code's hook system on every Bash tool call.
#
# Environment variables set by Claude Code are passed via stdin as JSON.
# We parse and rewrite the command, then emit modified JSON on stdout.

set -euo pipefail

# Guard against recursive rewrites
if [[ "${CTXCLP_INTERNAL:-0}" == "1" ]]; then
    # Pass stdin through unchanged
    cat
    exit 0
fi

CTXCLP_BIN="${CTXCLP_BIN:-ctxclp}"

# Read the hook event JSON from stdin
input=$(cat)

# Extract tool_name using python3 (available everywhere)
tool_name=$(echo "$input" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('tool_name', d.get('tool', '')))
" 2>/dev/null || echo "")

# Only rewrite Bash tool calls
if [[ "$tool_name" != "Bash" ]]; then
    echo "$input"
    exit 0
fi

# Rewrite command to use ctxclp run
echo "$input" | python3 -c "
import json, sys, os
d = json.load(sys.stdin)
tool_input = d.get('tool_input', d.get('input', {}))
cmd = tool_input.get('command', tool_input.get('cmd', ''))
if cmd and not cmd.startswith('ctxclp'):
    ctxclp_bin = os.environ.get('CTXCLP_BIN', 'ctxclp')
    tool_input['command'] = f'CTXCLP_INTERNAL=1 {ctxclp_bin} run -- {cmd}'
    d['tool_input'] = tool_input
print(json.dumps(d))
"
