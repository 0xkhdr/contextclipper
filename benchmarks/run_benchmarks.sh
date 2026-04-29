#!/usr/bin/env bash
# run_benchmarks.sh — run the full ContextClipper benchmark suite
#
# Usage:
#   ./benchmarks/run_benchmarks.sh [--json]
#
# Exits 0 if all benchmarks pass their minimum reduction targets.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== ContextClipper Benchmarks ==="
echo "Project: $PROJECT_ROOT"
echo ""

# Prefer .venv if it exists, otherwise rely on PATH
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON="$(command -v python3 || command -v python)"
fi
echo "Python: $PYTHON"
echo ""

cd "$PROJECT_ROOT"

"$PYTHON" benchmarks/benchmark_runner.py "$@"
