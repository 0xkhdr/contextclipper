"""Shell filter engine: loads TOML filter definitions and compresses command output.

Public API (stable, backwards-compatible):

  - ``compress_output(command, raw_output, exit_code=0, raw_output_id=None,
                      *, dry_run=False, max_input_bytes=None, max_tokens=None,
                      strategy=None) -> CompressionResult``
  - ``CompressionResult`` — data class with ``compressed``, ``original_lines``,
    ``kept_lines``, ``raw_output_id``, ``elapsed_ms``, plus ``removed_lines``
    (set when ``dry_run=True``), ``truncated`` (set when input exceeded
    ``max_input_bytes`` and was truncated), and ``is_structured`` (True when
    the compressed output is valid JSON, so callers can route metadata to
    stderr rather than appending it to content).
  - ``FilterRegistry`` — thread-safe registry. ``validate()`` runs a self-check
    over all loaded filters and returns problems found.
  - ``register_strategy(name, fn)`` / ``unregister_strategy(name)`` — install a
    custom compressor. ``fn(lines, command, exit_code) -> list[str]`` is called
    in place of TOML rules when a filter declares ``strategy = "<name>"``.

Security & robustness notes:

- All input is line-bounded to ``MAX_LINE_BYTES`` (default 64 KiB) before regex
  evaluation to mitigate ReDoS — Python's ``re`` has no native timeout.
- The total input is bounded to ``MAX_INPUT_BYTES`` (default 16 MiB); excess is
  truncated and a marker line appended.
- TOML parse errors are logged at WARNING and the filter is skipped, never
  raised — a single broken user filter must not break the engine.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .logging import get_logger

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
BUILTIN_FILTERS_DIR = Path(__file__).parent.parent / "filters"

# Patterns that strongly suggest a line is an error/warning — used for safety analysis.
_ERROR_SIGNALS = re.compile(
    r"\b(error|ERROR|Error|FAIL|failed|FAILED|exception|Exception|traceback|Traceback"
    r"|panic|PANIC|fatal|FATAL|critical|CRITICAL|assertion|AssertionError)\b",
    re.IGNORECASE,
)

# A drop_matching pattern that catches virtually everything is dangerous.
_CATCH_ALL_PATTERNS = re.compile(r"^\.\*$|^\.\+$|^\^?\.\*\$?$")


def _user_config_dir() -> Path:
    """Return XDG-aware user filter directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "contextclipper" / "filters"


USER_FILTERS_DIR = _user_config_dir()

# Hard input bounds to mitigate ReDoS and OOM. Configurable via env vars.
MAX_LINE_BYTES = int(os.environ.get("CTXCLP_MAX_LINE_BYTES", 64 * 1024))
MAX_INPUT_BYTES = int(os.environ.get("CTXCLP_MAX_INPUT_BYTES", 16 * 1024 * 1024))
TRUNCATION_MARKER = "[ctxclp: input truncated to {n} bytes]"

# Approx tokens per char for adaptive clipping (English-biased; safe upper bound).
_CHARS_PER_TOKEN = 4

log = get_logger()


class FilterParseError(Exception):
    """Raised internally when a filter file cannot be parsed; logged, not re-raised."""


Strategy = Callable[[list[str], str, int], list[str]]
_strategies: dict[str, Strategy] = {}
_strategies_lock = threading.RLock()


def register_strategy(name: str, fn: Strategy) -> None:
    """Register a custom compression strategy callable.

    The function receives ``(lines, command, exit_code)`` and must return the
    list of kept lines. Strategies are looked up by name when a TOML filter
    declares ``strategy = "<name>"``.
    """
    with _strategies_lock:
        _strategies[name] = fn


def unregister_strategy(name: str) -> None:
    with _strategies_lock:
        _strategies.pop(name, None)


def _get_strategy(name: str) -> Strategy | None:
    with _strategies_lock:
        return _strategies.get(name)


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
        """Return the metadata footer line (never includes newline)."""
        parts = [f"{self.kept_lines}/{self.original_lines} lines, -{self.reduction_pct}% tokens"]
        if self.raw_output_id:
            parts.append(f"raw_id={self.raw_output_id}")
            parts.append(f"fetch: ctxclp fetch {self.raw_output_id}")
        if self.truncated:
            parts.append("truncated")
        if self.filter_name:
            parts.append(f"filter={self.filter_name}")
        return "[ctxclp: " + " | ".join(parts) + "]"

    def __str__(self) -> str:
        # Never append the metadata footer inline for structured (JSON) output
        # so that callers reading stdout as JSON don't see a corrupt trailing line.
        if self.is_structured:
            return self.compressed
        return self.compressed + "\n" + self.metadata_footer()


