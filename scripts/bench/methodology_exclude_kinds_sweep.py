#!/usr/bin/env python3
"""M4-prereq benchmark — gate `exclude_kinds={'standard'}` lift decision.

Empirically answers: "If we drop the default `exclude_kinds={'standard'}` from
`memory_query.recall()`, does methodology recall@3 stay >= 0.80 AND does the
false-positive rate (FPR) stay <= 0.10?"

Two conditions per probe:
- A (baseline): exclude_kinds={"standard"}  — current production
- B (M4 candidate): exclude_kinds=set()      — standards re-enabled

Outputs:
- stdout table (condition × recall@3 × fpr × rank1_std × verdict)
- JSON to scripts/bench/results/methodology_exclude_kinds_<DATE>.json
- stderr verdict line: "[M4-PREREQ] PASS|FAIL (recall=X, fpr=Y)"
- Exit 0 on normal verdict path; exit 1 on missing inputs.

Requires Linux/macOS — inherits the win32 fast-fail from `memory_query.py`.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

# Anchor every path to __file__ (not cwd). Mirrors
# `scripts/bench/standards_format_sweep.py:27-32`.
_BENCH_DIR = Path(__file__).resolve().parent          # scripts/bench/
_SCRIPTS_DIR = _BENCH_DIR.parent                       # scripts/
_REPO_ROOT = _SCRIPTS_DIR.parent                       # ~/deus/
_MQ_PATH = _SCRIPTS_DIR / "memory_query.py"
_SP_PATH = _SCRIPTS_DIR / "standards_pack.py"
_DEFAULT_PROBES = _SCRIPTS_DIR / "tests" / "fixtures" / "methodology_probes.jsonl"
_DEFAULT_OUTPUT_DIR = _BENCH_DIR / "results"

# Gate thresholds (informational — printed alongside verdict).
_RECALL_GATE = 0.80
_FPR_GATE = 0.10

# Absolute sanity floor for condition A. Below this we WARN — either the
# probe-path equality test is broken (path-format drift) or the retrieval
# pipeline has regressed. Anchored to "can't beat coin-flip on top-3" rather
# than a remembered baseline, so the threshold is defensible without citing
# an unverified historical number.
_RECALL_SANITY_FLOOR = 0.50

# Decision-gate verdict labels.
_PASS = "PASS"
_FAIL = "FAIL"


# memory_query imports memory_tree at module level. Insert scripts/ onto sys.path
# BEFORE the importlib load so that `import memory_tree as mt` resolves. Guarded
# to avoid double-registration in test harnesses that pre-load these modules.
_scripts_str = str(_SCRIPTS_DIR)
if _scripts_str not in sys.path:
    sys.path.insert(0, _scripts_str)


def _load_mq():
    """Load memory_query as a module without forcing production cwd state."""
    if "memory_query" in sys.modules:
        return sys.modules["memory_query"]
    spec = importlib.util.spec_from_file_location("memory_query", _MQ_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load memory_query from {_MQ_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memory_query"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_sp():
    """Load standards_pack as a module (for _parse_kind, _default_auto_mem_dir)."""
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


def _resolve_auto_mem_dir(sp, override: str | None) -> Path:
    """Resolve auto-memory dir. CLI override wins; otherwise use sp default chain."""
    if override:
        return Path(override).expanduser()
    return sp._default_auto_mem_dir()


def _kind_of(auto_mem_dir: Path, ns_path: str, ext_ns: str, sp) -> str | None:
    """Resolve an `auto-memory/<file>` namespaced path to its frontmatter kind.

    Returns None if (a) the path is not under the external namespace, or
    (b) the file cannot be read. Callers use None to signal "not a standards
    candidate" — counted into `nonexternal_returned` diagnostic.
    """
    if not ns_path.startswith(ext_ns):
        return None
    rel = ns_path[len(ext_ns):]  # safe slice: ext_ns has trailing slash
    f = auto_mem_dir / rel
    try:
        content = f.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    return sp._parse_kind(content)


def _binom_ci_halfwidth(p_hat: float, n: int) -> float:
    """95% normal-approx binomial CI half-width. Returns nan for n<=0."""
    if n <= 0:
        return float("nan")
    return 1.96 * math.sqrt(p_hat * (1 - p_hat) / n)


def _run_condition(
    mq,
    sp,
    probes: list[dict[str, Any]],
    exclude_kinds: set[str],
    auto_mem_dir: Path,
    condition_label: str,
) -> dict[str, Any]:
    """Run all probes through `recall()` once, aggregate diagnostics.

    `atom_fallback` probes are excluded from recall@3 / FPR denominators — they
    bypass tree-retrieval ranking entirely so they tell us nothing about how
    `exclude_kinds` affects ranking. They are surfaced in their own counter.
    """
    ext_ns = mq.mt.EXTERNAL_NAMESPACE  # "auto-memory/"

    tree_hits = 0
    tree_n = 0
    std_in_top3_count = 0
    std_at_rank1_count = 0
    fallback_count = 0
    fell_back_no_atom_count = 0
    nonexternal_returned = 0
    per_probe: list[dict[str, Any]] = []

    for p in probes:
        result = mq.recall(
            p["query"],
            k=3,
            exclude_kinds=exclude_kinds,
            source=f"bench-m4-prereq-{condition_label}",
        )
        # `recall()` has three return shapes (memory_query.py:164-189):
        # (1) atom_fallback=True — branch routed through atom DB; skip.
        # (2) fell_back=True without atom_fallback — abstain, no atoms;
        #     also bypasses tree ranking — skip too, else silently counts
        #     as a miss and conflates abstain with rank failure.
        # (3) normal tree result — proceed to scoring.
        if result.get("atom_fallback"):
            fallback_count += 1
            per_probe.append({
                "query": p["query"],
                "expected": p["expected_path"],
                "atom_fallback": True,
            })
            continue
        if result.get("fell_back"):
            fell_back_no_atom_count += 1
            per_probe.append({
                "query": p["query"],
                "expected": p["expected_path"],
                "fell_back_no_atom": True,
            })
            continue

        paths = result.get("paths", [])[:3]
        tree_n += 1
        hit = p["expected_path"] in paths
        if hit:
            tree_hits += 1

        kinds = [_kind_of(auto_mem_dir, x, ext_ns, sp) for x in paths]
        if any(k == "standard" for k in kinds):
            std_in_top3_count += 1
        if kinds and kinds[0] == "standard":
            std_at_rank1_count += 1
        nonexternal_returned += sum(
            1 for x in paths if not x.startswith(ext_ns)
        )

        per_probe.append({
            "query": p["query"],
            "expected": p["expected_path"],
            "returned": paths,
            "kinds": kinds,
            "hit": hit,
            "std_in_top3": [
                paths[i] for i, k in enumerate(kinds) if k == "standard"
            ],
        })

    recall_at_3 = tree_hits / tree_n if tree_n else float("nan")
    fpr = std_in_top3_count / tree_n if tree_n else float("nan")
    ci = _binom_ci_halfwidth(recall_at_3, tree_n)
    return {
        "condition": condition_label,
        "exclude_kinds": sorted(exclude_kinds),
        "n_probes": len(probes),
        "tree_n": tree_n,
        "atom_fallback_count": fallback_count,
        "fell_back_no_atom_count": fell_back_no_atom_count,
        "tree_hits": tree_hits,
        # Consistent None-on-NaN policy across all aggregate metrics — emits
        # valid RFC 8259 JSON (no literal `NaN`) and disambiguates "no data"
        # from "zero score" for downstream consumers.
        "recall_at_3": round(recall_at_3, 4) if not math.isnan(recall_at_3) else None,
        "fpr": round(fpr, 4) if not math.isnan(fpr) else None,
        "std_at_rank1_count": std_at_rank1_count,
        "nonexternal_returned": nonexternal_returned,
        "recall_ci_halfwidth": round(ci, 4) if not math.isnan(ci) else None,
        "per_probe": per_probe,
    }


def _verdict(recall_at_3: float | None, fpr: float | None) -> str:
    if recall_at_3 is None or fpr is None:
        return _FAIL
    return _PASS if recall_at_3 >= _RECALL_GATE and fpr <= _FPR_GATE else _FAIL


def _git_sha() -> str:
    """Best-effort current git SHA. Returns 'unknown' on any failure.

    Subprocess directly — bench.store import is deferred until --save.
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
        prog="methodology_exclude_kinds_sweep",
        description=(
            "M4-prereq: measure recall@3 + FPR with and without "
            "exclude_kinds={'standard'} on methodology probes."
        ),
    )
    parser.add_argument(
        "--probes",
        type=Path,
        default=_DEFAULT_PROBES,
        help=f"Probe fixture path (default: {_DEFAULT_PROBES})",
    )
    parser.add_argument(
        "--auto-mem-dir",
        default=None,
        help="Override auto-memory dir (default: sp._default_auto_mem_dir())",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Output dir (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Persist results to bench DB (uses bench.store.save_run).",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Optional human label persisted with the run.",
    )
    args = parser.parse_args(argv)

    probes_path: Path = args.probes
    if not probes_path.exists():
        print(f"[M4-PREREQ] probes file not found: {probes_path}", file=sys.stderr)
        return 1

    sp = _load_sp()
    mq = _load_mq()
    auto_mem_dir = _resolve_auto_mem_dir(sp, args.auto_mem_dir)
    if not auto_mem_dir.exists():
        print(
            f"[M4-PREREQ] auto-mem dir not found: {auto_mem_dir}",
            file=sys.stderr,
        )
        return 1

    probes = _load_probes(probes_path)
    methodology_probes = [p for p in probes if p.get("tag") == "methodology"]

    # Condition B uses an empty set, NOT None — `recall()` substitutes the
    # production default when exclude_kinds is None, defeating the experiment.
    cond_a = _run_condition(
        mq, sp, methodology_probes, {"standard"}, auto_mem_dir, "A_baseline",
    )
    cond_b = _run_condition(
        mq, sp, methodology_probes, set(), auto_mem_dir, "B_m4_candidate",
    )

    # Pretty stdout table. `fb_atom` = recall fell back AND atom_fallback
    # served context (skipped from rank metrics). `fb_none` = recall fell
    # back AND atom fallback found nothing either (also skipped). Both
    # buckets exclude from tree_n.
    print()
    print(
        f"{'condition':<18} {'recall@3':>9} {'fpr':>7} {'rank1_std':>10} "
        f"{'tree_n':>7} {'fb_atom':>8} {'fb_none':>8} {'verdict':>8}"
    )
    print("-" * 83)
    for row in (cond_a, cond_b):
        r3 = "  n/a  " if row["recall_at_3"] is None else f"{row['recall_at_3']:.4f}"
        fp = "  n/a  " if row["fpr"] is None else f"{row['fpr']:.4f}"
        verdict = _verdict(row["recall_at_3"], row["fpr"])
        print(
            f"{row['condition']:<18} {r3:>9} {fp:>7} "
            f"{row['std_at_rank1_count']:>10d} {row['tree_n']:>7d} "
            f"{row['atom_fallback_count']:>8d} "
            f"{row['fell_back_no_atom_count']:>8d} {verdict:>8}"
        )

    # JSON output (full per-probe traces).
    args.output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.date.today().isoformat()
    out_path = args.output_dir / f"methodology_exclude_kinds_{date_str}.json"
    payload = {
        "run_date": date_str,
        "git_sha": _git_sha(),
        "probes_path": str(probes_path),
        "auto_mem_dir": str(auto_mem_dir),
        "n_total_probes": len(probes),
        "n_methodology_probes": len(methodology_probes),
        "gate": {"recall_min": _RECALL_GATE, "fpr_max": _FPR_GATE},
        "conditions": [cond_a, cond_b],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nresults written to {out_path}", file=sys.stderr)

    # Decision-gate verdict — focuses on condition B (the M4 candidate).
    b_recall = cond_b["recall_at_3"]
    b_fpr = cond_b["fpr"]
    b_verdict = _verdict(b_recall, b_fpr)
    b_ci = cond_b["recall_ci_halfwidth"]
    print(
        f"[M4-PREREQ] B (m4_candidate): recall@3={b_recall} fpr={b_fpr} "
        f"tree_n={cond_b['tree_n']} fb_atom={cond_b['atom_fallback_count']} "
        f"fb_none={cond_b['fell_back_no_atom_count']}",
        file=sys.stderr,
    )
    ci_str = f"±{b_ci:.4f}" if b_ci is not None else "n/a"
    print(
        f"[M4-PREREQ] 95% CI half-width for recall@3: {ci_str} "
        f"(n={cond_b['tree_n']}, gate={_RECALL_GATE})",
        file=sys.stderr,
    )
    if b_verdict == _PASS:
        print(f"[M4-PREREQ] {b_verdict} (gate: recall>={_RECALL_GATE} AND fpr<={_FPR_GATE})", file=sys.stderr)
    else:
        print(
            f"[M4-PREREQ] {b_verdict} "
            f"(gate: recall>={_RECALL_GATE} AND fpr<={_FPR_GATE}; "
            f"got recall={b_recall}, fpr={b_fpr})",
            file=sys.stderr,
        )

    # Sanity: condition A recall@3 below the absolute floor (module-level
    # `_RECALL_SANITY_FLOOR`) suggests the path-equality test is broken
    # (e.g. probe path format drift) OR the retrieval pipeline has degraded
    # — either way, the M4 gate verdict on condition B should not be
    # trusted without investigation. We print a WARN but never abort; the
    # human decides whether to act.
    a_recall = cond_a["recall_at_3"]
    if a_recall is not None and a_recall < _RECALL_SANITY_FLOOR:
        print(
            f"[M4-PREREQ] WARN: condition A recall_at_3={a_recall:.4f} "
            f"below sanity floor {_RECALL_SANITY_FLOOR}. Either probe-path "
            f"equality is broken OR retrieval signal is degraded — verify "
            f"before trusting condition B's verdict.",
            file=sys.stderr,
        )

    if args.save:
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        from bench.store import save_run  # noqa: E402
        from bench.types import CaseResult, RunResult  # noqa: E402

        # Refuse to persist a run that produced no measurable signal — a
        # None recall (tree_n=0) coerced to 0.0 would be indistinguishable
        # from a genuine zero in downstream regression queries.
        for row in (cond_a, cond_b):
            if row["recall_at_3"] is None:
                print(
                    f"[M4-PREREQ] refusing to --save: condition "
                    f"{row['condition']} has no tree probes "
                    f"(tree_n=0). Inspect inputs and re-run.",
                    file=sys.stderr,
                )
                return 1

        # All scores are now non-None floats; safe to persist.
        cases = [
            CaseResult(
                case_id=row["condition"],
                score=row["recall_at_3"],
                meta={
                    "fpr": row["fpr"],
                    "tree_n": row["tree_n"],
                    "atom_fallback_count": row["atom_fallback_count"],
                    "fell_back_no_atom_count": row["fell_back_no_atom_count"],
                    "std_at_rank1_count": row["std_at_rank1_count"],
                    "nonexternal_returned": row["nonexternal_returned"],
                    "exclude_kinds": row["exclude_kinds"],
                },
            )
            for row in (cond_a, cond_b)
        ]
        run = RunResult(
            suite="methodology_exclude_kinds_sweep",
            score=cond_b["recall_at_3"],
            cases=cases,
            meta={
                "probes_path": str(probes_path),
                "auto_mem_dir": str(auto_mem_dir),
                "gate": {"recall_min": _RECALL_GATE, "fpr_max": _FPR_GATE},
                "verdict": b_verdict,
            },
        )
        run_id = save_run(run, label=args.label)
        print(f"persisted run_id={run_id} to bench DB", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
