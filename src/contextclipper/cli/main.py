"""ctxclp CLI entry point: run, build, install, serve, stats, audit, filter, hook, doctor."""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import click
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()
err_console = Console(stderr=True)

_DEFAULT_TIMEOUT = int(os.environ.get("CTXCLP_COMMAND_TIMEOUT", 300))


def _get_graph():  # type: ignore[no-untyped-def]
    from contextclipper.engine.graph import GraphDB  # type: ignore[import-not-found]
    db_path = Path(os.environ.get("CTXCLP_DB", str(Path.home() / ".local/share/contextclipper/graph.db")))
    return GraphDB(db_path)


def _get_stats():  # type: ignore[no-untyped-def]
    from contextclipper.engine.stats import StatsDB  # type: ignore[import-not-found]
    return StatsDB()


@click.group()
@click.version_option("0.3.0", prog_name="ctxclp")
def cli() -> None:
    """ContextClipper — universal token optimizer for AI coding agents."""


# ── ctxclp run <command> ──────────────────────────────────────────────────────

@cli.command(name="run", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("command", nargs=-1, required=True)
@click.option("--raw", is_flag=True, help="Print raw output without compression")
@click.option("--dry-run", is_flag=True, help="Show what would be removed alongside the compressed output")
@click.option("--max-tokens", type=int, default=None, help="Tail-keep the output so total tokens ≤ N")
@click.option("--timeout", type=int, default=None, help=f"Command timeout in seconds (default: {_DEFAULT_TIMEOUT})")
def cmd_run(command: tuple, raw: bool, dry_run: bool, max_tokens: int | None, timeout: int | None) -> None:
    """Execute a shell command and print compressed output."""
    env = os.environ.copy()
    env["CTXCLP_INTERNAL"] = "1"
    env.pop("CTXCLP_HOOK_ACTIVE", None)

    full_cmd = " ".join(command)
    effective_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT

    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired:
        err_console.print(f"[red]Command timed out after {effective_timeout}s[/red]")
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
            filter_name=cr.filter_name,
            strategy_name=cr.strategy_name,
        )
        stats.close()
    except Exception as e:
        log.warning("Failed to record stats: %s", e)

    # For structured (JSON) output: write clean content to stdout, metadata to stderr.
    if cr.is_structured:
        sys.stdout.write(cr.compressed + "\n")
        err_console.print(cr.metadata_footer(), style="dim")
    else:
        sys.stdout.write(str(cr) + "\n")

    if dry_run and cr.removed_lines:
        _print_dry_run_report(cr)

    sys.exit(result.returncode)


def _print_dry_run_report(cr) -> None:  # type: ignore[no-untyped-def]
    from contextclipper.engine.filters import _ERROR_SIGNALS  # type: ignore[import-not-found]
    removed = cr.removed_lines or []
    err_console.print()
    err_console.print(Panel(
        f"[bold]{len(removed)}[/bold] line(s) would be removed "
        f"([green]{cr.kept_lines}[/green] kept / [dim]{cr.original_lines}[/dim] total)",
        title="[bold yellow]ctxclp dry-run report[/bold yellow]",
        border_style="yellow",
    ))
    if cr.dropped_error_lines:
        err_console.print(
            f"[red bold]⚠  {len(cr.dropped_error_lines)} error-signal line(s) would be dropped![/red bold]"
        )
        for ln in cr.dropped_error_lines[:10]:
            err_console.print(f"  [red]{ln[:120]}[/red]")
        if len(cr.dropped_error_lines) > 10:
            err_console.print(f"  [dim]…and {len(cr.dropped_error_lines) - 10} more[/dim]")
        err_console.print()

    kept_sample = set(cr.compressed.splitlines()) if cr.compressed else set()
    table = Table(title="Removed lines (first 30)", show_header=True, show_lines=False)
    table.add_column("#", style="dim", width=5)
    table.add_column("Content", no_wrap=False)
    for ln_no, content in removed[:30]:
        is_error = bool(_ERROR_SIGNALS.search(content))
        style = "red" if is_error else "dim"
        table.add_row(str(ln_no), Text(content[:120], style=style))
    err_console.print(table)
    if len(removed) > 30:
        err_console.print(f"[dim]  …and {len(removed) - 30} more removed lines[/dim]")


# ── ctxclp fetch <id> ────────────────────────────────────────────────────────

