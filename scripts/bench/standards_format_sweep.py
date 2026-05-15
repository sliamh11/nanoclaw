#!/usr/bin/env python3
"""M1a benchmark — sweep standards_pack (format, budget) → tier1_coverage.

Sweeps `(format ∈ {name_only, name_desc}) × (budget ∈ {800, 1200, 1500, 2000})`
against the methodology probe fixture. Pure file-scan + set membership — no
APIs, no DB queries, no LLM calls.

Replicates the production packing loop instead of calling `load_standards()`:
- need filenames preserved (production discards them after `_parse_name_desc`)
- format toggle does not yet exist in production code (M3 will add it)
- avoid the production cache invalidating across iterations

Results land at `scripts/bench/results/standards_format_sweep_YYYY-MM-DD.json`.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# Anchor every path to __file__ (not cwd). Pattern matches
# `scripts/bench/suites/memory_tree.py:_load_mt()`.
_BENCH_DIR = Path(__file__).resolve().parent          # scripts/bench/
_SCRIPTS_DIR = _BENCH_DIR.parent                       # scripts/
_REPO_ROOT = _SCRIPTS_DIR.parent                       # ~/deus/
_SP_PATH = _SCRIPTS_DIR / "standards_pack.py"
_DEFAULT_PROBES = _SCRIPTS_DIR / "tests" / "fixtures" / "methodology_probes.jsonl"
_DEFAULT_OUTPUT_DIR = _BENCH_DIR / "results"

# Cells where tier1_coverage is expected to hold these floors. If a cell falls
# below its floor, the script emits a stderr WARN — does NOT fail the run.
# Floors derived from PR #413 verification: all 27 standard atoms fit at 1200
# budget (876 tokens); name_only is ~12 tokens/atom so 24 STANDARD_NAMES fit
# easily even at 800.
_FLOOR_BY_CELL = {
    ("name_only", 800): 0.95,
    ("name_only", 1200): 0.95,
    ("name_only", 1500): 0.95,
    ("name_only", 2000): 0.95,
    ("name_desc", 800): 0.90,
    ("name_desc", 1200): 1.00,
    ("name_desc", 1500): 1.00,
    ("name_desc", 2000): 1.00,
}


def _load_sp():
    """Load standards_pack as a module without touching production cwd state."""
    if "standards_pack" in sys.modules:
        return sys.modules["standards_pack"]
    spec = importlib.util.spec_from_file_location("standards_pack", _SP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load standards_pack from {_SP_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["standards_pack"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_probes(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _methodology_probes(probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to the methodology slice (tier1_should_cover: true)."""
    return [p for p in probes if p.get("tier1_should_cover")]


def _resolve_auto_mem_dir(sp, override: str | None) -> Path:
    """Resolve auto-memory dir with explicit override beating sp defaults."""
    if override:
        return Path(override).expanduser()
    # Fall back to the production resolver — same chain SessionStart uses.
    return sp._default_auto_mem_dir()