@dataclass
class FilterRule:
    type: str
    description: str = ""
    pattern: str | None = None
    replacement: str | None = None
    prefix: str | None = None
    max_lines: int = 10
    lines: int = 50
    priority: int = 0
    start_pattern: str | None = None
    end_pattern: str | None = None
    fields: list[str] = field(default_factory=list)
    """For ``json_select`` rules: jq-style dot-paths to extract (e.g. ``.status.phase``)."""
    _compiled: re.Pattern | None = field(default=None, init=False, repr=False)
    _compiled_start: re.Pattern | None = field(default=None, init=False, repr=False)
    _compiled_end: re.Pattern | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.pattern:
            try:
                self._compiled = re.compile(self.pattern)
            except re.error as e:
                log.warning("Invalid filter pattern %r: %s", self.pattern, e)
        if self.start_pattern:
            try:
                self._compiled_start = re.compile(self.start_pattern)
            except re.error as e:
                log.warning("Invalid filter start_pattern %r: %s", self.start_pattern, e)
        if self.end_pattern:
            try:
                self._compiled_end = re.compile(self.end_pattern)
            except re.error as e:
                log.warning("Invalid filter end_pattern %r: %s", self.end_pattern, e)


@dataclass
class CommandFilter:
    name: str
    description: str
    match_patterns: list[re.Pattern]
    rules: list[FilterRule]
    command_overrides: list[dict[str, Any]] = field(default_factory=list)
    on_failure_rules: list[FilterRule] = field(default_factory=list)
    strategy: str | None = None
    source_path: Path | None = None


def _load_rules(raw_rules: list[dict]) -> list[FilterRule]:
    rules = []
    for r in raw_rules:
        rules.append(FilterRule(
            type=r.get("type", "drop_matching"),
            description=r.get("description", ""),
            pattern=r.get("pattern"),
            replacement=r.get("replacement"),
            prefix=r.get("prefix"),
            max_lines=int(r.get("max_lines", 10)),
            lines=int(r.get("lines", 50)),
            priority=int(r.get("priority", 0)),
            start_pattern=r.get("start_pattern"),
            end_pattern=r.get("end_pattern"),
            fields=list(r.get("fields", [])),
        ))
    return rules


def _load_toml_filter(path: Path) -> CommandFilter | None:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.warning("Failed to load filter %s: %s", path, e)
        return None
    fdef = data.get("filter", {})
    name = fdef.get("name", path.stem)
    desc = fdef.get("description", "")
    patterns_raw = fdef.get("patterns", [])
    match_patterns = []
    for p in patterns_raw:
        mc = p.get("match_command")
        if mc:
            try:
                match_patterns.append(re.compile(mc))
            except re.error as e:
                log.warning("Invalid match_command %r in %s: %s", mc, path, e)
    rules = _load_rules(fdef.get("rules", []))
    overrides_raw = fdef.get("command_overrides", [])
    overrides = []
    for ov in overrides_raw:
        match = ov.get("match", "")
        try:
            ov_compiled = re.compile(match)
        except re.error as e:
            log.warning("Invalid override match %r in %s: %s", match, path, e)
            continue
        ov_rules = _load_rules(ov.get("rules", []))
        overrides.append({"match": ov_compiled, "rules": ov_rules})
    on_failure = _load_rules(fdef.get("on_failure", {}).get("rules", []))
    strategy = fdef.get("strategy")
    return CommandFilter(
        name=name,
        description=desc,
        match_patterns=match_patterns,
        rules=rules,
        command_overrides=overrides,
        on_failure_rules=on_failure,
        strategy=strategy,
        source_path=path,
    )


