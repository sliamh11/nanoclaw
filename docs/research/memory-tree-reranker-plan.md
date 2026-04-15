# Memory-tree Tier 3: Cross-encoder reranker

**Status:** Plan, not yet implemented.
**Gated on:** ≥1 week of real query logs showing Phase 8 LLM verifier either insufficient or unsuitable. If real-world dogfood reveals the baseline is adequate, skip Tier 3 entirely.
**Parent context:** [`memory-tree-verifier-plan.md`](./memory-tree-verifier-plan.md) — Phase 8 LLM verifier. Benchmark showed it was too slow (45× p95) and over-rejects legitimate "partial" matches. [`memory-tree-benchmark-robustness.md`](./memory-tree-benchmark-robustness.md) — baseline characterization. [Tier 1 grid-sweep, 2026-04-15 session] — threshold/margin calibration doesn't help; abstain-near FPs share score-space with legit queries.

## Problem

Phase 8 (LLM verifier) proved two things:
1. **Latency wall.** Any autoregressive LLM takes seconds per query — 36–45× slower than baseline 102ms p95. Unusable as default-on.
2. **Wrong accuracy shape.** gemma4:e4b over-rejects ambiguous/multi legit hits because a general instruction-tuned LLM treats "partial answer" as not-answer. Drop-by-label is too aggressive.

The core intuition from Phase 8 is still correct: retrieval-layer cosine is a weak proxy for relevance, so a second-stage signal would help. But we need the right *shape* of second-stage — fast, relevance-native, not score-vs-score-LLM-judge.

## Proposal

Replace the Phase 8 LLM verifier with a **cross-encoder reranker**: a lightweight model purpose-built for (query, document) relevance scoring. Cross-encoders:

- take (query, doc) jointly as one input, attend over both
- output a single scalar relevance score (0–1), not a categorical label
- are ~100× smaller than instruction-tuned LLMs
- run at ~5–40ms per pair on CPU (vs ~1s for gemma4:e2b)
- are the standard IR re-ranking primitive (MS-MARCO, BEIR leaderboards)

## Model selection

| Model | Size | Latency/pair (CPU) | Notes |
|---|---|---|---|
| MS-MARCO MiniLM L-6-v2 | ~80 MB | ~5 ms | English only, ubiquitous baseline |
| MS-MARCO MiniLM L-12-v2 | ~130 MB | ~10 ms | Slightly better accuracy |
| **BGE-reranker-v2-m3** | **~560 MB** | **~20–40 ms** | **Multilingual (Hebrew), strong** |
| jina-reranker-v2-base-multilingual | ~300 MB | ~15 ms | Multilingual alt |
| BGE-reranker-base-v2-gemma-2b | ~1.4 GB | ~80 ms | Upper bound, overkill |

