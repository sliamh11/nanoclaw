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
    # 1. Explicit env override wins (matches memory_tree.EXTERNAL_DIR_ENV).
    env = os.environ.get("DEUS_AUTO_MEMORY_DIR")
    if env:
        return Path(env)
    # 2. Derive the per-project auto-memory dir from CLAUDE_PROJECT_DIR.
    #    Mirrors memory_indexer.py promotion target (~/.claude/projects/<encoded>/memory).
    #    Encoding: leading '-' + slashes replaced with '-'.
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        encoded = project_dir.replace("/", "-")
        if not encoded.startswith("-"):
            encoded = "-" + encoded
        candidate = Path(os.path.expanduser(f"~/.claude/projects/{encoded}/memory"))
        if candidate.is_dir():
            return candidate
    # 3. Derive from this script's filesystem location (repo_root/scripts/standards_pack.py).
    repo_root = Path(__file__).resolve().parent.parent
    encoded = repo_root.as_posix().replace("/", "-")
    legacy = Path(os.path.expanduser(f"~/.claude/projects/{encoded}/memory"))
    if legacy.is_dir():
        return legacy
    # 4. Final fallback (will trigger fail-loud warning in load_standards).
    return Path(os.path.expanduser("~/.deus/auto-memory"))


AUTO_MEM_DIR = _default_auto_mem_dir()

CACHE_PATH = Path(os.environ.get(
    "DEUS_STANDARDS_CACHE",
    os.path.expanduser("~/.deus/standards_pack_cache.json"),
))

# Cap on the total tokens of `kind: standard` atom one-liners injected as
# always-on context at SessionStart. On overrun, atoms are dropped in
# directory-sort order and a stderr WARN is emitted — silent drops have
# historically lost non-negotiable methodology rules.
#
# Not to be confused with `memory_tree.py:ROOT_TOKEN_BUDGET` — that caps
# MEMORY_TREE.md size for the navigation root, a different system.
TOKEN_BUDGET = int(os.environ.get("DEUS_STANDARDS_TOKEN_BUDGET", "1200"))

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
        # Fail-loud to stderr (never stdout — would pollute the JSON hook output).
        # Helps catch the silent-empty-pack failure mode that masked PR #380's gap.
        print(
            f"[standards_pack] WARN: auto-memory dir not found: {d}. "
            "Standards pack will be empty. Check DEUS_AUTO_MEMORY_DIR or CLAUDE_PROJECT_DIR.",
            file=sys.stderr,
        )
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

    dropped: list[str] = []
    dropped_tokens = 0
    truncated_at: int | None = None
    for idx, (name, desc) in enumerate(atoms):
        oneliner = f"- {name}: {desc}" if desc else f"- {name}"
        cost = _token_estimate(oneliner)
        if total_tokens + cost > token_budget:
            truncated_at = idx
            break
        lines.append(oneliner)
        total_tokens += cost

    if truncated_at is not None:
        # Collect every atom that didn't make it in (not just the first overrun).
        # Second pass over the already-built `atoms` list keeps the include set
        # exactly what the original first-fit loop produced — we only iterate
        # past the cutoff to report names.
        for name, desc in atoms[truncated_at:]:
            oneliner = f"- {name}: {desc}" if desc else f"- {name}"
            dropped.append(name)
            dropped_tokens += _token_estimate(oneliner)
        # Fail-loud: silent drops previously hid the loss of non-negotiable
        # methodology rules (e.g. `feedback_warden_loop`). Emit to stderr so
        # the hook output stays JSON-clean on stdout.
        print(
            f"[standards_pack] WARN: budget exceeded — dropped {len(dropped)} atom(s) "
            f"totalling {dropped_tokens} tokens: {', '.join(dropped)}. "
            f"Raise DEUS_STANDARDS_TOKEN_BUDGET (current: {token_budget}) or "
            f"reduce atom count.",
            file=sys.stderr,
        )

    if not lines:
        # Fail-loud: dir exists but no kind=standard atoms found. Likely classification gap.
        print(
            f"[standards_pack] WARN: 0 kind=standard atoms in {d} "
            f"(scanned {sum(1 for _ in d.glob('*.md'))} .md files). "
            "Standards pack will be empty.",
            file=sys.stderr,
        )
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
            "dropped": dropped,
            "dropped_tokens": dropped_tokens,
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
