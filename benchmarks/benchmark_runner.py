#!/usr/bin/env python3
"""ContextClipper benchmark runner.

Measures token savings for a set of realistic command-output traces.  Each
trace is a JSON file in ``benchmarks/traces/`` containing a ``command`` and
``output`` field.

The runner compresses each trace with ContextClipper and reports:
- Original token count (chars / 4)
- Compressed token count
- Reduction percentage
- Whether error/signal lines were preserved

Usage::

    python benchmarks/benchmark_runner.py [--json] [--traces-dir <path>]

Exit code is 0 if all benchmarks meet their ``min_reduction_pct`` targets.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running from project root without installation
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


def _approx_tokens(text: str) -> int:
    """Approximate token count: 1 token ≈ 4 characters."""
    return max(1, len(text) // 4)


def run_benchmarks(traces_dir: Path) -> list[dict]:
    from contextclipper.engine.filters import compress_output  # type: ignore

    results = []
    for trace_file in sorted(traces_dir.glob("*.json")):
        with open(trace_file) as f:
            trace = json.load(f)

        name = trace.get("name", trace_file.stem)
        command = trace.get("command", "")
        output = trace.get("output", "")
        exit_code = trace.get("exit_code", 0)
        min_reduction = trace.get("min_reduction_pct", 30)

        t0 = time.monotonic()
        cr = compress_output(command, output, exit_code, dry_run=True)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 2)

        orig_tokens = _approx_tokens(output)
        comp_tokens = _approx_tokens(cr.compressed)
        reduction_pct = round((1 - comp_tokens / orig_tokens) * 100, 1) if orig_tokens else 0.0

        dropped_errors = cr.dropped_error_lines or []
        passed = reduction_pct >= min_reduction

        results.append({
            "name": name,
            "command": command,
            "original_tokens": orig_tokens,
            "compressed_tokens": comp_tokens,
            "reduction_pct": reduction_pct,
            "min_reduction_pct": min_reduction,
            "original_lines": cr.original_lines,
            "kept_lines": cr.kept_lines,
            "filter_used": cr.filter_name or "fallback",
            "elapsed_ms": elapsed_ms,
            "error_lines_dropped": len(dropped_errors),
            "dropped_error_samples": dropped_errors[:3],
            "passed": passed,
        })

    return results


def print_table(results: list[dict]) -> None:
    cols = [
        ("Benchmark", "name", 30),
        ("Filter", "filter_used", 12),
        ("Orig tokens", "original_tokens", 12),
        ("Comp tokens", "compressed_tokens", 12),
        ("Reduction", "reduction_pct", 10),
        ("Target", "min_reduction_pct", 8),
        ("Status", "passed", 8),
    ]
    header = "  ".join(f"{label:<{w}}" for label, _, w in cols)
    print(header)
    print("-" * len(header))
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        row = "  ".join(
            f"{str(r[key]):<{w}}"
            for _, key, w in cols[:-1]
        )
        print(f"{row}  {status}")
        if r["error_lines_dropped"] > 0:
            print(f"  WARNING: {r['error_lines_dropped']} error-signal line(s) were dropped")
            for s in r["dropped_error_samples"]:
                print(f"    - {s[:80]}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="ContextClipper benchmark runner")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output results as JSON")
    parser.add_argument(
        "--traces-dir",
        default=str(Path(__file__).parent / "traces"),
        help="Directory containing .json trace files",
    )
    args = parser.parse_args()

    traces_dir = Path(args.traces_dir)
    if not traces_dir.exists():
        print(f"ERROR: traces directory not found: {traces_dir}", file=sys.stderr)
        return 1

    results = run_benchmarks(traces_dir)

    if not results:
        print("No trace files found.", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\nContextClipper Benchmark Results — {len(results)} trace(s)\n")
        print_table(results)

        total_orig = sum(r["original_tokens"] for r in results)
        total_comp = sum(r["compressed_tokens"] for r in results)
        overall_reduction = round((1 - total_comp / total_orig) * 100, 1) if total_orig else 0
        passed = sum(1 for r in results if r["passed"])

        print(f"Overall: {total_orig} → {total_comp} tokens "
              f"(-{overall_reduction}%) across all traces")
        print(f"Tests passed: {passed}/{len(results)}")

    all_passed = all(r["passed"] for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
