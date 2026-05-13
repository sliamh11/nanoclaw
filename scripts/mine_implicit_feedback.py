#!/usr/bin/env python3
"""Mine implicit feedback signals from the production retrieval log.

Identifies:
1. Wrong abstains: user reformulated after system abstained (same topic, different words)
2. Positive retrievals: user continued on-topic after successful retrieval
3. Negative retrievals: user reformulated after retrieval (wasn't satisfied)

Output: JSONL fixture for atom benchmark evaluation.

Usage:
  python3 scripts/mine_implicit_feedback.py [--output PATH] [--min-confidence 0.6]
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

LOG_PATH = Path(os.path.expanduser("~/.deus/memory_tree_queries.jsonl"))
SESSION_GAP = timedelta(minutes=30)
REFORMULATION_THRESHOLD = 0.65
CONTINUATION_THRESHOLD = 0.40


def load_queries(path: Path) -> list[dict]:
    queries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                q = json.loads(line)
                if len(q.get("query", "")) < 15:
                    continue
                if q["query"].startswith("/") or "<task-notification" in q["query"]:
                    continue
                if "<system-reminder" in q["query"] or q["query"].startswith("Command running"):
                    continue
                queries.append(q)
            except (json.JSONDecodeError, KeyError):
                pass
    return queries


def deduplicate_sequential(queries: list[dict]) -> list[dict]:
    """Remove exact duplicate queries within 5 seconds (hook double-fires)."""
    result = []
    for q in queries:
        if result and result[-1]["query"] == q["query"]:
            try:
                t1 = datetime.fromisoformat(result[-1]["ts"])
                t2 = datetime.fromisoformat(q["ts"])
                if abs((t2 - t1).total_seconds()) < 5:
                    continue
            except (ValueError, TypeError):
                pass
        result.append(q)
    return result


def split_sessions(queries: list[dict]) -> list[list[dict]]:
    """Split queries into sessions based on time gap."""
    sessions: list[list[dict]] = []
    current: list[dict] = []
    for q in queries:
        if current:
            try:
                t_prev = datetime.fromisoformat(current[-1]["ts"])
                t_curr = datetime.fromisoformat(q["ts"])
                if (t_curr - t_prev) > SESSION_GAP:
                    sessions.append(current)
                    current = []
            except (ValueError, TypeError):
                pass
        current.append(q)
    if current:
        sessions.append(current)
    return sessions


def compute_similarity(text_a: str, text_b: str, embed_fn) -> float:
    """Compute cosine similarity between two texts."""
    vec_a = embed_fn(text_a)
    vec_b = embed_fn(text_b)
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def mine_signals(sessions: list[list[dict]], embed_fn) -> dict:
    """Mine implicit feedback signals from session query sequences."""
    wrong_abstains = []
    positive_retrievals = []
    negative_retrievals = []

    for session in sessions:
        for i, q in enumerate(session):
            if i + 1 >= len(session):
                continue
            next_q = session[i + 1]

            sim = compute_similarity(q["query"], next_q["query"], embed_fn)

            if q.get("fell_back"):
                if sim >= REFORMULATION_THRESHOLD and not next_q.get("fell_back"):
                    wrong_abstains.append({
                        "original_query": q["query"],
                        "reformulated_query": next_q["query"],
                        "similarity": round(sim, 4),
                        "original_confidence": q.get("final_confidence", 0),
                        "reformulated_results": next_q.get("results", []),
                        "ts": q["ts"],
                        "signal": "wrong-abstain",
                    })
            elif not q.get("fell_back") and q.get("results"):
                if sim >= REFORMULATION_THRESHOLD:
                    if next_q.get("fell_back") or next_q["query"] != q["query"]:
                        negative_retrievals.append({
                            "query": q["query"],
                            "reformulated_query": next_q["query"],
                            "similarity": round(sim, 4),
                            "retrieved_paths": q.get("results", []),
                            "ts": q["ts"],
                            "signal": "negative-retrieval",
                        })
                elif sim >= CONTINUATION_THRESHOLD:
                    positive_retrievals.append({
                        "query": q["query"],
                        "next_query": next_q["query"],
                        "similarity": round(sim, 4),
                        "retrieved_paths": q.get("results", []),
                        "ts": q["ts"],
                        "signal": "positive-retrieval",
                    })

    return {
        "wrong_abstains": wrong_abstains,
        "positive_retrievals": positive_retrievals,
        "negative_retrievals": negative_retrievals,
    }


def build_fixture(signals: dict, max_per_type: int = 30) -> list[dict]:
    """Convert mined signals into atom benchmark fixture entries."""
    fixture = []

    for wa in sorted(signals["wrong_abstains"], key=lambda x: -x["similarity"])[:max_per_type]:
        fixture.append({
            "query": wa["original_query"],
            "expected_atoms": [],
            "tag": "wrong-abstain",
            "source": "implicit-feedback",
            "meta": {
                "reformulated_to": wa["reformulated_query"],
                "similarity": wa["similarity"],
                "confidence": wa["original_confidence"],
            },
        })

    for pr in sorted(signals["positive_retrievals"], key=lambda x: -x["similarity"])[:max_per_type]:
        fixture.append({
            "query": pr["query"],
            "expected_atoms": [],
            "tag": "positive-retrieval",
            "source": "implicit-feedback",
            "meta": {
                "retrieved_paths": pr["retrieved_paths"][:3],
                "continuation_query": pr["next_query"],
                "similarity": pr["similarity"],
            },
        })

    return fixture


def main():
    parser = argparse.ArgumentParser(description="Mine implicit feedback from retrieval log")
    parser.add_argument("--output", default=None, help="Output fixture path")
    parser.add_argument("--stats-only", action="store_true", help="Print stats without embedding")
    parser.add_argument("--max-per-type", type=int, default=30, help="Max entries per signal type")
    args = parser.parse_args()

    if not LOG_PATH.exists():
        print(f"ERROR: Log not found: {LOG_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading queries from {LOG_PATH}...", file=sys.stderr)
    raw = load_queries(LOG_PATH)
    print(f"  Loaded {len(raw)} clean queries", file=sys.stderr)

    deduped = deduplicate_sequential(raw)
    print(f"  After dedup: {len(deduped)}", file=sys.stderr)

    sessions = split_sessions(deduped)
    print(f"  Sessions: {len(sessions)}", file=sys.stderr)
    multi_query = [s for s in sessions if len(s) >= 2]
    print(f"  Sessions with 2+ queries: {len(multi_query)}", file=sys.stderr)

    fell_back = sum(1 for q in deduped if q.get("fell_back"))
    print(f"  Abstain rate: {fell_back}/{len(deduped)} ({100*fell_back/len(deduped):.1f}%)", file=sys.stderr)

    if args.stats_only:
        return

    print("Computing similarities (this may take a few minutes)...", file=sys.stderr)
    from evolution.providers.embeddings import embed as provider_embed

    _cache: dict[str, list[float]] = {}

    def cached_embed(text: str) -> list[float]:
        if text not in _cache:
            _cache[text] = provider_embed(text)
        return _cache[text]

    signals = mine_signals(multi_query, cached_embed)

    print(f"\n=== MINED SIGNALS ===", file=sys.stderr)
    print(f"Wrong abstains:       {len(signals['wrong_abstains'])}", file=sys.stderr)
    print(f"Positive retrievals:  {len(signals['positive_retrievals'])}", file=sys.stderr)
    print(f"Negative retrievals:  {len(signals['negative_retrievals'])}", file=sys.stderr)

    if signals["wrong_abstains"]:
        print(f"\n=== TOP WRONG ABSTAINS ===", file=sys.stderr)
        for wa in sorted(signals["wrong_abstains"], key=lambda x: -x["similarity"])[:10]:
            print(f"  [{wa['similarity']:.3f}] \"{wa['original_query'][:60]}\"", file=sys.stderr)
            print(f"    → reformulated: \"{wa['reformulated_query'][:60]}\"", file=sys.stderr)

    if signals["negative_retrievals"]:
        print(f"\n=== TOP NEGATIVE RETRIEVALS ===", file=sys.stderr)
        for nr in sorted(signals["negative_retrievals"], key=lambda x: -x["similarity"])[:10]:
            print(f"  [{nr['similarity']:.3f}] \"{nr['query'][:60]}\"", file=sys.stderr)
            print(f"    → reformulated: \"{nr['reformulated_query'][:60]}\"", file=sys.stderr)

    fixture = build_fixture(signals, max_per_type=args.max_per_type)
    output = json.dumps({"signals": signals, "fixture_count": len(fixture)}, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            for entry in fixture:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"\nFixture written to {out_path} ({len(fixture)} entries)", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
