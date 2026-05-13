#!/usr/bin/env python3
"""Embedding model shootout: compare discrimination quality across models.

Tests each model on the same set of benchmark queries + target atoms.
No DB changes, no production code touched - pure in-memory comparison.

Usage:
  python3 scripts/embedding_shootout.py [--models embeddinggemma,snowflake-arctic-embed2,...]
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import sqlite3
import struct
import sys
import time
import urllib.parse
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

FIXTURE_PATH = Path(_PROJECT_ROOT) / "scripts" / "tests" / "fixtures" / "atom_queries.jsonl"
DB_PATH = Path(os.path.expanduser("~/.deus/memory.db"))

DEFAULT_MODELS = [
    "embeddinggemma",
    "snowflake-arctic-embed2",
    "nomic-embed-text",
    "bge-m3",
    "mxbai-embed-large",
]

PREFIXED_MODELS = {
    "snowflake-arctic-embed2": "Represent this sentence for searching relevant passages: ",
    "nomic-embed-text": "search_query: ",
    "bge-m3": "Represent this sentence: ",
}

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


def ollama_embed(texts: list[str], model: str) -> list[list[float]]:
    parsed = urllib.parse.urlparse(OLLAMA_HOST)
    hostname = parsed.hostname or "localhost"
    port = parsed.port or 11434

    payload = json.dumps({"model": model, "input": texts}).encode()
    conn = http.client.HTTPConnection(hostname, port, timeout=60)
    conn.request("POST", "/api/embed", body=payload,
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()

    if resp.status != 200:
        raise RuntimeError(f"Ollama {model} returned {resp.status}: {body[:200]}")

    data = json.loads(body)
    return data.get("embeddings", [])


def l2_dist(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def load_benchmark() -> tuple[list[dict], dict[int, str]]:
    """Load benchmark queries and find target atoms in the DB."""
    import sqlite_vec
    db = sqlite3.connect(str(DB_PATH))
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)

    queries = []
    with open(FIXTURE_PATH) as f:
        for line in f:
            q = json.loads(line.strip())
            if q.get("expected_atoms"):
                queries.append(q)

    target_atoms: dict[int, str] = {}
    all_atoms = db.execute(
        "SELECT id, chunk FROM entries WHERE type = 'atom' AND orphaned_at IS NULL"
    ).fetchall()

    for q in queries:
        for exp in q["expected_atoms"]:
            for aid, chunk in all_atoms:
                if exp.lower() in chunk.lower() and aid not in target_atoms:
                    target_atoms[aid] = chunk

    distractor_atoms = {}
    for aid, chunk in all_atoms:
        if aid not in target_atoms:
            distractor_atoms[aid] = chunk
        if len(distractor_atoms) >= 50:
            break

    db.close()
    return queries, target_atoms, distractor_atoms


def evaluate_model(
    model: str,
    queries: list[dict],
    target_atoms: dict[int, str],
    distractor_atoms: dict[int, str],
    use_prefix: bool = False,
) -> dict:
    """Evaluate a single model on discrimination quality."""
    prefix = PREFIXED_MODELS.get(model, "") if use_prefix else ""
    label = f"{model}" + (" +prefix" if use_prefix and prefix else "")
    print(f"\n  Testing {label}...", file=sys.stderr)

    all_atoms = {**target_atoms, **distractor_atoms}
    atom_texts = list(all_atoms.values())
    atom_ids = list(all_atoms.keys())
    target_id_set = set(target_atoms.keys())

    t0 = time.monotonic()
    try:
        atom_vecs = ollama_embed(atom_texts, model)
    except Exception as e:
        print(f"    ERROR embedding atoms: {e}", file=sys.stderr)
        return {"model": label, "error": str(e)}

    query_texts = [q["query"] for q in queries]
    if prefix:
        query_texts_prefixed = [prefix + qt for qt in query_texts]
    else:
        query_texts_prefixed = query_texts

    try:
        query_vecs = ollama_embed(query_texts_prefixed, model)
    except Exception as e:
        print(f"    ERROR embedding queries: {e}", file=sys.stderr)
        return {"model": label, "error": str(e)}

    embed_time = time.monotonic() - t0
    dim = len(atom_vecs[0]) if atom_vecs else 0
    print(f"    dim={dim}, embed_time={embed_time:.1f}s", file=sys.stderr)

    hits = 0
    score_gaps = []
    discrimination_ratios = []
    relevant_dists = []
    irrelevant_dists = []

    for qi, (q, q_vec) in enumerate(zip(queries, query_vecs)):
        expected = q["expected_atoms"]

        dists = []
        for ai, a_vec in enumerate(atom_vecs):
            dist = l2_dist(q_vec, a_vec)
            is_target = atom_ids[ai] in target_id_set
            is_relevant = is_target and any(
                exp.lower() in atom_texts[ai].lower() for exp in expected
            )
            dists.append((dist, atom_ids[ai], is_relevant))
            if is_relevant:
                relevant_dists.append(dist)
            else:
                irrelevant_dists.append(dist)

        dists.sort(key=lambda x: x[0])
        top_k = dists[:5]

        hit = any(is_rel for _, _, is_rel in top_k)
        if hit:
            hits += 1

        rel_in_results = [d for d, _, is_rel in dists if is_rel]
        irr_in_results = [d for d, _, is_rel in dists if not is_rel]
        if rel_in_results and irr_in_results:
            gap = min(irr_in_results) - min(rel_in_results)
            score_gaps.append(gap)

        all_dists_vals = [d for d, _, _ in dists]
        if all_dists_vals:
            mean_d = sum(all_dists_vals) / len(all_dists_vals)
            std_d = (sum((d - mean_d) ** 2 for d in all_dists_vals) / len(all_dists_vals)) ** 0.5
            cv = std_d / mean_d if mean_d > 0 else 0
            discrimination_ratios.append(cv)

    recall = hits / len(queries) if queries else 0

    def safe_stats(vals):
        if not vals:
            return {"mean": 0, "std": 0, "min": 0, "max": 0, "p10": 0}
        vals_s = sorted(vals)
        mean = sum(vals_s) / len(vals_s)
        std = (sum((v - mean) ** 2 for v in vals_s) / len(vals_s)) ** 0.5
        p10 = vals_s[max(0, len(vals_s) // 10)]
        return {"mean": round(mean, 4), "std": round(std, 4),
                "min": round(vals_s[0], 4), "max": round(vals_s[-1], 4),
                "p10": round(p10, 4)}

    result = {
        "model": label,
        "dim": dim,
        "recall_at_5": round(recall, 4),
        "hits": hits,
        "total": len(queries),
        "embed_time_s": round(embed_time, 1),
        "score_gap": safe_stats(score_gaps),
        "discrimination_cv": safe_stats(discrimination_ratios),
        "relevant_dist": safe_stats(relevant_dists),
        "irrelevant_dist": safe_stats(irrelevant_dists),
        "separation": round(
            safe_stats(irrelevant_dists)["mean"] - safe_stats(relevant_dists)["mean"], 4
        ),
    }

    # Z-score analysis
    if relevant_dists and irrelevant_dists:
        all_d = relevant_dists + irrelevant_dists
        mu = sum(all_d) / len(all_d)
        sigma = (sum((d - mu) ** 2 for d in all_d) / len(all_d)) ** 0.5
        if sigma > 0:
            rel_z = [(d - mu) / sigma for d in relevant_dists]
            irr_z = [(d - mu) / sigma for d in irrelevant_dists]
            result["zscore_relevant_mean"] = round(sum(rel_z) / len(rel_z), 4)
            result["zscore_irrelevant_mean"] = round(sum(irr_z) / len(irr_z), 4)
            result["zscore_separation"] = round(
                result["zscore_irrelevant_mean"] - result["zscore_relevant_mean"], 4
            )

    return result


def evaluate_cross_encoder(
    queries: list[dict],
    target_atoms: dict[int, str],
    distractor_atoms: dict[int, str],
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
) -> dict:
    """Evaluate a cross-encoder reranker on discrimination quality."""
    print(f"\n  Testing cross-encoder ({model_name})...", file=sys.stderr)
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        return {"model": "cross-encoder", "error": "sentence-transformers not installed"}

    t0 = time.monotonic()
    ce = CrossEncoder(model_name)
    load_time = time.monotonic() - t0
    print(f"    Model loaded in {load_time:.1f}s", file=sys.stderr)

    all_atoms = {**target_atoms, **distractor_atoms}
    atom_texts = list(all_atoms.values())
    atom_ids = list(all_atoms.keys())
    target_id_set = set(target_atoms.keys())

    hits = 0
    score_gaps = []
    discrimination_ratios = []
    relevant_scores = []
    irrelevant_scores = []

    t0 = time.monotonic()
    for qi, q in enumerate(queries):
        query_text = q["query"]
        expected = q["expected_atoms"]

        pairs = [(query_text, atom_text) for atom_text in atom_texts]
        scores = ce.predict(pairs).tolist()

        scored = []
        for ai, score in enumerate(scores):
            is_target = atom_ids[ai] in target_id_set
            is_relevant = is_target and any(
                exp.lower() in atom_texts[ai].lower() for exp in expected
            )
            scored.append((score, atom_ids[ai], is_relevant))
            if is_relevant:
                relevant_scores.append(score)
            else:
                irrelevant_scores.append(score)

        scored.sort(key=lambda x: -x[0])
        top_k = scored[:5]

        hit = any(is_rel for _, _, is_rel in top_k)
        if hit:
            hits += 1

        rel_s = [s for s, _, is_rel in scored if is_rel]
        irr_s = [s for s, _, is_rel in scored if not is_rel]
        if rel_s and irr_s:
            score_gaps.append(max(rel_s) - max(irr_s))

        all_s = [s for s, _, _ in scored]
        if all_s:
            mean_s = sum(all_s) / len(all_s)
            std_s = (sum((s - mean_s) ** 2 for s in all_s) / len(all_s)) ** 0.5
            cv = std_s / abs(mean_s) if mean_s != 0 else 0
            discrimination_ratios.append(cv)

    eval_time = time.monotonic() - t0
    recall = hits / len(queries) if queries else 0

    def safe_stats(vals):
        if not vals:
            return {"mean": 0, "std": 0, "min": 0, "max": 0, "p10": 0}
        vals_s = sorted(vals)
        mean = sum(vals_s) / len(vals_s)
        std = (sum((v - mean) ** 2 for v in vals_s) / len(vals_s)) ** 0.5
        p10 = vals_s[max(0, len(vals_s) // 10)]
        return {"mean": round(mean, 4), "std": round(std, 4),
                "min": round(vals_s[0], 4), "max": round(vals_s[-1], 4),
                "p10": round(p10, 4)}

    result = {
        "model": "cross-encoder",
        "dim": "N/A",
        "recall_at_5": round(recall, 4),
        "hits": hits,
        "total": len(queries),
        "embed_time_s": round(eval_time, 1),
        "score_gap": safe_stats(score_gaps),
        "discrimination_cv": safe_stats(discrimination_ratios),
        "relevant_dist": safe_stats(relevant_scores),
        "irrelevant_dist": safe_stats(irrelevant_scores),
        "separation": round(
            safe_stats(relevant_scores)["mean"] - safe_stats(irrelevant_scores)["mean"], 4
        ),
        "zscore_separation": 0,
    }

    if relevant_scores and irrelevant_scores:
        all_s = relevant_scores + irrelevant_scores
        mu = sum(all_s) / len(all_s)
        sigma = (sum((s - mu) ** 2 for s in all_s) / len(all_s)) ** 0.5
        if sigma > 0:
            rel_z = [(s - mu) / sigma for s in relevant_scores]
            irr_z = [(s - mu) / sigma for s in irrelevant_scores]
            result["zscore_relevant_mean"] = round(sum(rel_z) / len(rel_z), 4)
            result["zscore_irrelevant_mean"] = round(sum(irr_z) / len(irr_z), 4)
            result["zscore_separation"] = round(
                result["zscore_relevant_mean"] - result["zscore_irrelevant_mean"], 4
            )

    return result


def print_comparison(results: list[dict]):
    print("\n" + "=" * 90)
    print(f"{'Model':<30} {'Dim':>4} {'Recall@5':>9} {'Separation':>11} {'Gap(mean)':>10} {'CV(mean)':>9} {'Z-sep':>7} {'Time':>5}")
    print("-" * 90)
    for r in sorted(results, key=lambda x: -x.get("recall_at_5", 0)):
        if "error" in r:
            print(f"{r['model']:<30} ERROR: {r['error'][:50]}")
            continue
        print(
            f"{r['model']:<30} {r['dim']:>4} {r['recall_at_5']:>9.3f} "
            f"{r['separation']:>11.4f} {r['score_gap']['mean']:>10.4f} "
            f"{r['discrimination_cv']['mean']:>9.4f} "
            f"{r.get('zscore_separation', 0):>7.3f} "
            f"{r['embed_time_s']:>5.1f}s"
        )
    print("=" * 90)
    print("\nMetrics explained:")
    print("  Recall@5:    fraction of queries where the correct atom is in top-5 by L2 distance")
    print("  Separation:  mean(irrelevant_dist) - mean(relevant_dist). Higher = better discrimination")
    print("  Gap(mean):   mean of per-query (best_wrong - best_right). Higher = more confident correct rankings")
    print("  CV(mean):    coefficient of variation of distances per query. Higher = more spread (less compression)")
    print("  Z-sep:       z-score separation (relevant vs irrelevant). Higher = better statistical discrimination")


def main():
    parser = argparse.ArgumentParser(description="Embedding model shootout")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="Comma-separated model names")
    parser.add_argument("--no-prefix", action="store_true",
                        help="Skip prefix variants")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--no-cross-encoder", action="store_true",
                        help="Skip cross-encoder evaluation")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",")]

    print("Loading benchmark data...", file=sys.stderr)
    queries, target_atoms, distractor_atoms = load_benchmark()
    print(f"  {len(queries)} queries, {len(target_atoms)} target atoms, {len(distractor_atoms)} distractors", file=sys.stderr)

    results = []
    for model in models:
        r = evaluate_model(model, queries, target_atoms, distractor_atoms, use_prefix=False)
        results.append(r)

        if not args.no_prefix and model in PREFIXED_MODELS:
            r_prefix = evaluate_model(model, queries, target_atoms, distractor_atoms, use_prefix=True)
            results.append(r_prefix)

    if not args.no_cross_encoder:
        r_ce = evaluate_cross_encoder(queries, target_atoms, distractor_atoms)
        results.append(r_ce)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_comparison(results)


if __name__ == "__main__":
    main()
