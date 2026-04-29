"""ctxclp-engine: shared filter engine, code graph indexer, and analytics."""

from .filters import (
    CompressionResult,
    FilterParseError,
    FilterRegistry,
    compress_output,
    get_registry,
    register_strategy,
    unregister_strategy,
)
from .graph import GraphDB
from .logging import get_logger
from .redact import redact_command, redact_text
from .stats import StatsDB
from .tee import get_raw, save_raw

__all__ = [
    "CompressionResult",
    "FilterParseError",
    "FilterRegistry",
    "GraphDB",
    "StatsDB",
    "compress_output",
    "get_logger",
    "get_raw",
    "get_registry",
    "redact_command",
    "redact_text",
    "register_strategy",
    "save_raw",
    "unregister_strategy",
]
