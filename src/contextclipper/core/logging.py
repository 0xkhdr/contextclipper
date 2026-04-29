"""Structured logging for ctxclp.

A single named logger ``ctxclp`` is configured the first time
:func:`get_logger` is called. The level can be overridden with the
``CTXCLP_LOG_LEVEL`` env var (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).

Logging never writes user output content — only metadata (sizes, line counts,
durations, error class names). Filter / parser failures log at WARNING. The
library deliberately avoids ``print`` so embedders can route logs as they wish.
"""

from __future__ import annotations

import logging
import os

_LOGGER_NAME = "ctxclp"
_configured = False


def get_logger() -> logging.Logger:
    global _configured
    log = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        level_name = os.environ.get("CTXCLP_LOG_LEVEL", "WARNING").upper()
        level = getattr(logging, level_name, logging.WARNING)
        if not log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("[ctxclp:%(levelname)s] %(message)s"))
            log.addHandler(handler)
        log.setLevel(level)
        log.propagate = False
        _configured = True
    return log
