#!/usr/bin/env python3
"""One-time migration: classify and tag atoms with kind: standard|knowledge.

Usage:
    python3 migrate_atom_tiers.py --dir ~/.claude/projects/.../memory   # dry-run (default)
    python3 migrate_atom_tiers.py --dir ... --apply                      # write changes
    python3 migrate_atom_tiers.py --dir ... --rollback                   # remove kind: field
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROCESS_VERBS = {
    "parallelize", "evaluate", "predict", "measure", "debug",
    "diagnose", "estimate", "research", "verify", "validate",
}

STANDARD_TAGS = {"methodology"}

STANDARD_NAMES = {
    "feedback_parallel_background.md",
    "feedback_evaluate_execution_strategy.md",
    "feedback_predict_before_testing.md",
    "feedback_debugging_methodology.md",
    "feedback_deep_research_workflow.md",
    "feedback_check_real_logs_first.md",
    "feedback_no_speculation.md",
    "feedback_branch_workflow.md",
    "feedback_scope_commits_by_concern.md",
    "feedback_security_first.md",
    "feedback_dev_workflow.md",
    "feedback_socratic_mindset.md",
}

_FM_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def classify_atom(path: Path) -> tuple[str, str]:
    """Returns (kind, reasoning) for an atom file."""
    if path.name in STANDARD_NAMES:
        return ("standard", "explicit list")

    content = path.read_text(encoding="utf-8", errors="replace")
    m = _FM_RE.match(content)
    if not m:
        return ("knowledge", "no frontmatter")

    fm = m.group(1)

    tags_match = re.search(r"^tags:\s*\[(.*?)\]", fm, re.MULTILINE)
    tags = set()
    if tags_match:
        tags = {t.strip().strip("'\"") for t in tags_match.group(1).split(",")}

    if tags & STANDARD_TAGS:
        return ("standard", f"tags overlap: {tags & STANDARD_TAGS}")

    name_match = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
    desc_match = re.search(r"^description:\s*>?\s*\n?\s*(.+?)(?=\n\S|\n---|\Z)", fm, re.MULTILINE | re.DOTALL)
    name = name_match.group(1).strip().lower() if name_match else ""
    desc = re.sub(r"\n\s+", " ", desc_match.group(1)).strip().lower() if desc_match else ""
    combined = name + " " + desc

    found_verbs = {v for v in PROCESS_VERBS if v in combined}
    if len(found_verbs) >= 2:
        return ("standard", f"process verbs: {found_verbs}")

    return ("knowledge", "no methodology signals")


def migrate_atoms(
    auto_mem_dir: Path,
    *,
    apply: bool = False,
) -> dict[str, list[str]]:
    """Scan atoms, classify, and optionally write kind: field."""
    result: dict[str, list[str]] = {"standard": [], "knowledge": []}

    for f in sorted(auto_mem_dir.glob("*.md")):
        kind, reason = classify_atom(f)
        result[kind].append(f.name)
        print(f"  {'[S]' if kind == 'standard' else '[K]'} {f.name:50s} {reason}")

        if apply:
            content = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^kind:\s", content, re.MULTILINE):
                content = re.sub(r"^kind:\s+.+$", f"kind: {kind}", content, count=1, flags=re.MULTILINE)
            else:
                content = content.replace("---\n", f"---\nkind: {kind}\n", 1)
            f.write_text(content, encoding="utf-8")

    return result


def rollback_atoms(auto_mem_dir: Path) -> int:
    """Remove kind: field from all atom files."""
    count = 0
    for f in sorted(auto_mem_dir.glob("*.md")):
        content = f.read_text(encoding="utf-8", errors="replace")
        new_content = re.sub(r"^kind:\s+.+\n", "", content, count=1, flags=re.MULTILINE)
        if new_content != content:
            f.write_text(new_content, encoding="utf-8")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify atoms into standard/knowledge tiers")
    parser.add_argument("--dir", required=True, help="Path to auto-memory directory")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--rollback", action="store_true", help="Remove kind: field from all atoms")
    args = parser.parse_args()

    d = Path(args.dir).expanduser()
    if not d.is_dir():
        print(f"Error: {d} is not a directory", file=sys.stderr)
        return 1

    if args.rollback:
        count = rollback_atoms(d)
        print(f"Rolled back kind: field from {count} files")
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== Atom Tier Migration ({mode}) ===\n")

    result = migrate_atoms(d, apply=args.apply)

    print(f"\n--- Summary ---")
    print(f"Standard: {len(result['standard'])} atoms")
    print(f"Knowledge: {len(result['knowledge'])} atoms")

    if not args.apply:
        print(f"\nRun with --apply to write changes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
