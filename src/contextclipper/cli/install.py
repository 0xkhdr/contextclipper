"""Agent auto-detection and hook/MCP injection."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

HOME = Path.home()
CTXCLP_BIN = shutil.which("ctxclp") or sys.executable + " -m contextclipper"

# ── Agent detection ───────────────────────────────────────────────────────────

def _detect_claude_code() -> bool:
    return (HOME / ".claude").is_dir() or (Path(".claude")).is_dir()


def _detect_cursor() -> bool:
    return (HOME / ".cursor").is_dir() or (HOME / "Library" / "Application Support" / "Cursor").is_dir()


def _detect_windsurf() -> bool:
    return (Path(".windsurfrules")).exists() or (HOME / ".windsurf").is_dir()


def _detect_cline() -> bool:
    return Path(".clinerules").exists()


def _detect_gemini_cli() -> bool:
    return (HOME / ".gemini" / "settings.json").exists()


def _detect_codex() -> bool:
    return (Path(".codex") / "config.json").exists()


AGENTS: dict[str, tuple[Any, Any]] = {
    "claude-code": (_detect_claude_code, "_install_claude_code"),
    "cursor": (_detect_cursor, "_install_cursor"),
    "windsurf": (_detect_windsurf, "_install_windsurf"),
    "cline": (_detect_cline, "_install_cline"),
    "gemini-cli": (_detect_gemini_cli, "_install_gemini_cli"),
    "codex": (_detect_codex, "_install_codex"),
}


# ── Hook templates ────────────────────────────────────────────────────────────

CLAUDE_CODE_HOOK = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"CTXCLP_HOOK_ACTIVE=1 {CTXCLP_BIN} hook-rewrite",
                    }
                ],
            }
        ]
    }
}

MCP_CONFIG = {
    "mcpServers": {
        "contextclipper": {
            "command": CTXCLP_BIN,
            "args": ["serve"],
            "env": {
                "CTXCLP_PROJECT_ROOT": "${workspaceFolder}",
            },
        }
    }
}

RULE_FILE_CONTENT = """# ContextClipper — Agent Instructions
When running shell commands, prefix them with `ctxclp run` to get compressed output
that fits within your context window. For example:
  ctxclp run git status
  ctxclp run composer install
  ctxclp run phpunit

For code navigation, use the ContextClipper MCP tools:
  - get_file(path)        → symbol summary without full source
  - search_symbols(query) → find classes/methods by name
  - get_affected(files)   → blast-radius of your changes
  - project://overview    → compact project map

