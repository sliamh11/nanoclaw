#!/usr/bin/env python3
"""TREC-style pooled atom benchmark builder.

Stage 1: Sample real production queries (stratified, deduped)
Stage 2: Run multiple retrieval variants, pool all returned atoms
Stage 3: LLM-judge each (query, atom) pair blind
Stage 4: Export as frozen benchmark fixture

Usage:
  python3 scripts/trec_atom_benchmark.py --stage sample   # → ~/.deus/bench/sampled_queries.jsonl
  python3 scripts/trec_atom_benchmark.py --stage pool      # → ~/.deus/bench/pooled_atoms.jsonl
  python3 scripts/trec_atom_benchmark.py --stage judge     # → ~/.deus/bench/judged_pairs.jsonl (Gemini)
  python3 scripts/trec_atom_benchmark.py --stage judge --judge ollama --judge-model gemma4:e4b --fresh
  python3 scripts/trec_atom_benchmark.py --stage export    # → scripts/tests/fixtures/atom_queries_trec.jsonl
  python3 scripts/trec_atom_benchmark.py --stage all       # run all stages
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

BENCH_DIR = Path(os.path.expanduser("~/.deus/bench"))
LOG_PATH = Path(os.path.expanduser("~/.deus/memory_tree_queries.jsonl"))
DB_PATH = Path(os.path.expanduser("~/.deus/memory.db"))

SAMPLE_SIZE = 120
POOL_DEPTH = 10
RANDOM_SAMPLE_RATIO = 0.05


def _open_db():
    import sqlite_vec
    db = sqlite3.connect(str(DB_PATH))
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def _serialize(vec):
    return struct.pack(f"{len(vec)}f", *vec)


# ── Stage 1: Sample ─────────────────────────────────────────────────────────

def stage_sample():
    """Sample real production queries, stratified and deduped."""
    print("Stage 1: Sampling production queries...", file=sys.stderr)

    raw_queries: dict[str, dict] = {}
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                q = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            text = q.get("query", "")
            if len(text) < 15 or len(text) > 500:
                continue
            if text.startswith("/") or "<task-notification" in text:
                continue
            if "<system-reminder" in text or "Command running" in text:
                continue
            key = text.strip().lower()
            if key not in raw_queries:
                raw_queries[key] = {
                    "query": text,
                    "fell_back": q.get("fell_back", False),
                    "confidence": q.get("final_confidence", 0),
                    "results": q.get("results", []),
                    "ts": q.get("ts", ""),
                    "count": 0,
                }
            raw_queries[key]["count"] += 1

    unique = list(raw_queries.values())
    print(f"  Unique queries: {len(unique)}", file=sys.stderr)

    abstained = [q for q in unique if q["fell_back"]]
    retrieved = [q for q in unique if not q["fell_back"]]
    print(f"  Abstained: {len(abstained)}, Retrieved: {len(retrieved)}", file=sys.stderr)

    random.seed(42)

    target_abstain = min(len(abstained), SAMPLE_SIZE // 4)
    target_retrieved = SAMPLE_SIZE - target_abstain

    sampled_abstain = random.sample(abstained, min(target_abstain, len(abstained)))

    freq_sorted = sorted(retrieved, key=lambda q: -q["count"])
    high_freq = freq_sorted[:len(freq_sorted) // 3]
    mid_freq = freq_sorted[len(freq_sorted) // 3: 2 * len(freq_sorted) // 3]
    low_freq = freq_sorted[2 * len(freq_sorted) // 3:]

    per_bucket = target_retrieved // 3
    sampled_retrieved = (
        random.sample(high_freq, min(per_bucket, len(high_freq)))
        + random.sample(mid_freq, min(per_bucket, len(mid_freq)))
        + random.sample(low_freq, min(per_bucket, len(low_freq)))
    )

    sampled = sampled_abstain + sampled_retrieved
    random.shuffle(sampled)

    for s in sampled:
        s["query_hash"] = hashlib.sha256(s["query"].encode()).hexdigest()[:16]

    out_path = BENCH_DIR / "sampled_queries.jsonl"
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for s in sampled:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"  Sampled {len(sampled)} queries → {out_path}", file=sys.stderr)
    print(f"    Abstain: {len(sampled_abstain)}, Retrieved: {len(sampled_retrieved)}", file=sys.stderr)
    return sampled


# ── Stage 2: Pool ────────────────────────────────────────────────────────────

def stage_pool():
    """Run multiple retrieval variants, pool all returned atoms."""
    print("Stage 2: Pooling atoms from multiple retrieval variants...", file=sys.stderr)

    sample_path = BENCH_DIR / "sampled_queries.jsonl"
    if not sample_path.exists():
        print("ERROR: Run --stage sample first", file=sys.stderr)
        sys.exit(1)

    with open(sample_path, encoding="utf-8") as f:
        queries = [json.loads(line) for line in f]

    from evolution.providers.embeddings import embed as provider_embed
    import memory_indexer as mi

    db = _open_db()

    atom_angle_count = int(os.environ.get("DEUS_ATOM_ANGLE_COUNT", "3"))

    pooled: list[dict] = []

    for i, q_entry in enumerate(queries):
        query = q_entry["query"]
        q_vec = provider_embed(query)
        q_blob = _serialize(q_vec)

        atom_pool: dict[int, dict] = {}

        # Variant 1: Raw ANN (no angles)
        rows = db.execute(
            """SELECT e.id, e.chunk, v.distance
               FROM embeddings v JOIN entries e ON e.id = v.rowid
               WHERE v.embedding MATCH ? AND k = ?
               AND e.type = 'atom' AND e.orphaned_at IS NULL
               ORDER BY v.distance LIMIT ?""",
            [q_blob, POOL_DEPTH * 3, POOL_DEPTH],
        ).fetchall()
        for eid, chunk, dist in rows:
            if eid not in atom_pool:
                atom_pool[eid] = {"id": eid, "chunk": chunk, "sources": {}}
            atom_pool[eid]["sources"]["raw"] = round(dist, 4)

        # Variant 2: Angle ANN
        try:
            angle_rows = db.execute(
                """SELECT aa.atom_id, ae.distance
                   FROM atom_angle_embeddings ae
                   JOIN atom_approach_angles aa
                     ON ae.rowid = aa.atom_id * ? + aa.angle_idx
                   WHERE ae.embedding MATCH ? AND k = ?
                   ORDER BY ae.distance LIMIT ?""",
                [atom_angle_count, q_blob, POOL_DEPTH * 3, POOL_DEPTH * 2],
            ).fetchall()
            angle_best: dict[int, float] = {}
            for aid, adist in angle_rows:
                if aid not in angle_best or adist < angle_best[aid]:
                    angle_best[aid] = adist
            for aid, adist in list(angle_best.items())[:POOL_DEPTH]:
                if aid not in atom_pool:
                    chunk_row = db.execute(
                        "SELECT chunk FROM entries WHERE id = ? AND type = 'atom' AND orphaned_at IS NULL",
                        [aid],
                    ).fetchone()
                    if chunk_row:
                        atom_pool[aid] = {"id": aid, "chunk": chunk_row[0], "sources": {}}
                if aid in atom_pool:
                    atom_pool[aid]["sources"]["angle"] = round(adist, 4)
        except sqlite3.OperationalError:
            pass

        # Variant 3: FTS5
        try:
            fts_rows = db.execute(
                """SELECT e.id, e.chunk
                   FROM entries_fts f
                   JOIN entries e ON e.rowid = f.rowid
                   WHERE entries_fts MATCH ? AND e.type = 'atom' AND e.orphaned_at IS NULL
                   LIMIT ?""",
                [mi._fts_escape(query), POOL_DEPTH],
            ).fetchall()
            for eid, chunk in fts_rows:
                if eid not in atom_pool:
                    atom_pool[eid] = {"id": eid, "chunk": chunk, "sources": {}}
                atom_pool[eid]["sources"]["fts"] = 0.0
        except sqlite3.OperationalError:
            pass

        # Add random sample of un-pooled atoms (catch blind spots)
        n_random = max(1, int(len(atom_pool) * RANDOM_SAMPLE_RATIO))
        pooled_ids = set(atom_pool.keys())
        random_rows = db.execute(
            """SELECT id, chunk FROM entries
               WHERE type = 'atom' AND orphaned_at IS NULL
               ORDER BY RANDOM() LIMIT ?""",
            [n_random + len(pooled_ids)],
        ).fetchall()
        added_random = 0
        for eid, chunk in random_rows:
            if eid not in pooled_ids and added_random < n_random:
                atom_pool[eid] = {"id": eid, "chunk": chunk, "sources": {"random": 0.0}}
                added_random += 1

        pooled.append({
            "query": query,
            "query_hash": q_entry["query_hash"],
            "fell_back": q_entry["fell_back"],
            "atoms": list(atom_pool.values()),
            "pool_size": len(atom_pool),
        })

        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(queries)} queries pooled", file=sys.stderr)

    db.close()

    out_path = BENCH_DIR / "pooled_atoms.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for p in pooled:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    total_pairs = sum(p["pool_size"] for p in pooled)
    print(f"  Pooled {total_pairs} (query, atom) pairs across {len(pooled)} queries → {out_path}", file=sys.stderr)
    return pooled


# ── Stage 3: Judge ───────────────────────────────────────────────────────────

JUDGE_PROMPT = (
    "You are a relevance judge for a personal knowledge retrieval system.\n\n"
    "Given a user's query and a stored fact about them, rate relevance:\n"
    "- 2: Directly answers or is essential context for the query\n"
    "- 1: Tangentially related, might be useful background\n"
    "- 0: Irrelevant to the query\n\n"
    "User query: {query}\n"
    "Stored fact: {atom}\n\n"
    "Respond with ONLY a single digit: 0, 1, or 2"
)


def _ollama_judge(prompt: str, model: str) -> int | None:
    """Call Ollama /api/generate and parse the first digit from the response."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 512},
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    text = body.get("response", "").strip()
    for ch in text:
        if ch in ("0", "1", "2"):
            return int(ch)
    return None


