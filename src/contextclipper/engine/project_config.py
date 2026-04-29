"""Per-project configuration loader for ContextClipper.

ContextClipper looks for a ``.ctxclp.toml`` file walking upward from
``cwd`` (or an explicit ``project_root``) toward the filesystem root.
The first file found wins (closest to the working directory).

Supported keys (all optional)
------------------------------
.. code-block:: toml

    [ctxclp]
    # Cap token budget for all commands run in this project
    max_tokens = 4000

    # Default compression level: "conservative" | "balanced" | "aggressive"
    compression = "balanced"

    # Extra TOML filter dirs to load for this project (relative to project root)
    filter_dirs = ["tools/ctxclp-filters"]

    # Commands to exclude from compression (run raw)
    passthrough_commands = ["my-interactive-tool"]

    # Disable specific built-in filters by name
    disable_filters = ["docker"]

Usage::

    from contextclipper.engine.project_config import load_project_config, ProjectConfig
    cfg = load_project_config()           # search from cwd
    cfg = load_project_config("/my/proj") # search from explicit root
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging import get_logger

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

log = get_logger()

_CONFIG_FILENAME = ".ctxclp.toml"

# Maximum directory levels to walk upward when searching for the config file.
_MAX_WALK_DEPTH = 10


@dataclass
class ProjectConfig:
    """Resolved per-project configuration.

    All attributes have safe defaults so callers can always use them
    without checking for ``None``.
    """

    #: Absolute path of the resolved config file, or ``None`` if not found.
    config_path: Path | None = None

    #: Per-project token budget (passed to ``compress_output(max_tokens=…)``).
    max_tokens: int | None = None

    #: Compression level hint: "conservative" | "balanced" | "aggressive".
    compression: str = "balanced"

    #: Additional filter directories (absolute paths) to load.
    filter_dirs: list[Path] = field(default_factory=list)

    #: Command patterns to pass through without compression.
    passthrough_commands: list[str] = field(default_factory=list)

    #: Built-in filter names to disable for this project.
    disable_filters: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        """True if a config file was located."""
        return self.config_path is not None

    def should_passthrough(self, command: str) -> bool:
        """Return True if ``command`` matches any passthrough pattern."""
        import re
        cmd_base = command.strip().split()[0] if command.strip() else command
        for pat in self.passthrough_commands:
            try:
                if re.search(pat, cmd_base):
                    return True
            except re.error:
                if pat in cmd_base:
                    return True
        return False

    def is_filter_disabled(self, filter_name: str) -> bool:
        """Return True if ``filter_name`` is in the disable list."""
        return filter_name in self.disable_filters


def _find_config_file(start: Path) -> Path | None:
    """Walk upward from ``start`` looking for ``.ctxclp.toml``."""
    current = start.resolve()
    for _ in range(_MAX_WALK_DEPTH):
        candidate = current / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent
    return None


def _parse_config(path: Path) -> ProjectConfig:
    """Parse a ``.ctxclp.toml`` file into a :class:`ProjectConfig`."""
    try:
        with open(path, "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.warning("Failed to parse %s: %s — using defaults", path, e)
        return ProjectConfig(config_path=path)

    section: dict[str, Any] = data.get("ctxclp", {})
    project_root = path.parent

    max_tokens: int | None = section.get("max_tokens")
    if max_tokens is not None:
        try:
            max_tokens = int(max_tokens)
            if max_tokens <= 0:
                log.warning("%s: max_tokens must be positive; ignoring", path)
                max_tokens = None
        except (TypeError, ValueError):
            log.warning("%s: invalid max_tokens value; ignoring", path)
            max_tokens = None

    compression = str(section.get("compression", "balanced"))
    if compression not in ("conservative", "balanced", "aggressive"):
        log.warning(
            "%s: unknown compression level %r; defaulting to 'balanced'",
            path,
            compression,
        )
        compression = "balanced"

    raw_filter_dirs: list[str] = section.get("filter_dirs", [])
    filter_dirs: list[Path] = []
    for d in raw_filter_dirs:
        p = Path(d)
        if not p.is_absolute():
            p = (project_root / p).resolve()
        if p.is_dir():
            filter_dirs.append(p)
        else:
            log.warning("%s: filter_dir %r does not exist; skipping", path, str(p))

    passthrough: list[str] = [str(x) for x in section.get("passthrough_commands", [])]
    disable: list[str] = [str(x) for x in section.get("disable_filters", [])]

    return ProjectConfig(
        config_path=path,
        max_tokens=max_tokens,
        compression=compression,
        filter_dirs=filter_dirs,
        passthrough_commands=passthrough,
        disable_filters=disable,
    )


def load_project_config(
    project_root: str | Path | None = None,
) -> ProjectConfig:
    """Load the nearest ``.ctxclp.toml`` for the given directory.

    Searches upward from ``project_root`` (defaults to :func:`os.getcwd`).
    Returns a :class:`ProjectConfig` with safe defaults if no file is found.

    Args:
        project_root: Starting directory for the upward search.
            Defaults to the current working directory.

    Returns:
        Resolved :class:`ProjectConfig`.
    """
    start = Path(project_root).resolve() if project_root else Path(os.getcwd())
    config_path = _find_config_file(start)
    if config_path is None:
        log.debug("No %s found (searched from %s)", _CONFIG_FILENAME, start)
        return ProjectConfig()
    log.debug("Loaded project config from %s", config_path)
    return _parse_config(config_path)
