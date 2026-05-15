"""Unit tests for scripts/bench/rule_following_judge.py.

Covers deterministic logic only — no live LLM calls. The live-LLM path is
NOT CI-gated; the pilot (3-probe smoke) is the human-run gate before opening
the PR.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

_BENCH_PATH = Path(__file__).resolve().parent.parent / "bench" / "rule_following_judge.py"


def _load_bench():
    """Load the bench module without polluting sys.modules across tests."""
    spec = importlib.util.spec_from_file_location("rule_following_judge", _BENCH_PATH)
    assert spec and spec.loader, f"cannot load spec from {_BENCH_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rule_following_judge"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bench():
    return _load_bench()


@pytest.fixture
def sp(bench):
    return bench._load_sp()


@pytest.fixture
def tmp_auto_mem_dir():
    """Build a synthetic auto_mem_dir with 3 standard atoms at different priorities."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # high priority
        (d / "feedback_alpha.md").write_text(
            "---\n"
            "kind: standard\n"
            "name: alpha_rule\n"
            "description: First high-priority rule.\n"
            "priority: high\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )
        # med priority (default)
        (d / "feedback_beta.md").write_text(
            "---\n"
            "kind: standard\n"
            "name: beta_rule\n"
            "description: Default-priority rule.\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )
        # low priority
        (d / "feedback_gamma.md").write_text(
            "---\n"
            "kind: standard\n"
            "name: gamma_rule\n"
            "description: Low-priority rule.\n"
            "priority: low\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )
        # non-standard (should be excluded)
        (d / "knowledge_atom.md").write_text(
            "---\n"
            "kind: knowledge\n"
            "name: knowledge_x\n"
            "description: A knowledge atom that should NOT be packed.\n"
            "---\n"
            "body\n",
            encoding="utf-8",
        )
        yield d


# ---------------------------------------------------------------------------
# Probe fixture loader
# ---------------------------------------------------------------------------


def test_load_probes_parses_jsonl(bench, tmp_path):
    """Probe loader correctly round-trips JSONL with id/tier/task/target_rules."""
    fixture = tmp_path / "probes.jsonl"
    fixture.write_text(
        '{"id": "t1", "tier": "easy", "task": "T1", "target_rules": ["r1"]}\n'
        '{"id": "t2", "tier": "hard", "task": "T2", "target_rules": ["r1", "r2"]}\n'
        "\n"  # blank line — should be skipped
        '{"id": "t3", "tier": "medium", "task": "T3", "target_rules": []}\n',
        encoding="utf-8",
    )
    probes = bench._load_probes(fixture)
    assert len(probes) == 3
    assert probes[0]["id"] == "t1"
    assert probes[1]["target_rules"] == ["r1", "r2"]
    assert probes[2]["tier"] == "medium"


def test_load_probes_missing_file_raises(bench, tmp_path):
    """Missing probe file raises FileNotFoundError, doesn't silently return []."""
    with pytest.raises(FileNotFoundError):
        bench._load_probes(tmp_path / "does_not_exist.jsonl")


# ---------------------------------------------------------------------------
# Arm text builder — production fidelity tests
# ---------------------------------------------------------------------------


def test_build_standards_text_zero_arm(bench, sp, tmp_auto_mem_dir):
    """`format: None` returns empty string regardless of atom availability."""
    arm = {"name": "zero", "format": None, "budget": 0}
    assert bench._build_standards_text(sp, arm, tmp_auto_mem_dir) == ""


def test_build_standards_text_respects_budget(bench, sp, tmp_auto_mem_dir):
    """Tiny budget drops atoms; only the first atom that fits is packed.

    Each oneliner is `- alpha_rule: First high-priority rule.` (~6 words *1.3
    ≈ 7 tokens). Budget=5 forces zero atoms to fit, returning empty string.
    """
    arm = {"name": "tight", "format": "name_desc", "budget": 5}
    out = bench._build_standards_text(sp, arm, tmp_auto_mem_dir)
    # 5-token budget is smaller than even one atom's name_desc cost → empty.
    assert out == ""


def test_build_standards_text_format_toggle(bench, sp, tmp_auto_mem_dir):
    """name_only output is shorter than name_desc for the same atom set."""
    arm_full = {"name": "full", "format": "name_desc", "budget": 10000}
    arm_only = {"name": "only", "format": "name_only", "budget": 10000}
    txt_full = bench._build_standards_text(sp, arm_full, tmp_auto_mem_dir)
    txt_only = bench._build_standards_text(sp, arm_only, tmp_auto_mem_dir)
    assert txt_full and txt_only
    assert len(txt_only) < len(txt_full)
    # name_only should NOT include descriptions.
    assert "First high-priority rule" not in txt_only
    assert "First high-priority rule" in txt_full


def test_build_standards_text_priority_sort(bench, sp, tmp_auto_mem_dir):
    """high-priority atom appears before med-priority and low-priority.

    Regression guard for PR #416's priority sort (the M1a sweep template
    predates this and is missing the sort — see plan-reviewer Blocking #1).
    """
    arm = {"name": "all", "format": "name_only", "budget": 10000}
    txt = bench._build_standards_text(sp, arm, tmp_auto_mem_dir)
    idx_alpha = txt.find("alpha_rule")  # high
    idx_beta = txt.find("beta_rule")    # med (default)
    idx_gamma = txt.find("gamma_rule")  # low
    assert idx_alpha != -1 and idx_beta != -1 and idx_gamma != -1
    # high (priority_rank=0) < med (1) < low (2)
    assert idx_alpha < idx_beta < idx_gamma


def test_build_standards_text_filters_non_standard(bench, sp, tmp_auto_mem_dir):
    """kind=knowledge atoms are NOT packed; only kind=standard is included."""
    arm = {"name": "all", "format": "name_only", "budget": 10000}
    txt = bench._build_standards_text(sp, arm, tmp_auto_mem_dir)
    assert "alpha_rule" in txt
    assert "beta_rule" in txt
    assert "gamma_rule" in txt
    assert "knowledge_x" not in txt


def test_build_standards_text_missing_dir_raises(bench, sp, tmp_path):
    """Nonexistent auto_mem_dir raises FileNotFoundError for non-zero arms."""
    arm = {"name": "full", "format": "name_desc", "budget": 1200}
    with pytest.raises(FileNotFoundError):
        bench._build_standards_text(sp, arm, tmp_path / "nope")


# ---------------------------------------------------------------------------
# Judge response parser — robustness against malformed Gemini output
# ---------------------------------------------------------------------------


def test_judge_response_parser_handles_valid_json(bench):
    """Valid JSON → {compliance, citation, evidence}."""
    raw = '{"compliance": 1, "citation": 0, "evidence": "The model hedged."}'
    out = bench._judge_response_parse(raw)
    assert out == {"compliance": 1, "citation": 0, "evidence": "The model hedged."}


def test_judge_response_parser_handles_markdown_fenced_json(bench):
    """Gemini sometimes wraps JSON in ```json ... ``` fences — parser unwraps."""
    raw = '```json\n{"compliance": 2, "citation": 2, "evidence": "full match"}\n```'
    out = bench._judge_response_parse(raw)
    assert out["compliance"] == 2
    assert out["citation"] == 2
    assert out["evidence"] == "full match"


def test_judge_response_parser_handles_malformed_json(bench):
    """Malformed JSON → fallback dict with zeros + error string (no raise)."""
    raw = "this is not json at all"
    out = bench._judge_response_parse(raw)
    assert out["compliance"] == 0
    assert out["citation"] == 0
    assert "parse_error" in out["evidence"]


def test_judge_response_parser_clamps_out_of_range_values(bench):
    """compliance=5 (invalid) → clamped to 0, not crash."""
    raw = '{"compliance": 5, "citation": -1, "evidence": "oob"}'
    out = bench._judge_response_parse(raw)
    assert out["compliance"] == 0
    assert out["citation"] == 0


def test_judge_response_parser_handles_empty_input(bench):
    """Empty / whitespace input → zero scores with marker."""
    assert bench._judge_response_parse("")["evidence"] == "<empty_judge_output>"
    assert bench._judge_response_parse("   \n  ")["evidence"] == "<empty_judge_output>"


# ---------------------------------------------------------------------------
# Aggregation — paired bootstrap CI sanity checks
# ---------------------------------------------------------------------------


def test_aggregate_empty_input_returns_zero(bench):
    """Empty per_probe list → safe zero-result dict, no KeyError."""
    out = bench._aggregate([], ["zero", "full@1200"])
    assert out["n"] == 0
    assert out["per_arm"] == {}
    assert out["hypotheses"] == {}


def test_aggregate_csr_isr_arithmetic(bench):
    """CSR/ISR are mean(score/2) per arm. Synthetic input → known output."""
    # 2 probes, 2 arms. Compliance: zero=[0,0], full=[2,2]. CSR(zero)=0, CSR(full)=1.
    per_probe = [
        {"scores": {
            "zero":       {"compliance": 0, "citation": 0, "evidence": ""},
            "full@1200":  {"compliance": 2, "citation": 1, "evidence": ""},
        }},
        {"scores": {
            "zero":       {"compliance": 0, "citation": 0, "evidence": ""},
            "full@1200":  {"compliance": 2, "citation": 2, "evidence": ""},
        }},
    ]
    out = bench._aggregate(per_probe, ["zero", "full@1200"])
    assert out["per_arm"]["zero"]["csr"] == 0.0
    assert out["per_arm"]["zero"]["isr"] == 0.0
    assert out["per_arm"]["full@1200"]["csr"] == 1.0
    # ISR for full = (0.5 + 1.0) / 2 = 0.75
    assert out["per_arm"]["full@1200"]["isr"] == 0.75


def test_aggregate_paired_bootstrap_synthetic(bench):
    """Synthetic identical scores per arm → CI bounds tight around 0."""
    per_probe = [
        {"scores": {
            "zero":       {"compliance": 1, "citation": 1, "evidence": ""},
            "full@1200":  {"compliance": 1, "citation": 1, "evidence": ""},
        }}
        for _ in range(30)
    ]
    out = bench._aggregate(per_probe, ["zero", "full@1200"])
    pw = out["pairwise"]["full@1200__vs__zero"]
    # Identical scores → mean delta = 0, CI bounds = [0, 0].
    assert pw["delta_csr"] == 0.0
    assert pw["ci95"] == [0.0, 0.0]
    # H1 requires CI strictly > 0 → FAIL with identical scores.
    assert out["hypotheses"]["H1"] == "FAIL"


def test_aggregate_hypothesis_h1_passes_when_full_dominates(bench):
    """If full@1200 strictly dominates zero on every probe → H1 PASS."""
    per_probe = [
        {"scores": {
            "zero":       {"compliance": 0, "citation": 0, "evidence": ""},
            "full@1200":  {"compliance": 2, "citation": 2, "evidence": ""},
        }}
        for _ in range(30)
    ]
    out = bench._aggregate(per_probe, ["zero", "full@1200"])
    pw = out["pairwise"]["full@1200__vs__zero"]
    assert pw["delta_csr"] == 1.0
    assert pw["ci95"][0] > 0  # whole CI is above zero
    assert out["hypotheses"]["H1"] == "PASS"


def test_aggregate_hypothesis_h2_passes_within_5pp(bench):
    """name_only@1200 within 5pp of full@1200 → H2 PASS."""
    # Identical scores between the two name_* arms → Δ=0, CI=[0,0] ⊂ [-0.05, 0.05].
    per_probe = [
        {"scores": {
            "zero":            {"compliance": 0, "citation": 0, "evidence": ""},
            "full@1200":       {"compliance": 2, "citation": 2, "evidence": ""},
            "name_only@1200":  {"compliance": 2, "citation": 2, "evidence": ""},
            "name_only@800":   {"compliance": 2, "citation": 2, "evidence": ""},
        }}
        for _ in range(30)
    ]
    out = bench._aggregate(
        per_probe, ["zero", "full@1200", "name_only@1200", "name_only@800"]
    )
    assert out["hypotheses"]["H2"] == "PASS"
    assert out["hypotheses"]["H3"] == "PASS"


def test_aggregate_hypothesis_h2_fails_on_large_drop(bench):
    """name_only@1200 dropping CSR by 0.5 vs full@1200 → H2 FAIL."""
    per_probe = [
        {"scores": {
            "zero":            {"compliance": 0, "citation": 0, "evidence": ""},
            "full@1200":       {"compliance": 2, "citation": 2, "evidence": ""},
            "name_only@1200":  {"compliance": 1, "citation": 1, "evidence": ""},  # 0.5 vs 1.0
            "name_only@800":   {"compliance": 0, "citation": 0, "evidence": ""},
        }}
        for _ in range(30)
    ]
    out = bench._aggregate(
        per_probe, ["zero", "full@1200", "name_only@1200", "name_only@800"]
    )
    assert out["hypotheses"]["H2"] == "FAIL"


# ---------------------------------------------------------------------------
# Arm config integrity
# ---------------------------------------------------------------------------


def test_arms_config_has_4_arms(bench):
    """ARMS must define exactly the 4 arms the plan locks in."""
    arm_names = [a["name"] for a in bench.ARMS]
    assert arm_names == ["zero", "full@1200", "name_only@1200", "name_only@800"]


def test_arms_config_zero_has_no_format(bench):
    """zero arm: format=None, budget=0 (semantic: no standards loaded)."""
    zero = next(a for a in bench.ARMS if a["name"] == "zero")
    assert zero["format"] is None
    assert zero["budget"] == 0