@cli.command(name="fetch")
@click.argument("output_id")
def cmd_fetch(output_id: str) -> None:
    """Retrieve the full raw output stored for the given ID (from tee store).

    Agents that see ``raw_id=<id>`` in compressed output can call this to get
    the complete original output. The tee store has a 24-hour TTL by default.
    """
    from contextclipper.engine.tee import get_raw  # type: ignore[import-not-found]
    data = get_raw(output_id)
    if data is None:
        err_console.print(
            f"[red]Output ID [bold]{output_id}[/bold] not found or expired "
            f"(TTL={os.environ.get('CTXCLP_TEE_TTL', '86400')}s).[/red]"
        )
        sys.exit(1)
    try:
        stats = _get_stats()
        stats.record_raw_pull(output_id)
        stats.close()
    except Exception:
        pass
    sys.stdout.write(data)


# ── ctxclp build ─────────────────────────────────────────────────────────────

@cli.command(name="build")
@click.argument("project_root", default=".", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Force full re-index (ignore cached hashes)")
def cmd_build(project_root: str, force: bool) -> None:
    """Index the project code graph (PHP, Python, TypeScript → SQLite)."""
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
            console.print(
                "[yellow]No supported AI agents detected. "
                "Supported: claude-code, cursor, windsurf, cline, gemini-cli, codex[/yellow]"
            )
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
    console.print(f"  Bytes saved:     {summary['bytes_saved']:,}")
    console.print(f"  Avg latency:     {summary['avg_elapsed_ms']}ms")
    console.print(f"  Raw pulls:       {summary.get('raw_pull_count', 0)}")

    if summary["top_commands"]:
        table = Table(title="Top Commands", show_header=True)
        table.add_column("Command", style="cyan")
        table.add_column("Count", justify="right")
        for row in summary["top_commands"]:
            table.add_row(row["command"][:60], str(row["count"]))
        console.print(table)


# ── ctxclp audit ─────────────────────────────────────────────────────────────

@cli.command(name="audit")
@click.option("--days", default=1, show_default=True, help="Days to look back")
@click.option("--last", "limit", default=50, show_default=True, help="Max records to show")
@click.option("--command", "cmd_filter", default=None, help="Filter by command substring")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def cmd_audit(days: int, limit: int, cmd_filter: str | None, as_json: bool) -> None:
    """Show detailed per-command clipping audit trail.

    Each record shows what was clipped, by which filter, and whether the full
    output was subsequently fetched by the agent.
    """
    try:
        stats = _get_stats()
        records = stats.audit(days=days, limit=limit, command_filter=cmd_filter)
        stats.close()
    except Exception as e:
        console.print(f"[red]Error reading audit log: {e}[/red]")
        return

    if as_json:
        console.print(json.dumps(records, indent=2))
        return

    if not records:
        console.print(f"[dim]No events in the last {days} day(s).[/dim]")
        return

    table = Table(
        title=f"Audit log — last {days} day(s), {len(records)} event(s)",
        show_header=True,
    )
    table.add_column("Time", style="dim", width=8)
    table.add_column("Command", style="cyan", max_width=40)
    table.add_column("Kept/Total", justify="right")
    table.add_column("-%", justify="right", style="green")
    table.add_column("Filter", style="magenta")
    table.add_column("Fetched?", justify="center")

    for r in records:
        ts = datetime.datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        fetched = "[red]YES[/red]" if r["had_raw_pull"] else ""
        table.add_row(
            ts,
            r["command"][:40],
            f"{r['kept_lines']}/{r['original_lines']}",
            f"{r['reduction_pct']}%",
            r["filter_name"] or "fallback",
            fetched,
        )
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
        console.print(
            f"[green]✓ ContextClipper healthy[/green] — "
            f"{filt_report['filters']} filters, {graph_report['files_indexed']} files indexed"
        )
        if filt_report.get("warnings"):
            for w in filt_report["warnings"]:
                console.print(f"  [yellow]⚠ {w}[/yellow]")
        sys.exit(0)

    console.print("[red]✗ ContextClipper validation failed[/red]")
    for p in filt_report["problems"]:
        console.print(f"  [red]filter:[/red] {p}")
    for p in graph_report["problems"]:
        console.print(f"  [red]graph:[/red] {p}")
    if filt_report.get("warnings"):
        for w in filt_report["warnings"]:
            console.print(f"  [yellow]⚠ {w}[/yellow]")
    sys.exit(1)


# ── ctxclp doctor ────────────────────────────────────────────────────────────

@cli.command(name="doctor")
def cmd_doctor() -> None:
    """Run a comprehensive health check: filters, graph DB, hooks, tee store."""
    from contextclipper.cli.install import detect_agents  # type: ignore[import-not-found]
    from contextclipper.engine import get_registry  # type: ignore[import-not-found]
    from contextclipper.engine.tee import _tee_dir  # type: ignore[import-not-found]

    issues: list[str] = []
    warnings_list: list[str] = []
    ok_list: list[str] = []

    # 1. Filter registry
    reg = get_registry()
    filt_report = reg.validate()
    if filt_report["ok"]:
        ok_list.append(f"Filter registry: {filt_report['filters']} filters loaded, all valid")
    else:
        for p in filt_report["problems"]:
            issues.append(f"Filter: {p}")
    if filt_report.get("warnings"):
        for w in filt_report["warnings"][:5]:
            warnings_list.append(f"Filter: {w}")
        excess = len(filt_report["warnings"]) - 5
        if excess > 0:
            warnings_list.append(f"Filter: …{excess} more description warnings (run ctxclp validate)")

    # 2. Graph DB
    try:
        graph = _get_graph()
        graph_report = graph.validate()
        graph.close()
        if graph_report["ok"]:
            ok_list.append(f"Code graph: {graph_report['files_indexed']} files indexed, DB healthy")
        else:
            for p in graph_report["problems"]:
                issues.append(f"Graph: {p}")
    except Exception as e:
        warnings_list.append(f"Graph DB not accessible: {e}")

    # 3. Tee store
    try:
        tee = _tee_dir()
        tee_size = sum(p.stat().st_size for p in tee.glob("*.log"))
        ok_list.append(f"Tee store: writable, {tee_size / 1024:.1f} KiB used")
    except Exception as e:
        issues.append(f"Tee store not writable: {e}")

    # 4. Stats DB
    try:
        stats = _get_stats()
        summary = stats.summary(days=30)
        stats.close()
        ok_list.append(f"Stats DB: {summary['total_commands']} commands in last 30 days")
    except Exception as e:
        issues.append(f"Stats DB not accessible: {e}")

    # 5. Installed agents / hooks
    try:
        detected = detect_agents()
        if detected:
            ok_list.append(f"Detected agents: {', '.join(detected)}")
        else:
            warnings_list.append("No AI agents detected — run `ctxclp install` to set up hooks")
    except Exception as e:
        warnings_list.append(f"Agent detection failed: {e}")

    # 6. ctxclp binary reachable
    try:
        bin_path = shlex.quote(sys.argv[0])
        ok_list.append(f"ctxclp binary: {sys.argv[0]}")
    except Exception:
        warnings_list.append("Could not determine ctxclp binary path")

    # Print report
    console.print("\n[bold]ContextClipper Doctor Report[/bold]\n")
    for msg in ok_list:
        console.print(f"  [green]✓[/green] {msg}")
    for msg in warnings_list:
        console.print(f"  [yellow]⚠[/yellow] {msg}")
    for msg in issues:
        console.print(f"  [red]✗[/red] {msg}")

    if not issues:
        console.print("\n[green bold]All checks passed.[/green bold]")
        sys.exit(0)
    else:
        console.print(f"\n[red bold]{len(issues)} issue(s) found.[/red bold]")
        sys.exit(1)


# ── ctxclp hook-rewrite (used internally by shell hooks) ─────────────────────

@cli.command(name="hook-rewrite", hidden=True)
def cmd_hook_rewrite() -> None:
    """Read a hook event from stdin, rewrite bash command to use ctxclp run."""
    import json as _json

    try:
        event = _json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = event.get("tool_name", event.get("tool", ""))
    if tool not in ("Bash", "bash", "shell", "run_command"):
        sys.exit(0)

    inp = event.get("tool_input", event.get("input", {}))
    cmd = inp.get("command", inp.get("cmd", ""))

    if not cmd or os.environ.get("CTXCLP_INTERNAL") == "1":
        sys.exit(0)

    ctxclp_bin = shlex.quote(sys.argv[0])
    # Use shlex.quote so that shell metacharacters in cmd are safe.
    inp["command"] = f"CTXCLP_INTERNAL=1 {ctxclp_bin} run -- {shlex.quote(cmd)}"
    event["tool_input"] = inp
    sys.stdout.write(_json.dumps(event))


# ── ctxclp filter ─────────────────────────────────────────────────────────────

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
    template = f'''[filter]
name = "{name}"
description = "Custom filter for {command_pattern} — describe what this filter does"

[[filter.patterns]]
match_command = "{command_pattern}"

[[filter.rules]]
description = "Drop noisy debug/info lines"
type = "drop_matching"
pattern = "^(DEBUG|INFO|TRACE):"

[[filter.rules]]
description = "Always keep error and warning lines"
type = "keep_matching"
pattern = "^(ERROR|WARN|FAIL|\\\\[ERROR\\\\])"
priority = 10

[filter.on_failure]
[[filter.on_failure.rules]]
description = "On non-zero exit, keep all lines to preserve full error context"
type = "keep_matching"
pattern = "."
priority = 5
'''
    out.write_text(template)
    console.print(f"[green]Created:[/green] {out}")
    console.print("Edit the file to add your filter rules, then test with:")
    console.print(f"  [cyan]ctxclp filter test {shlex.quote(command_pattern)}[/cyan]")


@cmd_filter.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def filter_list(as_json: bool) -> None:
    """List all loaded filters with their match patterns and rule counts."""
    from contextclipper.engine import get_registry  # type: ignore[import-not-found]
    reg = get_registry()
    filters = reg.all_filters()

    if as_json:
        data = []
        for f in filters:
            data.append({
                "name": f.name,
                "description": f.description,
                "source": str(f.source_path) if f.source_path else "builtin",
                "match_patterns": [p.pattern for p in f.match_patterns],
                "rule_count": len(f.rules),
                "override_count": len(f.command_overrides),
                "has_on_failure": bool(f.on_failure_rules),
                "strategy": f.strategy,
            })
        console.print(json.dumps(data, indent=2))
        return

    table = Table(title=f"Loaded Filters ({len(filters)})", show_header=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Description")
    table.add_column("Match Patterns", style="dim")
    table.add_column("Rules", justify="right")
    table.add_column("Source", style="dim")

    for f in filters:
        patterns = ", ".join(p.pattern for p in f.match_patterns[:3])
        if len(f.match_patterns) > 3:
            patterns += f" +{len(f.match_patterns) - 3}"
        rule_count = len(f.rules) + sum(len(ov.get("rules", [])) for ov in f.command_overrides)
        source = "builtin" if f.source_path and "contextclipper/filters" in str(f.source_path) else "user"
        table.add_row(
            f.name,
            (f.description or "[dim]no description[/dim]")[:60],
            patterns,
            str(rule_count),
            source,
        )
    console.print(table)


@cmd_filter.command(name="test")
@click.argument("command")
@click.option("--no-run", is_flag=True, help="Read output from stdin instead of running command")
def filter_test(command: str, no_run: bool) -> None:
    """Run COMMAND and show a safety analysis of how it would be clipped.

    Displays a safety score, warns if error-signal lines would be dropped,
    and shows a side-by-side diff of kept vs. removed lines.
    """
    from contextclipper.engine.filters import compress_output, _ERROR_SIGNALS  # type: ignore[import-not-found]
    from contextclipper.engine import get_registry  # type: ignore[import-not-found]

    if no_run:
        raw_output = sys.stdin.read()
        exit_code = 0
    else:
        console.print(f"[dim]Running:[/dim] {command}")
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=_DEFAULT_TIMEOUT,
            )
            raw_output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out[/red]")
            sys.exit(1)

    cr = compress_output(command, raw_output, exit_code, dry_run=True)
    reg = get_registry()
    safety = reg.safety_check(command, raw_output)

    score = safety["safety_score"]
    score_color = "green" if score >= 9 else "yellow" if score >= 6 else "red"

    console.print()
    console.print(Panel(
        f"Filter: [cyan]{safety['filter_used']}[/cyan]\n"
        f"Original: {cr.original_lines} lines → Kept: [green]{cr.kept_lines}[/green] "
        f"([green]-{cr.reduction_pct}%[/green])\n"
        f"Safety score: [{score_color}]{score}/10[/{score_color}]",
        title="[bold]Filter Test Report[/bold]",
        border_style="blue",
    ))

    if safety["recommendation"]:
        console.print(f"\n[bold]Recommendation:[/bold] {safety['recommendation']}")

    if safety["error_lines_dropped"]:
        console.print(f"\n[red bold]⚠  Error-signal lines that would be DROPPED:[/red bold]")
        for ln in safety["error_lines_dropped"]:
            console.print(f"  [red]─ {ln[:100]}[/red]")

    if safety["error_lines_kept"]:
        console.print(f"\n[green]✓  Error-signal lines that would be KEPT:[/green]")
        for ln in safety["error_lines_kept"][:5]:
            console.print(f"  [green]+ {ln[:100]}[/green]")

    removed = cr.removed_lines or []
    if removed:
        kept_lines = set(cr.compressed.splitlines())
        table = Table(title=f"Removed lines ({len(removed)} total, showing first 25)", show_header=True)
        table.add_column("#", style="dim", width=5)
        table.add_column("Removed content")

        for ln_no, content in removed[:25]:
            is_err = bool(_ERROR_SIGNALS.search(content))
            style = "red bold" if is_err else "dim"
            marker = "⚠ " if is_err else ""
            table.add_row(str(ln_no), Text(f"{marker}{content[:100]}", style=style))
        console.print(table)

    sys.exit(0 if score >= 8 else 1)


# ── ctxclp hook ──────────────────────────────────────────────────────────────

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
    console.print(
        f"\n[dim]Reduction: {cr.reduction_pct}% "
        f"({cr.original_lines} → {cr.kept_lines} lines)"
        f" | Filter: {cr.filter_name or 'generic-fallback'}[/dim]"
    )


if __name__ == "__main__":
    cli()
