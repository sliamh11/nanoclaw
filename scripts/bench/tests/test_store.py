"""Tests for scripts/bench/store.py"""
import sqlite3
import time
from pathlib import Path

import pytest

from scripts.bench.store import _connect, _db_path, get_cases, list_suites, recent_runs, resolve_run, save_run, trend
from scripts.bench.types import CaseResult, RunResult


def _make_result(suite: str = "demo", score: float = 0.8) -> RunResult:
    return RunResult(
        suite=suite,
        score=score,
        cases=[
            CaseResult(case_id="c1", score=1.0, tokens_in=10, meta={"x": 1}),
            CaseResult(case_id="c2", score=0.5, passed=False),
        ],
        tokens_in=100,
        tokens_out=50,
        latency_ms=123,
        cost_usd=0.001,
        meta={"run": True},
    )


def test_schema_created(isolate_bench_db):
    con = _connect()
    tables = {
        row[0] for row in
        con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    con.close()
    assert "runs" in tables
    assert "cases" in tables
    assert "schema_version" in tables


def test_schema_version(isolate_bench_db):
    con = _connect()
    ver = con.execute("SELECT version FROM schema_version").fetchone()[0]
    con.close()
    assert ver == 2


def test_migration_v1_to_v2(tmp_path, monkeypatch):
    """A version-1 DB (no label column) is migrated to version 2 with the column added."""
    db_path = tmp_path / "v1_test.db"
    monkeypatch.setenv("DEUS_BENCH_DB", str(db_path))

    # Build a v1 schema manually (no label column)
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        CREATE TABLE runs (
          run_id TEXT PRIMARY KEY,
          ts INTEGER NOT NULL,
          suite TEXT NOT NULL,
          model TEXT,
          git_sha TEXT,
          host TEXT,
          n_cases INTEGER NOT NULL DEFAULT 0,
          score REAL,
          tokens_in INTEGER NOT NULL DEFAULT 0,
          tokens_out INTEGER NOT NULL DEFAULT 0,
          latency_ms INTEGER NOT NULL DEFAULT 0,
          cost_usd REAL NOT NULL DEFAULT 0.0,
          meta TEXT
        );
        CREATE TABLE cases (
          run_id TEXT NOT NULL,
          case_id TEXT NOT NULL,
          score REAL,
          tokens_in INTEGER NOT NULL DEFAULT 0,
          tokens_out INTEGER NOT NULL DEFAULT 0,
          latency_ms INTEGER NOT NULL DEFAULT 0,
          passed INTEGER NOT NULL DEFAULT 1,
          meta TEXT,
          PRIMARY KEY (run_id, case_id)
        );
        INSERT INTO schema_version (version) VALUES (1);
    """)
    con.commit()
    con.close()

    # Now call _connect() — should run migration
    con = _connect()
    ver = con.execute("SELECT version FROM schema_version").fetchone()[0]
    cols = {row[1] for row in con.execute("PRAGMA table_info(runs)").fetchall()}
    con.close()

    assert ver == 2
    assert "label" in cols


def test_save_run_with_label(isolate_bench_db):
    run_id = save_run(_make_result(suite="labeled"), label="pre-fix")
    rows = recent_runs(suite="labeled")
    assert len(rows) == 1
    assert rows[0]["label"] == "pre-fix"


def test_save_run_without_label(isolate_bench_db):
    run_id = save_run(_make_result(suite="nolabel"))
    rows = recent_runs(suite="nolabel")
    assert rows[0]["label"] is None


def test_resolve_run_by_run_id(isolate_bench_db):
    run_id = save_run(_make_result(suite="res"))
    row = resolve_run(run_id)
    assert row is not None
    assert row["run_id"] == run_id


def test_resolve_run_by_label(isolate_bench_db):
    run_id = save_run(_make_result(suite="res2"), label="my-label")
    row = resolve_run("my-label")
    assert row is not None
    assert row["run_id"] == run_id


def test_resolve_run_by_git_sha(isolate_bench_db, monkeypatch):
    import scripts.bench.store as store_mod
    monkeypatch.setattr(store_mod, "_git_sha", lambda: "abc1234")
    run_id = save_run(_make_result(suite="res3"))
    row = resolve_run("abc1234")
    assert row is not None
    assert row["run_id"] == run_id


def test_resolve_run_not_found(isolate_bench_db):
    row = resolve_run("definitely-not-there")
    assert row is None


def test_get_cases_returns_all(isolate_bench_db):
    run_id = save_run(_make_result())
    cases = get_cases(run_id)
    assert len(cases) == 2
    assert {c["case_id"] for c in cases} == {"c1", "c2"}


def test_save_run_returns_id(isolate_bench_db):
    run_id = save_run(_make_result())
    assert isinstance(run_id, str)
    assert len(run_id) > 0


def test_save_run_round_trips(isolate_bench_db):
    run_id = save_run(_make_result(suite="mytest", score=0.75))
    rows = recent_runs(suite="mytest")
    assert len(rows) == 1
    r = rows[0]
    assert r["run_id"] == run_id
    assert r["suite"] == "mytest"
    assert abs(r["score"] - 0.75) < 1e-6
    assert r["n_cases"] == 2
    assert r["tokens_in"] == 100


def test_save_run_persists_cases(isolate_bench_db):
    run_id = save_run(_make_result())
    con = _connect()
    cases = con.execute(
        "SELECT * FROM cases WHERE run_id = ?", (run_id,)
    ).fetchall()
    con.close()
    assert len(cases) == 2
    case_ids = {c["case_id"] for c in cases}
    assert "c1" in case_ids
    assert "c2" in case_ids


def test_recent_runs_filters_by_suite(isolate_bench_db):
    save_run(_make_result(suite="alpha"))
    save_run(_make_result(suite="beta"))
    save_run(_make_result(suite="alpha"))

    alpha = recent_runs(suite="alpha")
    assert len(alpha) == 2
    assert all(r["suite"] == "alpha" for r in alpha)


def test_recent_runs_limit(isolate_bench_db):
    for _ in range(5):
        save_run(_make_result(suite="x"))
    rows = recent_runs(suite="x", limit=3)
    assert len(rows) == 3


def test_recent_runs_since_ts(isolate_bench_db):
    save_run(_make_result(suite="ts_test"))
    future_ts = int(time.time()) + 9999
    rows = recent_runs(suite="ts_test", since_ts=future_ts)
    assert len(rows) == 0


def test_list_suites(isolate_bench_db):
    save_run(_make_result(suite="suite_a"))
    save_run(_make_result(suite="suite_b"))
    suites = list_suites()
    assert "suite_a" in suites
    assert "suite_b" in suites


def test_trend_produces_aggregates(isolate_bench_db):
    save_run(_make_result(suite="trnd", score=0.8))
    save_run(_make_result(suite="trnd", score=0.6))
    rows = trend("trnd", days=30)
    assert len(rows) >= 1
    assert "day" in rows[0]
    assert "avg_score" in rows[0]
    assert "run_count" in rows[0]
    assert rows[0]["run_count"] == 2
