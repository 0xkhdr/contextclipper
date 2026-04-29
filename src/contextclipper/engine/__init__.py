"""ctxclp-engine: shared filter engine, code graph indexer, and analytics (DEPRECATED)."""

import warnings

from contextclipper.core.types import CompressionResult
from contextclipper.core.exceptions import FilterParseError
from contextclipper.shell.engine import (
    FilterRegistry,
    compress_output,
    get_registry,
    register_strategy,
    unregister_strategy,
)
from contextclipper.graph.builder import GraphDB
from contextclipper.core.logging import get_logger
from contextclipper.core.redact import redact_command, redact_text
from contextclipper.core.stats import StatsDB
from contextclipper.core.tee import get_raw, save_raw

warnings.warn(
    "contextclipper.engine is deprecated, use contextclipper.core, contextclipper.shell, and contextclipper.graph",
    DeprecationWarning,
    stacklevel=2,
)

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
