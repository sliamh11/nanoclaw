import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path

from ..registry import register
from ..types import CaseResult, RunResult

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
_HARNESS_PATH = _SCRIPTS_DIR / "token_bench" / "harness.py"

# Default budget: current measured values rounded up to nearest 100, +10% headroom.
# Measured 2026-04-17: host_cc_session=631, container_whatsapp_main_turn1=1203,
# container_telegram_main_turn1=1018  →  totals 2852 across 3 scenarios.
TOKEN_BUDGETS_PER_SCENARIO: dict[str, int] = {
    "host_cc_session": 700,                  # 631 → ceil100=700 (+10%)
    "container_whatsapp_main_turn1": 1400,   # 1203 → ceil100=1300 +10% ≈ 1400
    "container_telegram_main_turn1": 1200,   # 1018 → ceil100=1100 +10% ≈ 1200
}
# Overall suite budget (sum of per-scenario defaults + 10% headroom, round to 100)
TOKEN_BUDGET: int = int(os.environ.get("DEUS_BENCH_TOKEN_BUDGET", "3500"))


def _score(tokens_in: int, budget: int) -> float:
    if tokens_in <= budget:
        return 1.0
    return min(1.0, budget / max(tokens_in, 1))


def _load_harness():
    mod_name = "token_bench_harness"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@register("token")
def run_token(argv: list[str]) -> RunResult:
    p = argparse.ArgumentParser(prog="token")
    p.add_argument("--label", default="bench-cli")
    args = p.parse_args(argv)

    h = _load_harness()
    t_start = time.monotonic()

    files: dict = {}
    for key, rel in h.STATIC_CONTEXT_TARGETS:
        files[key] = h.file_info(h.REPO / rel)

    scenarios: dict = {}
    for name, keys in h.SCENARIOS.items():
        total_chars = sum(files[k].get("chars", 0) for k in keys if files[k].get("exists"))
        scenarios[name] = {
            "components": keys,
            "total_chars": total_chars,
            "est_tokens": h.est_tokens(total_chars),
        }

    elapsed_ms = int((time.monotonic() - t_start) * 1000)

    cases: list[CaseResult] = []
    total_tokens_in = 0
    for name, s in scenarios.items():
        est = s["est_tokens"]
        total_tokens_in += est
        budget = TOKEN_BUDGETS_PER_SCENARIO.get(name, TOKEN_BUDGET)
        case_score = _score(est, budget)
        over_by = max(0, est - budget)
        cases.append(CaseResult(
            case_id=name,
            score=case_score,
            tokens_in=est,
            meta={
                "chars": s["total_chars"],
                "components": s["components"],
                "budget": budget,
                "over_by": over_by,
            },
        ))

    suite_score = _score(total_tokens_in, TOKEN_BUDGET)
    suite_over_by = max(0, total_tokens_in - TOKEN_BUDGET)

    return RunResult(
        suite="token",
        score=suite_score,
        cases=cases,
        tokens_in=total_tokens_in,
        latency_ms=elapsed_ms,
        meta={
            "label": args.label,
            "chars_per_token": h.CHARS_PER_TOKEN,
            "budget": TOKEN_BUDGET,
            "over_by": suite_over_by,
        },
    )