class FilterRegistry:
    """Loads and caches all TOML filter definitions. Thread-safe."""

    def __init__(self) -> None:
        self._filters: list[CommandFilter] = []
        self._loaded = False
        self._lock = threading.RLock()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            for toml_path in sorted(BUILTIN_FILTERS_DIR.rglob("*.toml")):
                f = _load_toml_filter(toml_path)
                if f:
                    self._filters.append(f)
            user_dir = _user_config_dir()
            if user_dir.exists():
                for toml_path in sorted(user_dir.rglob("*.toml")):
                    f = _load_toml_filter(toml_path)
                    if f:
                        self._filters.append(f)
            self._loaded = True

    def reload(self) -> None:
        """Force-reload all filters from disk."""
        with self._lock:
            self._filters.clear()
            self._loaded = False
            self._ensure_loaded()

    def find(self, command: str) -> CommandFilter | None:
        self._ensure_loaded()
        cmd_base = command.strip().split("\n", 1)[0]
        for flt in self._filters:
            for pat in flt.match_patterns:
                if pat.search(cmd_base):
                    return flt
        return None

    def all_filters(self) -> list[CommandFilter]:
        self._ensure_loaded()
        with self._lock:
            return list(self._filters)

    def validate(self) -> dict[str, Any]:
        """Self-check: every loaded filter has at least one pattern, rules, and descriptions.

        Returns ``{"ok": bool, "filters": int, "problems": [...], "warnings": [...]}``.
        Used by the ``ctxclp validate`` and ``ctxclp doctor`` CLI commands.
        """
        self._ensure_loaded()
        problems: list[str] = []
        warnings: list[str] = []

        valid_types = {
            "drop_matching", "keep_matching", "regex_replace",
            "tail", "head", "keep_section", "prefix_collapse", "json_select",
        }

        for flt in self._filters:
            if not flt.match_patterns:
                problems.append(f"{flt.name}: no match_command patterns")
            if not flt.description:
                warnings.append(f"{flt.name}: missing filter-level description")
            if (
                not flt.rules
                and not flt.command_overrides
                and not flt.strategy
                and not flt.on_failure_rules
            ):
                problems.append(f"{flt.name}: no rules / overrides / strategy")

            all_rules = flt.rules + flt.on_failure_rules
            for ov in flt.command_overrides:
                all_rules = all_rules + ov.get("rules", [])

            for r in all_rules:
                if r.type not in valid_types:
                    problems.append(f"{flt.name}: unknown rule type {r.type!r}")
                if r.type in ("drop_matching", "keep_matching") and not r._compiled:
                    problems.append(f"{flt.name}: rule {r.type} missing compiled pattern")
                if r.type == "regex_replace" and (not r._compiled or r.replacement is None):
                    problems.append(f"{flt.name}: regex_replace missing pattern/replacement")
                if r.type == "keep_section" and not (r._compiled_start and r._compiled_end):
                    problems.append(f"{flt.name}: keep_section needs start_pattern and end_pattern")
                if r.type == "json_select" and not r.fields:
                    problems.append(f"{flt.name}: json_select rule has no fields defined")
                if not r.description:
                    warnings.append(f"{flt.name}: rule {r.type!r} has no description")
                # Detect dangerously broad drop patterns
                if r.type == "drop_matching" and r.pattern and _CATCH_ALL_PATTERNS.match(r.pattern):
                    problems.append(
                        f"{flt.name}: drop_matching pattern {r.pattern!r} matches everything — "
                        "this will drop all output"
                    )

        return {
            "ok": not problems,
            "filters": len(self._filters),
            "problems": problems,
            "warnings": warnings,
        }

    def safety_check(self, command: str, output: str) -> dict[str, Any]:
        """Check if the filter for ``command`` would drop any error-signal lines.

        Returns a dict with ``safety_score`` (0–10), ``error_lines_dropped``,
        ``error_lines_kept``, and ``recommendation``. Used by ``ctxclp filter test``.
        """
        flt = self.find(command)
        lines = output.splitlines()
        error_lines = [ln for ln in lines if _ERROR_SIGNALS.search(ln)]

        if not lines:
            return {"safety_score": 10, "error_lines_dropped": [], "error_lines_kept": [], "recommendation": ""}

        # Apply the filter
        if flt:
            rules = _find_override(flt, command) or flt.rules
            kept_set = set(_apply_rules(lines, rules))
        else:
            kept_set = {ln for ln in lines if ln.strip()}

        kept_errors = [ln for ln in error_lines if ln in kept_set]
        dropped_errors = [ln for ln in error_lines if ln not in kept_set]

        if not error_lines:
            score = 10
            rec = "No error-signal lines found in sample output."
        elif not dropped_errors:
            score = 10
            rec = "All error-signal lines are preserved."
        else:
            pct_kept = len(kept_errors) / len(error_lines)
            score = round(pct_kept * 10, 1)
            rec = (
                f"Consider adding a keep_matching rule with high priority for error patterns. "
                f"{len(dropped_errors)} error-signal line(s) would be dropped."
            )

        return {
            "safety_score": score,
            "error_lines_dropped": dropped_errors[:20],
            "error_lines_kept": kept_errors[:20],
            "recommendation": rec,
            "filter_used": flt.name if flt else "generic-fallback",
        }


