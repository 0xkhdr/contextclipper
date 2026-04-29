"""Unified exception hierarchy for ContextClipper."""

class ContextClipperError(Exception):
    """Base exception for all ContextClipper errors."""
    pass

class FilterParseError(ContextClipperError):
    """Raised when a filter file cannot be parsed; logged, not re-raised."""
    pass
