import argparse
import importlib.util
import sys
import time
from pathlib import Path

from ..registry import register
from ..types import CaseResult, RunResult

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
_HARNESS_PATH = _SCRIPTS_DIR / "token_bench" / "harness.py"

# Per-turn overhead components (estimated tokens added each turn after turn 1).
#
# tool_schema_bloat: MCP tool schemas are re-injected every turn. With ~10 active
# tools × ~150 chars / 3.7 ≈ 400 tokens per redeclaration round.
TOOL_SCHEMA_BLOAT: int = 400
#
# memory_tree_hint: Per-turn retrieval result injected from memory_tree (top-K
# nodes). ~5 nodes × ~150 chars each / 3.7 ≈ 200 tokens.
MEMORY_TREE_HINT: int = 200
#
# previous_response_copy: Skeleton of prior assistant turn copied into context
# for continuity. ~550 chars / 3.7 ≈ 150 tokens.
PREVIOUS_RESPONSE_COPY: int = 150

# Headroom multiplier applied to per-turn budget calculations.
_BUDGET_HEADROOM: float = 1.1


def _load_harness():
    mod_name = "token_bench_harness"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _score(tokens_in: int, budget: int) -> float:
    if tokens_in <= budget:
        return 1.0
    return min(1.0, budget / max(tokens_in, 1))


def _per_turn_increment() -> int:
    return TOOL_SCHEMA_BLOAT + MEMORY_TREE_HINT + PREVIOUS_RESPONSE_COPY


def _turn_tokens(baseline: int, turn: int) -> int:
    """Estimated cumulative token count at turn N (1-indexed)."""
    return baseline + (turn - 1) * _per_turn_increment()


def _turn_budget(baseline_budget: int, turn: int) -> int:
    increment = _per_turn_increment()
    return int((baseline_budget + (turn - 1) * increment) * _BUDGET_HEADROOM)


@register("token_multiturn")
def run_token_multiturn(argv: list[str]) -> RunResult:
    p = argparse.ArgumentParser(prog="token_multiturn")
    p.add_argument("--turns", type=int, default=5,
                   help="number of simulated turns (default: 5)")
    args = p.parse_args(argv)

    n_turns: int = args.turns
    if n_turns < 1:
        raise ValueError(f"--turns must be >= 1, got {n_turns}")

    h = _load_harness()
    t_start = time.monotonic()

    # Turn-1 baseline: host_cc_session scenario (CLAUDE.md only).
    files: dict = {}
    for key, rel in h.STATIC_CONTEXT_TARGETS:
        files[key] = h.file_info(h.REPO / rel)

    baseline_scenario = "host_cc_session"
    baseline_keys = h.SCENARIOS[baseline_scenario]
    baseline_chars = sum(
        files[k].get("chars", 0)
        for k in baseline_keys
        if files[k].get("exists")
    )
    baseline_tokens = h.est_tokens(baseline_chars)

    # Budget for the baseline turn — use the same per-scenario budget from token.py
    # if accessible, otherwise derive a proportional baseline.
    try:
        from . import token as _token_suite
        baseline_budget = _token_suite.TOKEN_BUDGETS_PER_SCENARIO.get(
            baseline_scenario,
            _token_suite.TOKEN_BUDGET,
        )
    except ImportError:
        baseline_budget = int(baseline_tokens * _BUDGET_HEADROOM)

    per_turn_components = {
        "tool_schema_bloat": TOOL_SCHEMA_BLOAT,
        "memory_tree_hint": MEMORY_TREE_HINT,
        "previous_response_copy": PREVIOUS_RESPONSE_COPY,
    }
    increment = _per_turn_increment()

    cumulative_tokens: list[int] = []
    cases: list[CaseResult] = []

    for turn in range(1, n_turns + 1):
        est = _turn_tokens(baseline_tokens, turn)
        cumulative_tokens.append(est)

        budget = _turn_budget(baseline_budget, turn)
        turn_score = _score(est, budget)

        cases.append(CaseResult(
            case_id=f"turn_{turn}",
            score=turn_score,
            tokens_in=est,
            meta={
                "turn": turn,
                "baseline_tokens": baseline_tokens,
                "increment_this_turn": (turn - 1) * increment,
                "budget": budget,
                "over_by": max(0, est - budget),
            },
        ))

    elapsed_ms = int((time.monotonic() - t_start) * 1000)

    suite_score = min(c.score for c in cases)
    last_turn_tokens = cumulative_tokens[-1]

    return RunResult(
        suite="token_multiturn",
        score=suite_score,
        cases=cases,
        tokens_in=last_turn_tokens,
        latency_ms=elapsed_ms,
        meta={
            "turns": n_turns,
            "per_turn_components": per_turn_components,
            "cumulative_tokens": cumulative_tokens,
            "chars_per_token": h.CHARS_PER_TOKEN,
            "baseline_scenario": baseline_scenario,
            "baseline_tokens": baseline_tokens,
        },
    )