_registry = FilterRegistry()


def get_registry() -> FilterRegistry:
    """Return the process-wide filter registry."""
    return _registry


def _truncate_line(line: str) -> str:
    if len(line) > MAX_LINE_BYTES:
        return line[:MAX_LINE_BYTES] + "…[line truncated]"
    return line


def _enforce_input_bounds(text: str) -> tuple[str, bool]:
    """Cap ``text`` at MAX_INPUT_BYTES. Returns (text, truncated)."""
    encoded_len = len(text.encode("utf-8", errors="replace"))
    if encoded_len <= MAX_INPUT_BYTES:
        return text, False
    safe_chars = MAX_INPUT_BYTES
    truncated = text[:safe_chars] + "\n" + TRUNCATION_MARKER.format(n=MAX_INPUT_BYTES) + "\n"
    return truncated, True


def _jq_select(obj: Any, path: str) -> Any:
    """Simple jq-style path selector supporting ``.field``, ``.a.b``, ``.arr[0]``."""
    if not path.startswith("."):
        return None
    parts = path[1:].split(".")
    current = obj
    for part in parts:
        if part == "":
            continue
        m = re.match(r"^(\w+)\[(-?\d+)\]$", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if isinstance(current, dict) and key in current:
                current = current[key]
                if isinstance(current, list) and -len(current) <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                return None
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _apply_json_select(lines: list[str], rules: list[FilterRule]) -> list[str] | None:
    """Apply json_select rules to the full output. Returns new lines or None if not JSON."""
    json_rules = [r for r in rules if r.type == "json_select"]
    if not json_rules:
        return None
    try:
        obj = json.loads("\n".join(lines))
    except (json.JSONDecodeError, ValueError):
        return None
    result: list[str] = []
    for r in json_rules:
        for fpath in r.fields:
            value = _jq_select(obj, fpath)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                result.append(f"{fpath}: {json.dumps(value, indent=2)}")
            else:
                result.append(f"{fpath}: {value}")
    return result if result else None


def _apply_rules(lines: list[str], rules: list[FilterRule]) -> list[str]:
    """Apply a list of filter rules to lines, returning kept lines.

    Rule application order honors ``priority`` for keep/drop rules: a higher
    priority keep rule overrides any drop rule, and a higher priority drop rule
    wins over a default-keep. Phases run in this fixed order:

    1. ``json_select`` — structured JSON field extraction (skips remaining phases if matched)
    2. ``head`` / ``tail`` — input slicing
    3. ``regex_replace`` — content substitution
    4. ``keep_section`` — region selection (start..end pattern)
    5. ``prefix_collapse`` — coalesce consecutive lines with a common prefix
    6. ``keep_matching`` / ``drop_matching`` — line-level filter (priority-aware)
    """
    # Phase 1: JSON-aware extraction (replaces all line-level processing if matched)
    json_result = _apply_json_select(lines, rules)
    if json_result is not None:
        return json_result

    keep_rules = sorted(
        [r for r in rules if r.type == "keep_matching"],
        key=lambda r: -r.priority,
    )
    drop_rules = sorted(
        [r for r in rules if r.type == "drop_matching"],
        key=lambda r: -r.priority,
    )
    replace_rules = [r for r in rules if r.type == "regex_replace"]
    tail_rules = [r for r in rules if r.type == "tail"]
    head_rules = [r for r in rules if r.type == "head"]
    section_rules = [r for r in rules if r.type == "keep_section"]
    prefix_rules = [r for r in rules if r.type == "prefix_collapse"]

    for r in head_rules:
        lines = lines[: max(0, r.lines)]
    for r in tail_rules:
        lines = lines[-max(0, r.lines):] if r.lines else []

    for r in replace_rules:
        if r._compiled and r.replacement is not None:
            lines = [r._compiled.sub(r.replacement, line) for line in lines]

    if section_rules:
        section_lines: list[str] = []
        any_section_matched = False
        for r in section_rules:
            if not (r._compiled_start and r._compiled_end):
                continue
            in_section = False
            for line in lines:
                if not in_section and r._compiled_start.search(line):
                    in_section = True
                    any_section_matched = True
                    section_lines.append(line)
                    continue
                if in_section:
                    section_lines.append(line)
                    if r._compiled_end.search(line):
                        in_section = False
        if any_section_matched:
            lines = section_lines

    if prefix_rules:
        new_lines: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            collapsed = False
            for r in prefix_rules:
                if r.prefix and line.startswith(r.prefix):
                    j = i
                    while j < len(lines) and lines[j].startswith(r.prefix):
                        j += 1
                    block = lines[i:j]
                    if len(block) > r.max_lines:
                        new_lines.extend(block[: r.max_lines])
                        new_lines.append(f"  [+{len(block) - r.max_lines} more lines with prefix {r.prefix!r}]")
                    else:
                        new_lines.extend(block)
                    i = j
                    collapsed = True
                    break
            if not collapsed:
                new_lines.append(line)
                i += 1
        lines = new_lines

    if not keep_rules and not drop_rules:
        return lines

    result: list[str] = []
    for line in lines:
        keep_priority = -1
        for r in keep_rules:
            if r._compiled and r._compiled.search(line):
                keep_priority = r.priority
                break
        drop_priority = -1
        for r in drop_rules:
            if r._compiled and r._compiled.search(line):
                drop_priority = r.priority
                break
        if keep_priority >= 0 and keep_priority >= drop_priority:
            result.append(line)
        elif drop_priority >= 0:
            continue
        else:
            result.append(line)
    return result


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _find_override(flt: CommandFilter, command: str) -> list[FilterRule] | None:
    cmd_base = command.strip().split("\n", 1)[0]
    for ov in flt.command_overrides:
        m = ov.get("match")
        if m and isinstance(m, re.Pattern) and m.search(cmd_base):
            return ov.get("rules", [])
    return None


def _adaptive_truncate(lines: list[str], max_tokens: int) -> tuple[list[str], bool]:
    """Tail-keep ``lines`` so total approximate tokens ≤ ``max_tokens``.

    Keeps the LAST lines — for CLI tools, errors and important output appear at
    the end, so tail-keeping is semantically correct.

    Returns (lines, truncated). Approximation: 1 token ≈ 4 characters.
    """
    if max_tokens <= 0:
        return lines, False
    budget_chars = max_tokens * _CHARS_PER_TOKEN
    total = sum(len(ln) + 1 for ln in lines)
    if total <= budget_chars:
        return lines, False
    # Accumulate from the end
    out: list[str] = []
    used = 0
    for ln in reversed(lines):
        cost = len(ln) + 1
        if used + cost > budget_chars:
            break
        out.append(ln)
        used += cost
    return list(reversed(out)), True


def _detect_structured(text: str) -> bool:
    """Return True if ``text`` looks like valid JSON (object or array)."""
    stripped = text.strip()
    if not stripped:
        return False
    if not (stripped[0] in ("{", "[") and stripped[-1] in ("}", "]")):
        return False
    try:
        json.loads(stripped)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def compress_output(
    command: str,
    raw_output: str,
    exit_code: int = 0,
    raw_output_id: str | None = None,
    *,
    dry_run: bool = False,
    max_input_bytes: int | None = None,
    max_tokens: int | None = None,
    strategy: str | None = None,
) -> CompressionResult:
    """Compress raw shell output using the matching filter, or a generic fallback.

    Args:
        command: The command line that produced ``raw_output``. Used to select
            a filter and override block.
        raw_output: Combined stdout+stderr.
        exit_code: Process exit code. Non-zero invokes ``[filter.on_failure]``
            rules (when present) which run after the regular rules.
        raw_output_id: Optional id from the tee store, embedded in the footer.
        dry_run: When true, fills ``CompressionResult.removed_lines`` with the
            lines that were dropped, for auditability.
        max_input_bytes: Override of :data:`MAX_INPUT_BYTES` for this call.
        max_tokens: When set, tail-truncates the kept lines so the total
            approximate token count is ≤ this value (1 token ≈ 4 chars).
        strategy: Force-select a registered Python strategy by name, bypassing
            the TOML rule engine.

    Returns:
        A :class:`CompressionResult` with the compressed text and metrics.
    """
    t0 = time.monotonic()
    cap = max_input_bytes if max_input_bytes is not None else MAX_INPUT_BYTES
    if cap and len(raw_output) > cap:
        raw_output = raw_output[:cap] + "\n" + TRUNCATION_MARKER.format(n=cap) + "\n"
        truncated = True
    else:
        truncated = False
    bytes_in = len(raw_output.encode("utf-8", errors="replace"))

    clean = _strip_ansi(raw_output)
    raw_lines = clean.splitlines()
    lines = [_truncate_line(ln) for ln in raw_lines]
    original_count = len(lines)

    flt = _registry.find(command)
    used_strategy: str | None = None
    if strategy:
        fn = _get_strategy(strategy)
        if fn:
            kept = fn(lines, command, exit_code)
            used_strategy = strategy
        else:
            log.warning("Strategy %r not registered; falling back to default", strategy)
            kept = _default_compress(flt, command, lines, exit_code)
    elif flt and flt.strategy:
        fn = _get_strategy(flt.strategy)
        if fn:
            kept = fn(lines, command, exit_code)
            used_strategy = flt.strategy
        else:
            log.warning("Strategy %r referenced by filter %s is not registered", flt.strategy, flt.name)
            kept = _default_compress(flt, command, lines, exit_code)
    else:
        kept = _default_compress(flt, command, lines, exit_code)

    deduped = _dedup_consecutive(kept)

    if max_tokens is not None and max_tokens > 0:
        deduped, tt = _adaptive_truncate(deduped, max_tokens)
        if tt:
            truncated = True
            deduped.insert(0, f"[ctxclp: output tail-trimmed to ≤{max_tokens} tokens; earlier lines omitted]")

    compressed = "\n".join(deduped)
    is_structured = _detect_structured(compressed)
    elapsed = round((time.monotonic() - t0) * 1000, 2)

    removed: list[tuple[int, str]] | None = None
    dropped_errors: list[str] | None = None
    if dry_run:
        kept_set: set[str] = set(deduped)
        removed = [
            (i + 1, ln) for i, ln in enumerate(raw_lines) if ln not in kept_set
        ]
        dropped_errors = [ln for _, ln in removed if _ERROR_SIGNALS.search(ln)]

    return CompressionResult(
        compressed=compressed,
        original_lines=original_count,
        kept_lines=len(deduped),
        raw_output_id=raw_output_id,
        elapsed_ms=elapsed,
        removed_lines=removed,
        truncated=truncated,
        strategy_name=used_strategy,
        bytes_in=bytes_in,
        bytes_out=len(compressed.encode("utf-8", errors="replace")),
        is_structured=is_structured,
        filter_name=flt.name if flt else None,
        dropped_error_lines=dropped_errors,
    )


def _default_compress(
    flt: CommandFilter | None,
    command: str,
    lines: list[str],
    exit_code: int,
) -> list[str]:
    if flt:
        rules = _find_override(flt, command)
        if rules is None:
            rules = flt.rules
        kept = _apply_rules(lines, rules)
        if exit_code != 0 and flt.on_failure_rules:
            kept = _apply_rules(kept, flt.on_failure_rules)
        return kept
    return [ln for ln in lines if ln.strip()]


def _dedup_consecutive(lines: list[str]) -> list[str]:
    deduped: list[str] = []
    prev: str | None = None
    repeat = 0
    for ln in lines:
        if ln == prev:
            repeat += 1
        else:
            if repeat > 0:
                deduped.append(f"  [above line repeated {repeat}×]")
                repeat = 0
            deduped.append(ln)
            prev = ln
    if repeat > 0:
        deduped.append(f"  [above line repeated {repeat}×]")
    return deduped
