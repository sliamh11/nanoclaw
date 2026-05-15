from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "settings_merge.py"


def _load_module():
    if "settings_merge" in sys.modules:
        return sys.modules["settings_merge"]
    spec = importlib.util.spec_from_file_location("settings_merge", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["settings_merge"] = mod
    spec.loader.exec_module(mod)
    return mod


SM = _load_module()


def _write_settings(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── Deep merge unit tests ────────────────────────────────────────────────────


def test_deep_merge_preserves_top_level_keys(tmp_path):
    settings = tmp_path / "s.json"
    _write_settings(settings, {"model": "opus", "env": {"A": "1"}, "hooks": {}})

    result = SM.merge_settings(settings, {"model": "sonnet"})

    assert result["model"] == "sonnet"
    assert result["env"] == {"A": "1"}
    assert result["hooks"] == {}


def test_deep_merge_recurses_dicts(tmp_path):
    settings = tmp_path / "s.json"
    _write_settings(settings, {"env": {"A": "1", "B": "2"}})

    result = SM.merge_settings(settings, {"env": {"B": "3", "C": "4"}})

    assert result["env"] == {"A": "1", "B": "3", "C": "4"}


# ── Hooks array merge ────────────────────────────────────────────────────────


def test_hooks_array_merge_appends_unique(tmp_path):
    settings = tmp_path / "s.json"
    existing_hook = {
        "hooks": [{"type": "command", "command": "python3 /path/to/hook_a.py"}]
    }
    new_hook = {
        "hooks": [{"type": "command", "command": "python3 /path/to/hook_b.py"}]
    }
    _write_settings(settings, {"hooks": {"Stop": [existing_hook]}})

    result = SM.merge_settings(settings, {"hooks": {"Stop": [new_hook]}})
    assert len(result["hooks"]["Stop"]) == 2
    commands = [
        h["command"]
        for group in result["hooks"]["Stop"]
        for h in group["hooks"]
    ]
    assert "python3 /path/to/hook_a.py" in commands
    assert "python3 /path/to/hook_b.py" in commands

    result2 = SM.merge_settings(settings, {"hooks": {"Stop": [new_hook]}})
    assert len(result2["hooks"]["Stop"]) == 2


def test_hooks_preserves_matchers(tmp_path):
    settings = tmp_path / "s.json"
    group_a = {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "a"}]}
    group_b = {"matcher": "Bash", "hooks": [{"type": "command", "command": "b"}]}
    _write_settings(settings, {"hooks": {"PreToolUse": [group_a]}})

    result = SM.merge_settings(settings, {"hooks": {"PreToolUse": [group_b]}})
    assert len(result["hooks"]["PreToolUse"]) == 2


# ── Permissions array merge ──────────────────────────────────────────────────


def test_permissions_array_merge(tmp_path):
    settings = tmp_path / "s.json"
    _write_settings(settings, {"permissions": {"allow": ["Read", "Edit"]}})

    result = SM.merge_settings(settings, {"permissions": {"allow": ["Bash(*)"]}})

    assert result["permissions"]["allow"] == ["Read", "Edit", "Bash(*)"]


def test_permissions_dedup(tmp_path):
    settings = tmp_path / "s.json"
    _write_settings(settings, {"permissions": {"allow": ["Read", "Edit"]}})

    result = SM.merge_settings(settings, {"permissions": {"allow": ["Read"]}})
    assert result["permissions"]["allow"] == ["Read", "Edit"]


# ── File lifecycle ───────────────────────────────────────────────────────────


def test_merge_into_nonexistent_file(tmp_path):
    settings = tmp_path / "subdir" / "settings.json"

    result = SM.merge_settings(settings, {"model": "opus", "hooks": {}})

    assert settings.exists()
    assert result == {"model": "opus", "hooks": {}}
    assert _read_settings(settings) == result


# ── Concurrent writes ────────────────────────────────────────────────────────


def test_concurrent_write_race_deterministic(tmp_path):
    """Spawn N threads that each merge a unique key. All keys must survive.

    This is a smoke test for flock serialization. The cross-process guarantee
    (the load-bearing case) relies on fcntl.flock kernel semantics; this test
    exercises the read-merge-write cycle under contention.
    """
    settings = tmp_path / "s.json"
    _write_settings(settings, {})

    n = 10
    errors: list[Exception] = []

    def merge_key(i: int) -> None:
        try:
            SM.merge_settings(settings, {f"key_{i}": i})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=merge_key, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Merge errors: {errors}"
    final = _read_settings(settings)
    for i in range(n):
        assert f"key_{i}" in final, f"key_{i} missing from merged result"
        assert final[f"key_{i}"] == i


# ── Crash consistency ────────────────────────────────────────────────────────


def test_atomic_write_crash_leaves_file_consistent(tmp_path):
    settings = tmp_path / "s.json"
    original = {"model": "opus", "hooks": {"Stop": []}}
    _write_settings(settings, original)

    original_text = settings.read_text(encoding="utf-8")

    with patch.object(SM.os, "replace", side_effect=OSError("simulated crash")):
        with pytest.raises(OSError, match="simulated crash"):
            SM.merge_settings(settings, {"model": "sonnet"})

    assert settings.read_text(encoding="utf-8") == original_text
    temps = list(tmp_path.glob("settings_merge_*"))
    assert not temps, f"orphaned temp files: {temps}"
