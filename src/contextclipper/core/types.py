"""Shared types and dataclasses."""

from __future__ import annotations
import os
from dataclasses import dataclass, field

@dataclass
class CompressionResult:
    compressed: str
    original_lines: int
    kept_lines: int
    raw_output_id: str | None = None
    elapsed_ms: float = 0.0
    removed_lines: list[tuple[int, str]] | None = None
    """Populated only when ``dry_run=True`` — list of (1-based line_no, content)."""
    truncated: bool = False
    strategy_name: str | None = None
    bytes_in: int = 0
    bytes_out: int = 0
    is_structured: bool = False
    """True when the compressed output is valid JSON — callers should route
    the ctxclp metadata footer to stderr instead of appending it to content."""
    filter_name: str | None = None
    """Name of the matched filter, or None for the generic fallback."""
    dropped_error_lines: list[str] | None = None
    """Lines containing error signals that were dropped (populated for safety analysis)."""

    @property
    def reduction_pct(self) -> float:
        if self.original_lines == 0:
            return 0.0
        return round((1 - self.kept_lines / self.original_lines) * 100, 1)

    def metadata_footer(self) -> str:
        """Return the human-readable metadata footer line (never includes newline)."""
        parts = [f"{self.kept_lines}/{self.original_lines} lines, -{self.reduction_pct}% tokens"]
        if self.raw_output_id:
            parts.append(f"raw_id={self.raw_output_id}")
            parts.append(f"fetch: ctxclp fetch {self.raw_output_id}")
        if self.truncated:
            parts.append("truncated")
        if self.filter_name:
            parts.append(f"filter={self.filter_name}")
        return "[ctxclp: " + " | ".join(parts) + "]"

    def machine_footer_line(self) -> str | None:
        """Return the machine-parseable footer ``[CTXCLP:raw=<uuid>]``, or None."""
        if self.raw_output_id:
            return f"[CTXCLP:raw={self.raw_output_id}]"
        return None

    def __str__(self) -> str:
        machine_footer_default = os.environ.get("CTXCLP_INCLUDE_MACHINE_FOOTER", "1") == "1"
        if self.is_structured:
            return self.compressed
        out = self.compressed + "\n" + self.metadata_footer()
        mf = self.machine_footer_line()
        if mf and machine_footer_default:
            out += "\n" + mf
        return out

@dataclass
class SymbolSummary:
    """Lightweight symbol representation for agents."""
    name: str
    kind: str                          # "class", "method", "function"
    file_path: str
    line_start: int
    line_end: int
    signature: str | None = None
    docstring: str | None = None
    visibility: str = "public"         # public, private, protected
