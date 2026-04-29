"""MCP tool implementations — thin wrappers over the engine."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from contextclipper.shell.engine import compress_output  # type: ignore[import-not-found]
from contextclipper.graph.builder import GraphDB  # type: ignore[import-not-found]
from contextclipper.core.stats import StatsDB  # type: ignore[import-not-found]
from contextclipper.core.tee import get_raw, save_raw  # type: ignore[import-not-found]


def tool_get_file(
    graph: GraphDB,
    path: str,
    mode: str = "summary_only",
    project_root: str = ".",
) -> str:
    """Return symbol summary for a single file.

    Path is sanitized: absolute paths must resolve to inside ``project_root``,
    and ``..`` traversal is rejected.
    """
    root = Path(project_root).resolve()
    p = Path(path)
    try:
        if p.is_absolute():
            resolved = p.resolve()
            rel = str(resolved.relative_to(root))
        else:
            if ".." in p.parts:
                return f"Path `{path}` is invalid: parent-directory traversal is not allowed."
            rel = str(p)
    except ValueError:
        return f"Path `{path}` is outside the project root."
    return graph.get_file_symbols(rel, mode=mode)  # type: ignore[arg-type]


def tool_search_symbols(graph: GraphDB, query: str, kind: str | None = None) -> list[dict]:
    return graph.search_symbols(query, kind=kind)


def tool_get_affected(graph: GraphDB, files: list[str]) -> dict[str, list[str]]:
    return graph.get_affected(files)


def tool_rebuild_graph(graph: GraphDB, project_root: str = ".") -> dict[str, Any]:
    root = Path(project_root).resolve()
    return graph.build(root)


def tool_run_shell(
    command: str,
    compression_level: str = "auto",
    stats_db: StatsDB | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Execute a shell command and return compressed output.

    ``compression_level``:
      - ``"none"``       — return raw output, no filter applied
      - ``"minimal"``   — only strip ANSI / blank lines (no rules)
      - ``"auto"``      (default) — full TOML rule engine
      - ``"aggressive"`` — auto + tail-keep to ~2 k tokens unless ``max_tokens`` set
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 120s", "exit_code": -1}
    except Exception as e:
        return {"error": e.__class__.__name__ + ": " + str(e), "exit_code": -1}

    combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
    raw_id = save_raw(command, combined, result.returncode)

    if compression_level == "none":
        return {
            "output": combined,
            "exit_code": result.returncode,
            "original_lines": combined.count("\n"),
            "kept_lines": combined.count("\n"),
            "reduction_pct": 0.0,
            "raw_output_id": raw_id,
        }

    effective_max = max_tokens
    if compression_level == "aggressive" and effective_max is None:
        effective_max = 2000

    cr = compress_output(
        command, combined, result.returncode, raw_id, max_tokens=effective_max,
    )

    if stats_db:
        stats_db.record(
            command=command,
            original_lines=cr.original_lines,
            kept_lines=cr.kept_lines,
            exit_code=result.returncode,
            bytes_in=cr.bytes_in,
            bytes_out=cr.bytes_out,
            elapsed_ms=cr.elapsed_ms,
            filter_name=cr.filter_name,
            strategy_name=cr.strategy_name,
        )

    return {
        "output": str(cr),
        "exit_code": result.returncode,
        "original_lines": cr.original_lines,
        "kept_lines": cr.kept_lines,
        "reduction_pct": cr.reduction_pct,
        "bytes_in": cr.bytes_in,
        "bytes_out": cr.bytes_out,
        "elapsed_ms": cr.elapsed_ms,
        "raw_output_id": raw_id,
        "truncated": cr.truncated,
        "filter_name": cr.filter_name,
        "metadata": cr.metadata_footer(),
    }


def tool_get_raw_output(output_id: str, stats_db: StatsDB | None = None) -> str:
    """Retrieve full raw output and record the fetch in the stats DB.

    When an agent fetches the full output after receiving compressed output,
    this is tracked so filter quality can be improved over time.
    """
    raw = get_raw(output_id)
    if raw is None:
        return f"Output ID `{output_id}` not found or expired (24h TTL)."
    if stats_db:
        try:
            stats_db.record_raw_pull(output_id)
        except Exception:
            pass
    return raw


def tool_get_overview(graph: GraphDB, detail: str = "compact") -> str:
    return graph.get_overview(detail=detail)  # type: ignore[arg-type]


def tool_get_stats(stats_db: StatsDB, days: int = 7) -> dict:
    return stats_db.summary(days=days)