**Default pick: BGE-reranker-v2-m3.** The vault is multilingual-adjacent (Hebrew content in study notes, English frontmatter). MiniLM L-6 is English-only and would silently degrade on Hebrew queries. 560MB one-time download via `/setup` alongside the existing embedder (matches the Ollama-required precedent in PR #175).

For k=5 candidates: 100–200ms total cross-encoder latency → **well under the 500ms p95 target.**

## Runtime

Three viable paths, ranked:

1. **onnxruntime + quantized ONNX model.** `pip install onnxruntime` (~30MB). Download a pre-quantized int8 ONNX of BGE-reranker-v2-m3 (~150MB post-quantize) during setup. No torch dependency. Single forward pass per pair.
2. **fastembed** (`pip install fastembed`). Wraps onnxruntime + curates model list. Handles download automatically. ~10MB on top of onnxruntime. Cleaner API.
3. **sentence-transformers.** `pip install sentence-transformers` pulls torch (~800MB). Way too heavy for the payoff. **Rejected.**

**Default pick: fastembed.** Curation + auto-download cuts integration to ~30 lines of Python.

## Integration points

The reranker drop-in replaces the Phase 8 block in `retrieve()`:

```python
# Existing Phase 8 (LLM verifier — to be removed)
if use_verifier and top:
    labeled = verify_candidates(query, candidates, ...)
    top, dropped = rerank_by_verifier(top, labeled)

# Replacement Phase 8' (cross-encoder reranker)
if use_reranker and top:
    pairs = [(query, cand_text(id)) for (id, _, _, _, _) in top]
    rerank_scores = reranker.score(pairs)  # list[float]
    top = merge_scores(top, rerank_scores, weight=RERANKER_WEIGHT)
    top.sort(key=lambda r: r[3], reverse=True)
```

Key design choice: **combine, don't replace.** Phase 8 dropped candidates labelled 'no' — too binary. The reranker returns a continuous score, which we combine with the retriever score:

```python
final_score = (1 - w) * cosine_score + w * reranker_score
```

with `w ∈ [0, 1]` tunable. This preserves the retriever's signal (abstain-threshold still works) while letting the reranker demote "topically close but not answering" without hard-dropping them. Both signals agree → high final; disagree → moderate → may abstain via existing threshold logic.

**Default `w = 0.5`.** Gridable.

## Evaluation plan

Phase 3.1 — offline benchmark on 90q fixture:
- Compute `recall@k`, `MRR@k`, per-tag breakdown (reuse existing `benchmark` CLI)
- Compare to baseline (no reranker) and to PR #176 (LLM verifier)
- Target: `recall@k ≥ 0.822` (baseline — no regression) AND `abstain-near ≥ 0.75` (meaningful gain) AND `p95 ≤ 500ms`

Phase 3.2 — real-log calibration:
- After ≥1 week of dogfood, extract real queries from `~/.deus/memory_tree_queries.jsonl`
- Dedupe against benchmark fixture (to avoid contamination)
- Run `calibrate` on real queries labeled by query-log analysis heuristics (rephrasing cluster detection)
- Compare reranker-on vs reranker-off on this real subset

Phase 3.3 — ablation:
- Sweep `RERANKER_WEIGHT ∈ {0.0, 0.3, 0.5, 0.7, 1.0}`
- Find Pareto-optimal on (recall, abstain-near, p95)

## Rollback / opt-in gate

Match PR #176's pattern:
- Off by default behind `--rerank` CLI flag + `DEUS_TREE_RERANK=1` env
- `RERANKER_UNREACHABLE` exception class → fail-open to cosine-only ranking
- Feature flag in `retrieve()` kwarg: `use_reranker: bool = False`

Default-on gate only if Phase 3.1 benchmark + Phase 3.2 real-log analysis both meet targets.

## Risks and unknowns

1. **Download size.** BGE-reranker-v2-m3 is 560MB (150MB quantized). Adds ~2 min to `/setup` on broadband. Justified only if accuracy gain is real.
2. **Cold-start latency.** First reranker call loads the ONNX session (~1s). Subsequent calls are fast. Must pre-warm at startup (add to the existing setup/ollama.ts pattern).
3. **Hebrew content.** The vault has Hebrew study notes. BGE-reranker-v2-m3 is multilingual but not Hebrew-specialized. Worth spot-checking on a few Hebrew queries before committing.
4. **The same over-rejection risk as LLM verifier.** A trained reranker is less pathological than a general LLM, but may still downrank "topically close" above "partial answer." The combine-don't-replace design mitigates but doesn't eliminate this.
5. **May still not help abstain-near.** Tier 1 grid sweep revealed the fundamental issue: abstain-near false positives share score-space with legit queries. A reranker re-weights within the shortlist but doesn't synthesize new information. If the retriever puts a wrong file in top-5, the reranker can demote it, but abstain-near queries often don't have any right answer — so every rerank result is still wrong.

## Estimated effort

- Plan (this doc): **done** (~1 hr).
- Spike: `fastembed` install, download model, single-query smoke test against vault — 1 hr.
- Integration: `MemoryTreeReranker` wrapper + Phase 8' block + CLI flag + env gate — 3 hr.
- Tests: unit tests for reranker wrapper (transport stub, fail-open, score merge) — 2 hr.
- Phase 3.1 benchmark on 90q: 1 hr (mostly waiting).
- Phase 3.2 real-log calibration: 2 hr after ≥1 week of real data.
- Default-on decision + PR follow-up: 1 hr.

**Total: ~10 hr engineering + 1 week wall-clock for Phase 3.2.**

## Decision gate before starting

Do NOT start implementation until one of the following is true:
1. Real query logs (after ≥1 week of dogfood) show abstain-near false positives are actually user-visible problems (rephrased queries, user complaints, confidently-wrong answers that mislead).
2. We decide to ship PR #176's LLM verifier and need a lower-latency replacement.
3. A specific user-facing bug is traced back to retrieval quality that a reranker would fix.

If none of the above are true after ≥1 week, the baseline is good enough. Close Phase 8 + Tier 3, move on.
