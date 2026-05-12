#!/usr/bin/env python3
"""SessionStart hook: inject persistent working standards.

Scans auto-memory atoms for kind=standard, formats them as condensed
one-liners, and emits as additionalContext with directive framing.
Cached by directory mtime to avoid re-scanning 130+ files on every session.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

def _default_auto_mem_dir() -> Path:
    env = os.environ.get("DEUS_AUTO_MEMORY_DIR")
    if env:
        return Path(env)
    try:
        _scripts = Path(__file__).resolve().parent
        sys.path.insert(0, str(_scripts))
        import memory_tree as mt
        return Path(mt.EXTERNAL_DIR)
    except Exception:
        return Path(os.path.expanduser("~/.deus/auto-memory"))


AUTO_MEM_DIR = _default_auto_mem_dir()

CACHE_PATH = Path(os.environ.get(
    "DEUS_STANDARDS_CACHE",
    os.path.expanduser("~/.deus/standards_pack_cache.json"),
))

TOKEN_BUDGET = int(os.environ.get("DEUS_STANDARDS_TOKEN_BUDGET", "800"))

_FM_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def _parse_kind(content: str) -> str | None:
    m = _FM_RE.match(content)
    if not m:
        return None
    km = re.search(r"^kind:\s*(.+)$", m.group(1), re.MULTILINE)
    return km.group(1).strip() if km else None


def _parse_name_desc(content: str) -> tuple[str, str]:
    m = _FM_RE.match(content)
    if not m:
        return ("", "")
    fm = m.group(1)
    name = ""
    desc = ""
    nm = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
    if nm:
        name = nm.group(1).strip()
    dm = re.search(r"^description:\s*>?\s*\n?\s*(.+?)(?=\n\S|\n---|\Z)", fm, re.MULTILINE | re.DOTALL)
    if dm:
        desc = re.sub(r"\n\s+", " ", dm.group(1)).strip()
    return (name, desc)


def _token_estimate(text: str) -> int:
    return int(len(text.split()) * 1.3)


def _dir_mtime(d: Path) -> float:
    try:
        return d.stat().st_mtime
    except OSError:
        return 0.0


def load_standards(auto_mem_dir: Path | None = None, token_budget: int = TOKEN_BUDGET) -> str:
    """Load kind=standard atoms as condensed one-liners within token budget."""
    d = auto_mem_dir or AUTO_MEM_DIR
    if not d.is_dir():
        return ""

    mtime = _dir_mtime(d)
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
            if cached.get("mtime") == mtime and cached.get("budget") == token_budget:
                return cached.get("context", "")
        except (json.JSONDecodeError, OSError):
            pass

    lines: list[str] = []
    total_tokens = 0

    atoms: list[tuple[str, str]] = []
    for f in sorted(d.glob("*.md")):
        content = f.read_text(encoding="utf-8", errors="replace")
        kind = _parse_kind(content)
        if kind != "standard":
            continue
        name, desc = _parse_name_desc(content)
        if name:
            atoms.append((name, desc))

    for name, desc in atoms:
        oneliner = f"- {name}: {desc}" if desc else f"- {name}"
        cost = _token_estimate(oneliner)
        if total_tokens + cost > token_budget:
            break
        lines.append(oneliner)
        total_tokens += cost

    if not lines:
        return ""

    context = (
        "=== Working Standards (apply to ALL actions this session) ===\n"
        "These are verified methodology rules. Follow them reflexively.\n"
        + "\n".join(lines)
        + "\n=== End Working Standards ==="
    )

    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps({
            "mtime": mtime,
            "budget": token_budget,
            "context": context,
            "atom_count": len(lines),
            "tokens": total_tokens,
        }))
    except OSError:
        pass

    return context


def main() -> None:
    try:
        sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        pass

    context = load_standards()
    if not context:
        return

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"[deus standards-pack] {e}\n")
    sys.exit(0)
