"""MCP server: exposes code graph tools and shell compression over stdio."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
    Resource,
    TextContent,
    Tool,
)

from contextclipper.graph.builder import GraphDB  # type: ignore[import-not-found]
from contextclipper.core.stats import StatsDB  # type: ignore[import-not-found]
from contextclipper.mcp.tools import (  # type: ignore[import-not-found]
    tool_get_affected,
    tool_get_file,
    tool_get_overview,
    tool_get_raw_output,
    tool_get_stats,
    tool_rebuild_graph,
    tool_run_shell,
    tool_search_symbols,
)

PROJECT_ROOT = Path(os.environ.get("CTXCLP_PROJECT_ROOT", ".")).resolve()
GRAPH_DB_PATH = Path(os.environ.get("CTXCLP_DB", str(Path.home() / ".local/share/contextclipper/graph.db")))


def build_server() -> Server:
    server = Server("contextclipper")
    graph = GraphDB(GRAPH_DB_PATH)
    stats = StatsDB()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_file",
                description="Get the symbol summary for a PHP (or any indexed) file. "
                            "Returns classes, methods, and dependencies without the full source.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from project root"},
                        "mode": {
                            "type": "string",
                            "enum": ["summary_only", "full", "smart"],
                            "default": "summary_only",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="search_symbols",
                description="Search for classes, methods, functions by name or FQN.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["class", "interface", "trait", "method", "function"],
                            "description": "Optional: filter by symbol kind",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="get_affected",
                description="Given a list of changed files, return all files that depend on them.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Relative paths of changed files",
                        }
                    },
                    "required": ["files"],
                },
            ),
            Tool(
                name="run_shell",
                description="Execute a shell command and return compressed output. "
                            "Use this instead of the Bash tool to save tokens.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "compression_level": {
                            "type": "string",
                            "enum": ["auto", "aggressive", "minimal", "none"],
                            "default": "auto",
                        },
                    },
                    "required": ["command"],
                },
            ),
            Tool(
                name="get_raw_output",
                description="Retrieve the full raw output of a previous run_shell call by its output_id.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "output_id": {"type": "string"}
                    },
                    "required": ["output_id"],
                },
            ),
            Tool(
                name="rebuild_graph",
                description="Rebuild the code graph index for the current project. "
                            "Run after significant file changes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Absolute path to project root (defaults to configured root)",
                        }
                    },
                },
            ),
        ]

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri="project://overview",
                name="Project Overview",
                description="Compact tree of all indexed files and their top-level symbols.",
                mimeType="text/markdown",
            ),
            Resource(
                uri="project://stats",
                name="ContextClipper Stats",
                description="Token savings and usage statistics.",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        if uri == "project://overview":
            return tool_get_overview(graph)
        elif uri == "project://stats":
            return json.dumps(tool_get_stats(stats), indent=2)
        return f"Unknown resource: {uri}"

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == "get_file":
            result = tool_get_file(
                graph,
                arguments["path"],
                mode=arguments.get("mode", "summary_only"),
                project_root=str(PROJECT_ROOT),
            )
        elif name == "search_symbols":
            result = json.dumps(
                tool_search_symbols(graph, arguments["query"], kind=arguments.get("kind")),
                indent=2,
            )
        elif name == "get_affected":
            result = json.dumps(tool_get_affected(graph, arguments["files"]), indent=2)
        elif name == "run_shell":
            result = json.dumps(
                tool_run_shell(arguments["command"], arguments.get("compression_level", "auto"), stats),
                indent=2,
            )
        elif name == "get_raw_output":
            result = tool_get_raw_output(arguments["output_id"], stats_db=stats)
        elif name == "rebuild_graph":
            counts = tool_rebuild_graph(graph, arguments.get("project_root", str(PROJECT_ROOT)))
            result = json.dumps(counts, indent=2)
        else:
            result = f"Unknown tool: {name}"
        return [TextContent(type="text", text=str(result))]

    return server


async def run() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
