"""Subsystem availability detection with graceful degradation."""

from __future__ import annotations

import functools
from typing import Protocol


class SubsystemAvailability:
    """Detects which subsystems are installed."""

    @functools.cached_property
    def has_shell(self) -> bool:
        try:
            import contextclipper.shell  # noqa: F401
            return True
        except ImportError:
            return False

    @functools.cached_property
    def has_graph(self) -> bool:
        try:
            import contextclipper.graph  # noqa: F401
            return True
        except ImportError:
            return False

    @functools.cached_property
    def has_mcp(self) -> bool:
        try:
            import contextclipper.mcp  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def can_serve(self) -> bool:
        """Full MCP server requires both shell and graph."""
        return self.has_shell and self.has_graph and self.has_mcp


# Singleton
availability = SubsystemAvailability()
