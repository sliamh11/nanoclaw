"""Agent-native I/O helpers for Deus Python CLIs.

Provides TTY detection, JSON compact mode, and field selection.
See docs/decisions/printing-press-adoption.md for the protocol spec.
"""

from __future__ import annotations

import json
import os
from typing import Any


def is_agent_context() -> bool:
    """Return True when the caller is an agent expecting structured output.

    Currently checks DEUS_AGENT_NATIVE=1 only. TTY auto-detection is deferred
    to avoid breaking memory_benchmark.py which parses human-readable stdout
    from piped subprocesses. When TTY detection is added, guard with
    try/except for OSError on platforms where fileno() is unavailable.
    """
    return os.environ.get("DEUS_AGENT_NATIVE") == "1"


def compact_json(
    obj: Any,
    long_fields: tuple[str, ...] = (),
    truncate_at: int = 200,
) -> Any:
    """Strip None values and truncate long string fields for token efficiency."""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if v is None:
                continue
            if k in long_fields and isinstance(v, str) and len(v) > truncate_at:
                result[k] = v[:truncate_at] + "..."
            else:
                result[k] = compact_json(v, long_fields, truncate_at)
        return result
    if isinstance(obj, list):
        return [compact_json(item, long_fields, truncate_at) for item in obj]
    return obj


def select_fields(obj: Any, fields: str | None) -> Any:
    """Filter a dict or list of dicts to only the specified field paths.

    fields is a comma-separated string of dot-paths, e.g. "path,score,meta.tag".
    Returns the object unchanged if fields is None.
    """
    if fields is None:
        return obj

    paths = [f.strip() for f in fields.split(",") if f.strip()]
    if not paths:
        return obj

    if isinstance(obj, list):
        return [select_fields(item, fields) for item in obj]

    if not isinstance(obj, dict):
        return obj

    grouped: dict[str, list[str]] = {}
    direct_keys: list[str] = []
    for path in paths:
        parts = path.split(".", 1)
        key = parts[0]
        if key not in obj:
            continue
        if len(parts) == 1:
            direct_keys.append(key)
        else:
            grouped.setdefault(key, []).append(parts[1])

    result: dict[str, Any] = {}
    for key in direct_keys:
        result[key] = obj[key]
    for key, sub_paths in grouped.items():
        nested = obj[key]
        sub_select = ",".join(sub_paths)
        if isinstance(nested, list):
            result[key] = [select_fields(item, sub_select) for item in nested]
        elif isinstance(nested, dict):
            result[key] = select_fields(nested, sub_select)
        else:
            result[key] = nested
    return result


def agent_output(
    obj: Any,
    *,
    use_json: bool = False,
    compact: bool = False,
    select: str | None = None,
    long_fields: tuple[str, ...] = (),
    truncate_at: int = 200,
) -> str | None:
    """Format output for agent consumption. Returns JSON string, or None if not in agent mode."""
    if not use_json:
        return None
    data = obj
    if compact:
        data = compact_json(data, long_fields, truncate_at)
    if select:
        data = select_fields(data, select)
    return json.dumps(data)