Never request raw file contents when get_file or project://overview will suffice.
"""


# ── Installer functions ───────────────────────────────────────────────────────

def _write_json_merge(path: Path, new_data: dict) -> None:
    """Merge new_data into existing JSON at path (creates file if absent)."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    merged = _deep_merge(existing, new_data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2))


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _remove_json_key(path: Path, *keys: str) -> None:
    """Remove a key path from a JSON file."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return
    target = data
    for key in keys[:-1]:
        target = target.get(key, {})
    target.pop(keys[-1], None)
    path.write_text(json.dumps(data, indent=2))


def _install_claude_code(uninstall: bool = False) -> str:
    settings_path = HOME / ".claude" / "settings.json"
    mcp_path = Path(".mcp.json")

    if uninstall:
        _remove_json_key(settings_path, "hooks")
        _remove_json_key(mcp_path, "mcpServers", "contextclipper")
        return "Claude Code: hooks and MCP config removed."

    _write_json_merge(settings_path, CLAUDE_CODE_HOOK)
    _write_json_merge(mcp_path, MCP_CONFIG)
    return "Claude Code: PreToolUse hook + MCP config installed."


def _install_cursor(uninstall: bool = False) -> str:
    # Cursor stores hooks in ~/.cursor/hooks.json
    hooks_path = HOME / ".cursor" / "hooks.json"
    mcp_path = Path(".mcp.json")
    cursor_hook = {
        "preToolUse": {
            "bash": f"CTXCLP_HOOK_ACTIVE=1 {CTXCLP_BIN} hook-rewrite"
        }
    }
    if uninstall:
        if hooks_path.exists():
            _remove_json_key(hooks_path, "preToolUse", "bash")
        _remove_json_key(mcp_path, "mcpServers", "contextclipper")
        return "Cursor: hooks and MCP config removed."

    _write_json_merge(hooks_path, cursor_hook)
    _write_json_merge(mcp_path, MCP_CONFIG)
    return "Cursor: preToolUse hook + MCP config installed."


def _install_windsurf(uninstall: bool = False) -> str:
    rules_path = Path(".windsurfrules")
    if uninstall:
        if rules_path.exists():
            content = rules_path.read_text()
            marker_start = "\n# === ContextClipper ===\n"
            marker_end = "\n# === /ContextClipper ===\n"
            if marker_start in content:
                start = content.index(marker_start)
                end = content.index(marker_end) + len(marker_end)
                rules_path.write_text(content[:start] + content[end:])
        return "Windsurf: rule injection removed."

    existing = rules_path.read_text() if rules_path.exists() else ""
    if "ContextClipper" not in existing:
        marker = "\n# === ContextClipper ===\n" + RULE_FILE_CONTENT + "\n# === /ContextClipper ===\n"
        rules_path.write_text(existing + marker)
    _write_json_merge(Path(".mcp.json"), MCP_CONFIG)
    return "Windsurf: rule file injection + MCP config installed."


def _install_cline(uninstall: bool = False) -> str:
    rules_path = Path(".clinerules")
    if uninstall:
        if rules_path.exists():
            content = rules_path.read_text()
            if "ContextClipper" in content:
                rules_path.write_text(content.split("# === ContextClipper ===")[0])
        return "Cline: rule injection removed."

    existing = rules_path.read_text() if rules_path.exists() else ""
    if "ContextClipper" not in existing:
        rules_path.write_text(existing + "\n# === ContextClipper ===\n" + RULE_FILE_CONTENT)
    _write_json_merge(Path(".mcp.json"), MCP_CONFIG)
    return "Cline: rule file injection + MCP config installed."


def _install_gemini_cli(uninstall: bool = False) -> str:
    settings_path = HOME / ".gemini" / "settings.json"
    hook = {
        "tools": {
            "shell": {
                "BeforeTool": f"CTXCLP_HOOK_ACTIVE=1 {CTXCLP_BIN} hook-rewrite"
            }
        }
    }
    if uninstall:
        _remove_json_key(settings_path, "tools", "shell", "BeforeTool")
        return "Gemini CLI: BeforeTool hook removed."

    _write_json_merge(settings_path, hook)
    _write_json_merge(Path(".mcp.json"), MCP_CONFIG)
    return "Gemini CLI: BeforeTool hook + MCP config installed."


def _install_codex(uninstall: bool = False) -> str:
    config_path = Path(".codex") / "config.json"
    instructions_path = Path(".codex") / "instructions.md"
    if uninstall:
        if instructions_path.exists():
            content = instructions_path.read_text()
            if "ContextClipper" in content:
                instructions_path.write_text(content.split("# ContextClipper")[0])
        return "Codex: instructions removed."

    existing = instructions_path.read_text() if instructions_path.exists() else ""
    if "ContextClipper" not in existing:
        instructions_path.parent.mkdir(exist_ok=True)
        instructions_path.write_text(existing + "\n" + RULE_FILE_CONTENT)
    _write_json_merge(Path(".mcp.json"), MCP_CONFIG)
    return "Codex: instructions + MCP config installed."


_INSTALL_FUNCS = {
    "claude-code": _install_claude_code,
    "cursor": _install_cursor,
    "windsurf": _install_windsurf,
    "cline": _install_cline,
    "gemini-cli": _install_gemini_cli,
    "codex": _install_codex,
}


def detect_agents() -> list[str]:
    return [name for name, (detect, _) in AGENTS.items() if detect()]


def install_all(agents: list[str] | None = None, uninstall: bool = False) -> dict[str, str]:
    targets = agents or detect_agents()
    results: dict[str, str] = {}
    for agent in targets:
        fn = _INSTALL_FUNCS.get(agent)
        if fn:
            try:
                results[agent] = fn(uninstall=uninstall)
            except Exception as e:
                results[agent] = f"ERROR: {e}"
        else:
            results[agent] = "Unknown agent — skipped."
    return results
