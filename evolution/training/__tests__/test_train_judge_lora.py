"""Unit tests for evolution/training/train_judge_lora.py.

Coverage: pure-function correctness (iters math, scale-from-alpha, YAML emission,
command assembly, run-id format) + dry-run subprocess guard + drift detection.
Does NOT exercise the real mlx_lm.lora subprocess — that's a manual run.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from evolution.training import train_judge_lora as M


@pytest.fixture
def base_spec() -> M.RunSpec:
    return M.RunSpec(
        model=M.DEFAULT_MODEL,
        lora_rank=16,
        lora_alpha=32,
        lora_dropout=0.05,
        num_layers=8,
        batch_size=2,
        grad_accum=4,
        epochs=5,
        learning_rate=5e-5,
        mask_prompt=True,
        max_seq_length=4096,
        optimizer="adamw",
        seed=20260517,
        save_every=50,
        steps_per_report=10,
        steps_per_eval=50,
        val_batches=41,
        grad_checkpoint=False,
        yes=False,
        dry_run=False,
        pre_flight_only=False,
    )


def test_iters_from_epochs_canonical():
    # ceil(5 * 658 / 8) = ceil(411.25) = 412
    assert M.iters_from_epochs(epochs=5, n_train=658, batch=2, accum=4) == 412


def test_iters_from_epochs_rounding():
    # ceil(3 * 658 / 8) = ceil(246.75) = 247
    assert M.iters_from_epochs(epochs=3, n_train=658, batch=2, accum=4) == 247


def test_iters_from_epochs_exact_boundary():
    # Exact division: 4 * 800 / 8 = 400.0 → 400 (no ceiling)
    assert M.iters_from_epochs(epochs=4, n_train=800, batch=2, accum=4) == 400


def test_scale_from_alpha_rank():
    assert M.scale_from_alpha_rank(alpha=32, rank=16) == 2.0
    assert M.scale_from_alpha_rank(alpha=8, rank=8) == 1.0
    assert M.scale_from_alpha_rank(alpha=64, rank=16) == 4.0


def test_yaml_emission_has_required_keys(base_spec):
    yaml_text = M.emit_yaml_config(base_spec, iters=412, peak_lr=5e-5)
    # Sanity-check that all the keys the implementer needs are present in the textual YAML.
    # Avoid pulling in a YAML parser dependency just for tests.
    assert "lora_parameters:" in yaml_text
    assert "rank: 16" in yaml_text
    assert "scale: 2.0" in yaml_text
    assert "dropout: 0.05" in yaml_text
    assert "lr_schedule:" in yaml_text
    assert "name: cosine_decay" in yaml_text
    assert "warmup: 10" in yaml_text
    assert "arguments:" in yaml_text


def test_yaml_warmup_arguments_shape(base_spec):
    # arguments: [peak_lr, iters - warmup, peak_lr * 0.1]
    yaml_text = M.emit_yaml_config(base_spec, iters=412, peak_lr=5e-5)
    # decay_steps = iters - warmup (10) = 402; end = peak_lr * 0.1 = 5e-6
    assert "arguments: [5e-05, 402, 5.000000000000001e-06]" in yaml_text or \
           "arguments: [5e-05, 402, 5e-06]" in yaml_text or \
           re.search(r"arguments:\s*\[5e-?0?5,\s*402,\s*5(\.0+)?e-?0?6", yaml_text)


def test_command_assembly_includes_mask_prompt(base_spec, tmp_path):
    cmd = M.assemble_command(
        base_spec, data_dir=tmp_path / "data",
        adapter_dir=tmp_path / "adapter", config_path=tmp_path / "cfg.yaml", iters=412,
    )
    assert "--mask-prompt" in cmd
    assert "--train" in cmd
    assert "--iters" in cmd
    iters_idx = cmd.index("--iters")
    assert cmd[iters_idx + 1] == "412"


def test_command_assembly_omits_mask_prompt_when_false(base_spec, tmp_path):
    spec = replace(base_spec, mask_prompt=False)
    cmd = M.assemble_command(
        spec, data_dir=tmp_path / "data",
        adapter_dir=tmp_path / "adapter", config_path=tmp_path / "cfg.yaml", iters=412,
    )
    assert "--mask-prompt" not in cmd


def test_command_assembly_grad_checkpoint(base_spec, tmp_path):
    spec_off = base_spec
    cmd_off = M.assemble_command(spec_off, tmp_path, tmp_path, tmp_path / "c", 1)
    assert "--grad-checkpoint" not in cmd_off

    spec_on = replace(base_spec, grad_checkpoint=True)
    cmd_on = M.assemble_command(spec_on, tmp_path, tmp_path, tmp_path / "c", 1)
    assert "--grad-checkpoint" in cmd_on


def test_run_id_format():
    rid = M.make_run_id()
    # YYYYMMDDTHHMMSSZ-<7hex>[-dirty]
    assert re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{4,7}(-dirty)?$", rid) or \
           re.match(r"^\d{8}T\d{6}Z-nogit$", rid), f"unexpected run_id format: {rid}"


def test_dry_run_does_not_invoke_subprocess_popen(monkeypatch):
    """The dry-run path must not invoke Popen (training) or run (preflight checks).
    Patch BOTH to raise — if either is called, the test fails."""
    def boom_popen(*args, **kwargs):
        raise AssertionError(f"subprocess.Popen called in dry-run: args={args}")
    def boom_run(*args, **kwargs):
        raise AssertionError(f"subprocess.run called in dry-run: args={args}")

    monkeypatch.setattr(subprocess, "Popen", boom_popen)
    monkeypatch.setattr(subprocess, "run", boom_run)

    # Capture stdout so the assembled YAML + command don't pollute the test output.
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    rc = M.main(["--dry-run"])
    assert rc == 0
    output = buf.getvalue()
    assert "lora_parameters:" in output
    assert "mlx_lm" in output  # command line includes mlx_lm module name


def test_preflight_detects_dataset_drift(tmp_path, monkeypatch, base_spec):
    """Build a fake dataset dir where the file's actual hash != manifest hash;
    preflight() must SystemExit on the drift."""
    data_subdir = tmp_path / "data"
    data_subdir.mkdir()
    # Write split files with content X
    for split in ("train", "valid", "test"):
        (data_subdir / f"{split}.jsonl").write_text(f"{split} content\n")
    # Manifest says content_sha256 is "deadbeef" — won't match the real hashes
    manifest = {"content_sha256": {"train": "deadbeef", "valid": "deadbeef", "test": "deadbeef"}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    # Stub VENV existence so we reach the drift check
    monkeypatch.setattr(M, "VENV", tmp_path / "fake_venv_python")
    (tmp_path / "fake_venv_python").write_text("")

    with pytest.raises(SystemExit, match="content drift"):
        M.preflight(base_spec, tmp_path)


def test_preflight_aborts_on_missing_manifest(tmp_path, monkeypatch, base_spec):
    monkeypatch.setattr(M, "VENV", tmp_path / "fake_venv_python")
    (tmp_path / "fake_venv_python").write_text("")
    with pytest.raises(SystemExit, match="dataset manifest missing"):
        M.preflight(base_spec, tmp_path)


def test_preflight_aborts_on_missing_venv(tmp_path, monkeypatch, base_spec):
    monkeypatch.setattr(M, "VENV", tmp_path / "definitely-does-not-exist")
    with pytest.raises(SystemExit, match="venv binary missing"):
        M.preflight(base_spec, tmp_path)