def _run_one(
    sp,
    auto_mem_dir: Path,
    format_name: str,
    budget: int,
    methodology_probes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replicate the production packing loop with filename tracking.

    The production loop in standards_pack.py builds (name, desc) tuples and
    discards filenames before the budget pass — we need filenames here to
    compute set membership against the probe's expected_path values.
    """
    # Phase 1: scan + parse. Carry the file path through.
    atoms: list[tuple[Path, str, str]] = []
    for f in sorted(auto_mem_dir.glob("*.md")):
        content = f.read_text(encoding="utf-8", errors="replace")
        if sp._parse_kind(content) != "standard":
            continue
        name, desc = sp._parse_name_desc(content)
        if not name:
            continue
        atoms.append((f, name, desc))

    # Phase 2: first-fit against budget. Mirror production semantics exactly
    # (break on overrun; alphabetical-by-filename via `sorted()` above).
    total_tokens = 0
    included_filenames: list[str] = []
    dropped_filenames: list[str] = []
    truncated_at: int | None = None
    for idx, (f, name, desc) in enumerate(atoms):
        if format_name == "name_only":
            oneliner = f"- {name}"
        elif format_name == "name_desc":
            oneliner = f"- {name}: {desc}" if desc else f"- {name}"
        else:
            raise ValueError(f"unknown format: {format_name!r}")
        cost = sp._token_estimate(oneliner)
        if total_tokens + cost > budget:
            truncated_at = idx
            break
        included_filenames.append(f.name)
        total_tokens += cost

    if truncated_at is not None:
        for f, _, _ in atoms[truncated_at:]:
            dropped_filenames.append(f.name)

    # Phase 3: tier1_coverage against the probe fixture.
    standards_paths = {f"auto-memory/{n}" for n in included_filenames}
    tier1_hits = sum(
        1 for p in methodology_probes if p["expected_path"] in standards_paths
    )
    tier1_coverage = tier1_hits / len(methodology_probes) if methodology_probes else 0.0

    return {
        "format": format_name,
        "budget": budget,
        "tokens_used": total_tokens,
        "atoms_included": len(included_filenames),
        "atoms_dropped": len(dropped_filenames),
        "tier1_coverage": round(tier1_coverage, 4),
        "tier1_hits": tier1_hits,
        "n_methodology_probes": len(methodology_probes),
        "included_paths": included_filenames,
        "dropped_paths": dropped_filenames,
    }


def _check_floor(row: dict[str, Any]) -> str | None:
    """Return a WARN string if the row falls below its expected floor."""
    key = (row["format"], row["budget"])
    floor = _FLOOR_BY_CELL.get(key)
    if floor is None:
        return None
    if row["tier1_coverage"] < floor:
        return (
            f"[BENCH] WARN: format={row['format']} budget={row['budget']} "
            f"tier1_coverage={row['tier1_coverage']:.4f} below floor {floor:.2f}"
        )
    return None


def _git_sha() -> str:
    """Best-effort current git SHA. Returns 'unknown' on any failure.

    Uses subprocess directly instead of importing bench.store at module level
    because this helper runs before `--save` triggers the sys.path insert.
    """
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip() or "unknown"
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="standards_format_sweep",
        description="Sweep standards_pack (format, budget) → tier1_coverage.",
    )
    parser.add_argument(
        "--budgets",
        default="800,1200,1500,2000",
        help="Comma-separated budgets (default: 800,1200,1500,2000)",
    )
    parser.add_argument(
        "--formats",
        default="name_only,name_desc",
        help="Comma-separated formats (default: name_only,name_desc)",
    )
    parser.add_argument(
        "--probes",
        type=Path,
        default=_DEFAULT_PROBES,
        help=f"Path to probe fixture (default: {_DEFAULT_PROBES})",
    )
    parser.add_argument(
        "--auto-mem-dir",
        default=None,
        help="Override auto-memory dir (default: standards_pack._default_auto_mem_dir())",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Output JSON directory (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Also persist a RunResult to ~/.deus/bench/runs.db",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Label for the persisted run (only used with --save)",
    )
    args = parser.parse_args(argv)

    sp = _load_sp()
    auto_mem_dir = _resolve_auto_mem_dir(sp, args.auto_mem_dir)
    if not auto_mem_dir.is_dir():
        print(
            f"[bench] ERR: auto-memory dir not found: {auto_mem_dir}. "
            "Set DEUS_AUTO_MEMORY_DIR or pass --auto-mem-dir.",
            file=sys.stderr,
        )
        return 1

    probes = _load_probes(args.probes)
    methodology = _methodology_probes(probes)
    if not methodology:
        print(
            f"[bench] ERR: 0 methodology probes in {args.probes} "
            "(expected at least one entry with tier1_should_cover: true)",
            file=sys.stderr,
        )
        return 1

    budgets = [int(b) for b in args.budgets.split(",") if b.strip()]
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    rows: list[dict[str, Any]] = []
    for fmt in formats:
        for budget in budgets:
            row = _run_one(sp, auto_mem_dir, fmt, budget, methodology)
            rows.append(row)
            warn = _check_floor(row)
            if warn:
                print(warn, file=sys.stderr)
            print(
                f"format={fmt:9s} budget={budget:>5d}  "
                f"tokens={row['tokens_used']:>5d}  "
                f"included={row['atoms_included']:>3d}  "
                f"dropped={row['atoms_dropped']:>3d}  "
                f"tier1_coverage={row['tier1_coverage']:.4f}"
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.date.today().isoformat()
    out_path = args.output_dir / f"standards_format_sweep_{date_str}.json"
    payload = {
        "run_date": date_str,
        "git_sha": _git_sha(),
        "probes_path": str(args.probes),
        "auto_mem_dir": str(auto_mem_dir),
        "n_methodology_probes": len(methodology),
        "rows": rows,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nresults written to {out_path}", file=sys.stderr)

    if args.save:
        # Add scripts/ to sys.path so `from bench.store import save_run` works.
        # The `bench` package has `__init__.py`; `store.py` uses package-relative
        # `from .types import RunResult` internally so it must be imported as a
        # package member, not via a bare importlib load of the file.
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        from bench.store import save_run  # noqa: E402
        from bench.types import CaseResult, RunResult  # noqa: E402

        cases = [
            CaseResult(
                case_id=f"{r['format']}@{r['budget']}",
                score=r["tier1_coverage"],
                meta={
                    "tokens_used": r["tokens_used"],
                    "atoms_included": r["atoms_included"],
                    "atoms_dropped": r["atoms_dropped"],
                },
            )
            for r in rows
        ]
        run = RunResult(
            suite="standards_format_sweep",
            score=sum(c.score for c in cases) / len(cases) if cases else 0.0,
            cases=cases,
            meta={"probes_path": str(args.probes), "auto_mem_dir": str(auto_mem_dir)},
        )
        run_id = save_run(run, label=args.label)
        print(f"persisted run_id={run_id} to bench DB", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
