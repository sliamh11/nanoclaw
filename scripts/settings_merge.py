#!/usr/bin/env python3
"""
Locked read-merge-write for ~/.claude/settings.json.

Acquires an exclusive flock, reads the current file, deep-merges changes
(hooks and permission arrays are appended with dedup, never replaced),
and writes atomically via a temp file + os.replace.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path("~/.claude/settings.json").expanduser()
# flock is per-process; this lock serializes threads within the same process.
_THREAD_LOCK = threading.Lock()


def _canonical(item: Any) -> str:
    if isinstance(item, dict):
        return json.dumps(item, sort_keys=True)
    return json.dumps(item)


def _array_merge(base: list, override: list) -> list:
    seen = {_canonical(x) for x in base}
    merged = list(base)
    for item in override:
        key = _canonical(item)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _is_array_merge_key(path: tuple[str, ...]) -> bool:
    if len(path) >= 2 and path[0] == "hooks":
        return True
    if path == ("permissions", "allow") or path == ("permissions", "deny"):
        return True
    return False


def _deep_merge(
    base: dict[str, Any],
    override: dict[str, Any],
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    result = dict(base)
    for key, val in override.items():
        current_path = path + (key,)
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val, current_path)
        elif (
            key in result
            and isinstance(result[key], list)
            and isinstance(val, list)
            and _is_array_merge_key(current_path)
        ):
            result[key] = _array_merge(result[key], val)
        else:
            result[key] = val
    return result


def merge_settings(path: Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Acquire flock, read current JSON, deep-merge changes, atomic write."""
    if sys.platform == "win32":
        raise NotImplementedError("settings_merge requires fcntl (macOS/Linux)")

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _THREAD_LOCK:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = b""
            while True:
                chunk = os.read(fd, 8192)
                if not chunk:
                    break
                raw += chunk

            current: dict[str, Any] = {}
            if raw.strip():
                current = json.loads(raw)

            merged = _deep_merge(current, changes)

            text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=path.parent, delete=False,
                    prefix="settings_merge_",
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(text)
                os.replace(tmp_path, path)
            except Exception:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
                raise
        finally:
            os.close(fd)

    return merged


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Merge changes into settings.json")
    parser.add_argument("--path", type=Path, default=_DEFAULT_PATH)
    args = parser.parse_args()

    changes = json.load(sys.stdin)
    result = merge_settings(args.path, changes)
    print(json.dumps(result, indent=2))
