"""ctxclp CLI entry point: run, build, install, serve, stats, filter, hook-rewrite."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()


def _get_graph():  # type: ignore[no-untyped-def]
    from contextclipper.engine.graph import GraphDB  # type: ignore[import-not-found]
    db_path = Path(os.environ.get("CTXCLP_DB", str(Path.home() / ".local/share/contextclipper/graph.db")))
    return GraphDB(db_path)


def _get_stats():  # type: ignore[no-untyped-def]
    from contextclipper.engine.stats import StatsDB  # type: ignore[import-not-found]
    return StatsDB()


@click.group()
@click.version_option("0.1.0", prog_name="ctxclp")
def cli() -> None:
    """ContextClipper — universal token optimizer for AI coding agents."""


# ── ctxclp run <command> ──────────────────────────────────────────────────────

@cli.command(name="run", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("command", nargs=-1, required=True)
@click.option("--raw", is_flag=True, help="Print raw output without compression")
@click.option("--dry-run", is_flag=True, help="Show what would be removed alongside the compressed output")
@click.option("--max-tokens", type=int, default=None, help="Tail-truncate the kept output so total tokens ≤ N")
def cmd_run(command: tuple, raw: bool, dry_run: bool, max_tokens: int | None) -> None:
    """Execute a shell command and print compressed output."""
    # Prevent recursive hook activation
    env = os.environ.copy()
    env["CTXCLP_INTERNAL"] = "1"
    env.pop("CTXCLP_HOOK_ACTIVE", None)

    full_cmd = " ".join(command)

    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        console.print("[red]Command timed out after 300s[/red]")
        sys.exit(1)

    combined = result.stdout
    if result.stderr:
        combined += "\n" + result.stderr

    if raw:
        sys.stdout.write(combined)
        sys.exit(result.returncode)

    from contextclipper.engine.filters import compress_output  # type: ignore[import-not-found]
    from contextclipper.engine.logging import get_logger  # type: ignore[import-not-found]
    from contextclipper.engine.tee import save_raw  # type: ignore[import-not-found]
    log = get_logger()

    raw_id = None
    if result.returncode != 0:
        raw_id = save_raw(full_cmd, combined, result.returncode)

    cr = compress_output(
        full_cmd, combined, result.returncode, raw_id,
        dry_run=dry_run, max_tokens=max_tokens,
    )

    try:
        stats = _get_stats()
        stats.record(
            command=full_cmd,
            original_lines=cr.original_lines,
            kept_lines=cr.kept_lines,
            exit_code=result.returncode,
            bytes_in=cr.bytes_in,
            bytes_out=cr.bytes_out,
            elapsed_ms=cr.elapsed_ms,
        )
        stats.close()
    except Exception as e:
        log.warning("Failed to record stats: %s", e)

    sys.stdout.write(str(cr) + "\n")
    if dry_run and cr.removed_lines:
        sys.stderr.write(f"\n[ctxclp dry-run] {len(cr.removed_lines)} line(s) would be removed:\n")
        for ln_no, content in cr.removed_lines[:50]:
            sys.stderr.write(f"  {ln_no}: {content}\n")
        if len(cr.removed_lines) > 50:
            sys.stderr.write(f"  …and {len(cr.removed_lines) - 50} more\n")
    sys.exit(result.returncode)


# ── ctxclp build ─────────────────────────────────────────────────────────────

@cli.command(name="build")
@click.argument("project_root", default=".", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Force full re-index (ignore cached hashes)")
def cmd_build(project_root: str, force: bool) -> None:
    """Index the project code graph (PHP files → SQLite)."""
    root = Path(project_root).resolve()
    with console.status(f"[bold green]Indexing {root}…"):
        graph = _get_graph()
        counts = graph.build(root, force=force)
        graph.close()
    console.print(
        f"[green]Done:[/green] {counts['new']} new, {counts['updated']} updated, "
        f"{counts['skipped']} unchanged — {counts['total']} total files "
        f"in {counts.get('elapsed_ms', '?')}ms"
    )


# ── ctxclp install / uninstall ────────────────────────────────────────────────

@cli.command(name="install")
@click.option("--agent", multiple=True, help="Specific agent(s) to install for (default: auto-detect)")
@click.option("--uninstall", is_flag=True, help="Remove all injected hooks and configs")
def cmd_install(agent: tuple, uninstall: bool) -> None:
    """Auto-detect AI agents and install shell hooks + MCP config."""
    from contextclipper.cli.install import detect_agents, install_all  # type: ignore[import-not-found]

    agents_list = list(agent) if agent else None
    if not agents_list and not uninstall:
        detected = detect_agents()
        if not detected:
            console.print("[yellow]No supported AI agents detected. Supported: claude-code, cursor, windsurf, cline, gemini-cli, codex[/yellow]")
            return
        console.print(f"[bold]Detected agents:[/bold] {', '.join(detected)}")
        agents_list = detected

    results = install_all(agents_list, uninstall=uninstall)
    action = "Uninstalled" if uninstall else "Installed"
    for ag, msg in results.items():
        status = "[red]ERROR[/red]" if msg.startswith("ERROR") else "[green]OK[/green]"
        console.print(f"  {status}  {ag}: {msg}")
    console.print(f"\n[bold]{action} for {len(results)} agent(s).[/bold]")
    if not uninstall:
        console.print("[dim]Restart your AI tool for changes to take effect.[/dim]")


# ── ctxclp serve (MCP) ────────────────────────────────────────────────────────

@cli.command(name="serve")
def cmd_serve() -> None:
    """Start the ContextClipper MCP server (stdio transport)."""
    from contextclipper.mcp.server import run  # type: ignore[import-not-found]
    asyncio.run(run())


# ── ctxclp stats ─────────────────────────────────────────────────────────────

@cli.command(name="stats")
@click.option("--days", default=7, show_default=True, help="Number of days to report")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_stats(days: int, as_json: bool) -> None:
    """Show local token savings and usage statistics."""
    try:
        stats = _get_stats()
        summary = stats.summary(days=days)
        stats.close()
    except Exception as e:
        console.print(f"[red]Error reading stats: {e}[/red]")
        return

    if as_json:
        console.print(json.dumps(summary, indent=2))
        return

    console.print(f"\n[bold]ContextClipper Stats — last {days} day(s)[/bold]")
    console.print(f"  Commands run:    {summary['total_commands']}")
    console.print(f"  Lines original:  {summary['total_original_lines']}")
    console.print(f"  Lines kept:      {summary['total_kept_lines']}")
    console.print(f"  Reduction:       [green]{summary['reduction_pct']}%[/green]")

    if summary["top_commands"]:
        table = Table(title="Top Commands", show_header=True)
        table.add_column("Command", style="cyan")
        table.add_column("Count", justify="right")
        for row in summary["top_commands"]:
            table.add_row(row["command"][:60], str(row["count"]))
        console.print(table)


# ── ctxclp validate ──────────────────────────────────────────────────────────

@cli.command(name="validate")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_validate(as_json: bool) -> None:
    """Run a self-check on the filter registry and the code graph."""
    from contextclipper.engine import get_registry  # type: ignore[import-not-found]

    reg = get_registry()
    filt_report = reg.validate()
    try:
        graph = _get_graph()
        graph_report = graph.validate()
        graph.close()
    except Exception as e:
        graph_report = {"ok": False, "files_indexed": 0, "problems": [str(e)]}

    overall = {
        "ok": filt_report["ok"] and graph_report["ok"],
        "filters": filt_report,
        "graph": graph_report,
    }
    if as_json:
        console.print(json.dumps(overall, indent=2))
        sys.exit(0 if overall["ok"] else 1)

    if overall["ok"]:
        console.print(f"[green]✓ ContextClipper healthy[/green] — {filt_report['filters']} filters, {graph_report['files_indexed']} files indexed")
        sys.exit(0)
    console.print("[red]✗ ContextClipper validation failed[/red]")
    for p in filt_report["problems"]:
        console.print(f"  filter: {p}")
    for p in graph_report["problems"]:
        console.print(f"  graph: {p}")
    sys.exit(1)


# ── ctxclp hook-rewrite (used internally by shell hooks) ─────────────────────

@cli.command(name="hook-rewrite", hidden=True)
def cmd_hook_rewrite() -> None:
    """Read a hook event from stdin, rewrite bash command to use ctxclp run."""
    import json as _json

    # Claude Code / Cursor pass the tool input as JSON on stdin
    try:
        event = _json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # not a JSON event — pass through

    # Only intercept Bash tool and skip if already inside ctxclp
    tool = event.get("tool_name", event.get("tool", ""))
    if tool not in ("Bash", "bash", "shell", "run_command"):
        sys.exit(0)

    inp = event.get("tool_input", event.get("input", {}))
    cmd = inp.get("command", inp.get("cmd", ""))

    if not cmd or os.environ.get("CTXCLP_INTERNAL") == "1":
        sys.exit(0)

    ctxclp_bin = sys.argv[0]  # path to this ctxclp binary
    inp["command"] = f"CTXCLP_INTERNAL=1 {ctxclp_bin} run {cmd}"
    event["tool_input"] = inp
    sys.stdout.write(_json.dumps(event))


# ── ctxclp filter new <name> ─────────────────────────────────────────────────

@cli.group(name="filter")
def cmd_filter() -> None:
    """Manage ContextClipper command filters."""


@cmd_filter.command(name="new")
@click.argument("name")
@click.argument("command_pattern")
def filter_new(name: str, command_pattern: str) -> None:
    """Scaffold a new TOML filter file for a command."""
    user_filters = Path.home() / ".config" / "contextclipper" / "filters"
    user_filters.mkdir(parents=True, exist_ok=True)
    out = user_filters / f"{name}.toml"
    template = f"""[filter]
