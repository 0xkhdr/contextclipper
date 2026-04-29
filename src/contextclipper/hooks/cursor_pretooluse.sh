#!/usr/bin/env bash
# ContextClipper preToolUse hook for Cursor.
# Same pattern as the Claude Code hook but for Cursor's hook format.

set -euo pipefail

if [[ "${CTXCLP_INTERNAL:-0}" == "1" ]]; then
    cat
    exit 0
fi

CTXCLP_BIN="${CTXCLP_BIN:-ctxclp}"
input=$(cat)

python3 -c "
import json, sys, os
d = json.load(sys.stdin)
# Cursor may use 'tool' or 'tool_name'
tool = d.get('tool', d.get('tool_name', ''))
if tool.lower() not in ('bash', 'shell', 'run_terminal_command'):
    print(json.dumps(d))
    sys.exit(0)
inp = d.get('input', d.get('tool_input', {}))
cmd = inp.get('command', inp.get('cmd', ''))
if cmd and not cmd.startswith('ctxclp'):
    ctxclp_bin = os.environ.get('CTXCLP_BIN', 'ctxclp')
    inp['command'] = f'CTXCLP_INTERNAL=1 {ctxclp_bin} run -- {cmd}'
d['input'] = inp
print(json.dumps(d))
" <<< "$input"