def _gemini_judge(prompt: str, client, genai_types, gen_models: list[str]) -> tuple[int | None, bool]:
    """Call Gemini API and return (score, was_rate_limited)."""
    for model in gen_models:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=4,
                ),
            )
            text = resp.text.strip()
            score = int(text) if text in ("0", "1", "2") else 0
            return score, False
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                time.sleep(2)
                continue
            return None, False
    return None, True


def stage_judge(judge_backend: str = "gemini", judge_model: str = "gemma4:e4b"):
    """LLM-judge each (query, atom) pair blind."""
    print(f"Stage 3: Judging (query, atom) pairs [judge={judge_backend}, model={judge_model}]...", file=sys.stderr)

    pool_path = BENCH_DIR / "pooled_atoms.jsonl"
    if not pool_path.exists():
        print("ERROR: Run --stage pool first", file=sys.stderr)
        sys.exit(1)

    with open(pool_path, encoding="utf-8") as f:
        pooled = [json.loads(line) for line in f]

    cache_path = BENCH_DIR / "judge_cache.json"
    cache: dict[str, int] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        print(f"  Loaded {len(cache)} cached judgments", file=sys.stderr)

    gemini_client = None
    gemini_types = None
    gen_models = None
    if judge_backend == "gemini":
        from google import genai
        from google.genai import types as genai_types
        from evolution.config import load_api_key, GEN_MODELS
        gemini_client = genai.Client(api_key=load_api_key())
        gemini_types = genai_types
        gen_models = GEN_MODELS

    total_pairs = sum(len(p["atoms"]) for p in pooled)
    cached_hits = 0
    api_calls = 0
    errors = 0
    consecutive_errors = 0

    for pi, pool_entry in enumerate(pooled):
        query = pool_entry["query"]
        for atom_entry in pool_entry["atoms"]:
            atom_text = atom_entry["chunk"]
            cache_key = hashlib.sha256(f"{query}|||{atom_text}".encode()).hexdigest()[:24]

            if cache_key in cache:
                atom_entry["relevance"] = cache[cache_key]
                cached_hits += 1
                continue

            prompt = JUDGE_PROMPT.format(query=query, atom=atom_text)
            score = None

            if judge_backend == "ollama":
                score = _ollama_judge(prompt, judge_model)
                if score is not None:
                    api_calls += 1
            else:
                score, was_rate_limited = _gemini_judge(prompt, gemini_client, gemini_types, gen_models)
                if score is not None:
                    api_calls += 1

            if score is None:
                errors += 1
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    wait = min(60, consecutive_errors * 10)
                    print(f"  Rate limited ({consecutive_errors}x), waiting {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                continue
            consecutive_errors = 0
            atom_entry["relevance"] = score
            cache[cache_key] = score

            if (api_calls + cached_hits) % 50 == 0:
                cache_path.write_text(json.dumps(cache))

        if (pi + 1) % 10 == 0:
            print(f"  {pi+1}/{len(pooled)} queries judged ({api_calls} calls, {cached_hits} cached, {errors} errors)", file=sys.stderr)

    cache_path.write_text(json.dumps(cache))
    print(f"  Total: {api_calls} calls, {cached_hits} cached, {errors} errors", file=sys.stderr)

    out_path = BENCH_DIR / "judged_pairs.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for p in pooled:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    relevant = sum(
        1 for p in pooled for a in p["atoms"] if a.get("relevance", 0) >= 2
    )
    tangential = sum(
        1 for p in pooled for a in p["atoms"] if a.get("relevance", 0) == 1
    )
    irrelevant = sum(
        1 for p in pooled for a in p["atoms"] if a.get("relevance", 0) == 0
    )
    print(f"  Judgments: {relevant} relevant, {tangential} tangential, {irrelevant} irrelevant", file=sys.stderr)
    return pooled


