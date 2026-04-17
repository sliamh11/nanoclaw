import json
import os
import platform
import secrets
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from .types import RunResult

_DEFAULT_DB = Path("~/.deus/bench/runs.db").expanduser()
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _db_path() -> Path:
    env = os.environ.get("DEUS_BENCH_DB")
    if env:
        return Path(env)
    return _DEFAULT_DB


def _make_run_id() -> str:
    try:
        from ulid import ULID  # type: ignore
        return str(ULID())
    except ImportError:
        return f"{int(time.time() * 1000):013d}{secrets.token_hex(8)}"


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except FileNotFoundError:
        return None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS runs (
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
  meta TEXT,
  label TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_suite_ts ON runs (suite, ts);

CREATE TABLE IF NOT EXISTS cases (
  run_id TEXT NOT NULL,
  case_id TEXT NOT NULL,
  score REAL,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  passed INTEGER NOT NULL DEFAULT 1,
  meta TEXT,
  PRIMARY KEY (run_id, case_id),
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""

_CURRENT_VERSION = 2


def _migrate(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT version FROM schema_version").fetchone()
    ver = row[0] if row else 0
    if ver < 2:
        con.execute("ALTER TABLE runs ADD COLUMN label TEXT")
        con.execute("UPDATE schema_version SET version = 2")
        con.commit()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.exists()
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_SCHEMA)
    if not existing:
        # Fresh DB — set directly to current version
        con.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (_CURRENT_VERSION,),
        )
        con.commit()
    else:
        row = con.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            con.execute(
                "INSERT INTO schema_version (version) VALUES (1)"
            )
            con.commit()
        _migrate(con)
    return con


def save_run(result: RunResult, label: str | None = None) -> str:
    run_id = _make_run_id()
    ts = int(time.time())
    git_sha = _git_sha()
    host = platform.node()

    con = _connect()
    try:
        con.execute(
            """
            INSERT INTO runs
              (run_id, ts, suite, git_sha, host, n_cases, score,
               tokens_in, tokens_out, latency_ms, cost_usd, meta, label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ts,
                result.suite,
                git_sha,
                host,
                len(result.cases),
                result.score,
                result.tokens_in,
                result.tokens_out,
                result.latency_ms,
                result.cost_usd,
                json.dumps(result.meta) if result.meta else None,
                label,
            ),
        )
        for case in result.cases:
            con.execute(
                """
                INSERT INTO cases
                  (run_id, case_id, score, tokens_in, tokens_out,
                   latency_ms, passed, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    case.case_id,
                    case.score,
                    case.tokens_in,
                    case.tokens_out,
                    case.latency_ms,
                    1 if case.passed else 0,
                    json.dumps(case.meta) if case.meta else None,
                ),
            )
        con.commit()
    finally:
        con.close()

    return run_id


def list_suites() -> list[str]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT suite FROM runs ORDER BY suite"
        ).fetchall()
        return [row["suite"] for row in rows]
    finally:
        con.close()


def recent_runs(
    suite: str | None = None,
    limit: int = 20,
    since_ts: int | None = None,
) -> list[dict[str, Any]]:
    con = _connect()
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if suite is not None:
            conditions.append("suite = ?")
            params.append(suite)
        if since_ts is not None:
            conditions.append("ts >= ?")
            params.append(since_ts)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        rows = con.execute(
            f"SELECT * FROM runs {where} ORDER BY ts DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def get_cases(run_id: str) -> list[dict[str, Any]]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT * FROM cases WHERE run_id = ? ORDER BY case_id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def resolve_run(arg: str, suite: str | None = None) -> dict[str, Any] | None:
    """Return the most-recent run matching arg by run_id, label, or git_sha.

    Resolution order: exact run_id → label → git_sha (prefix or full).
    If suite is given, restrict candidates to that suite.
    Returns None when no match is found.
    """
    con = _connect()
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if suite is not None:
            conditions.append("suite = ?")
            params.append(suite)
        where_suite = ("WHERE " + " AND ".join(conditions) + " AND ") if conditions else "WHERE "

        # 1. exact run_id
        row = con.execute(
            f"SELECT * FROM runs {where_suite}run_id = ? ORDER BY ts DESC LIMIT 1",
            [*params, arg],
        ).fetchone()
        if row:
            return dict(row)

        # 2. label
        row = con.execute(
            f"SELECT * FROM runs {where_suite}label = ? ORDER BY ts DESC LIMIT 1",
            [*params, arg],
        ).fetchone()
        if row:
            return dict(row)

        # 3. git_sha (prefix match)
        row = con.execute(
            f"SELECT * FROM runs {where_suite}git_sha LIKE ? ORDER BY ts DESC LIMIT 1",
            [*params, arg + "%"],
        ).fetchone()
        if row:
            return dict(row)

        return None
    finally:
        con.close()


def trend(suite: str, days: int = 30) -> list[dict[str, Any]]:
    since_ts = int(time.time()) - days * 86400
    con = _connect()
    try:
        rows = con.execute(
            """
            SELECT
              date(ts, 'unixepoch') AS day,
              AVG(score) AS avg_score,
              COUNT(*) AS run_count
            FROM runs
            WHERE suite = ? AND ts >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (suite, since_ts),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()