name = "{name}"
description = "Custom filter for {command_pattern}"

[[filter.patterns]]
match_command = "{command_pattern}"

[[filter.rules]]
# Drop noisy lines
type = "drop_matching"
pattern = "^# TODO: add your drop pattern here"

[[filter.rules]]
# Keep important lines
type = "keep_matching"
pattern = "^(ERROR|WARN|✓|FAIL)"
priority = 10
"""
    out.write_text(template)
    console.print(f"[green]Created:[/green] {out}")
    console.print("Edit the file to add your filter rules, then test with: ctxclp run <your-command>")


# ── ctxclp hook test ─────────────────────────────────────────────────────────

@cli.group(name="hook")
def cmd_hook() -> None:
    """Hook management utilities."""


@cmd_hook.command(name="test")
@click.argument("command", default="git status")
def hook_test(command: str) -> None:
    """Simulate the hook chain for a given command."""
    from contextclipper.engine.filters import compress_output  # type: ignore[import-not-found]

    console.print(f"[bold]Simulating hook for:[/bold] {command}")
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
    cr = compress_output(command, combined, result.returncode)
    console.print(str(cr))
    console.print(f"\n[dim]Reduction: {cr.reduction_pct}% ({cr.original_lines} → {cr.kept_lines} lines)[/dim]")


if __name__ == "__main__":
    cli()