# ── Stage 4: Export ──────────────────────────────────────────────────────────

def stage_export():
    """Export judged pairs as a frozen benchmark fixture."""
    print("Stage 4: Exporting benchmark fixture...", file=sys.stderr)

    judged_path = BENCH_DIR / "judged_pairs.jsonl"
    if not judged_path.exists():
        print("ERROR: Run --stage judge first", file=sys.stderr)
        sys.exit(1)

    with open(judged_path, encoding="utf-8") as f:
        pooled = [json.loads(line) for line in f]

    fixture = []
    for p in pooled:
        relevant_atoms = [
            a["chunk"] for a in p["atoms"] if a.get("relevance", 0) >= 2
        ]

        if relevant_atoms:
            tag = "trec-positive"
        elif p["fell_back"]:
            tag = "trec-abstain"
        else:
            tag = "trec-no-relevant"

        expected = []
        for chunk in relevant_atoms:
            words = chunk.split()
            if len(words) >= 4:
                expected.append(" ".join(words[1:5]).lower())

        fixture.append({
            "query": p["query"],
            "expected_atoms": expected,
            "tag": tag,
            "source": "trec-pooling",
        })

    out_path = Path(_PROJECT_ROOT) / "scripts" / "tests" / "fixtures" / "atom_queries_trec.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for entry in fixture:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    pos = sum(1 for e in fixture if e["tag"] == "trec-positive")
    abstain = sum(1 for e in fixture if e["tag"] == "trec-abstain")
    no_rel = sum(1 for e in fixture if e["tag"] == "trec-no-relevant")
    print(f"  Exported {len(fixture)} queries: {pos} positive, {abstain} abstain, {no_rel} no-relevant", file=sys.stderr)
    print(f"  → {out_path}", file=sys.stderr)


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TREC-style pooled atom benchmark")
    parser.add_argument("--stage", required=True,
                        choices=["sample", "pool", "judge", "export", "all"],
                        help="Which stage to run")
    parser.add_argument("--judge", choices=["gemini", "ollama"], default="gemini",
                        help="Judge backend (default: gemini)")
    parser.add_argument("--judge-model", default="gemma4:e4b",
                        help="Model name for ollama judge (default: gemma4:e4b)")
    parser.add_argument("--fresh", action="store_true",
                        help="Clear judge cache before judging")
    args = parser.parse_args()

    if args.fresh:
        cache_path = BENCH_DIR / "judge_cache.json"
        if cache_path.exists():
            cache_path.unlink()
            print("Cleared judge cache.", file=sys.stderr)

    if args.stage == "sample" or args.stage == "all":
        stage_sample()
    if args.stage == "pool" or args.stage == "all":
        stage_pool()
    if args.stage == "judge" or args.stage == "all":
        stage_judge(judge_backend=args.judge, judge_model=args.judge_model)
    if args.stage == "export" or args.stage == "all":
        stage_export()


if __name__ == "__main__":
    main()
