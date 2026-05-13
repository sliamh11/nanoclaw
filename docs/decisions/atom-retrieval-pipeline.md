# ADR: Atom retrieval pipeline — multi-stage architecture

**Date:** 2026-05-13
**Status:** Accepted — shipped in PR #382.
**Scope:** `scripts/memory_indexer.py` (cmd_query, atom_benchmark, backfill_atom_angles), `scripts/tests/fixtures/atom_queries.jsonl`.

## Context

Atom retrieval was disabled in production (`DEUS_ATOM_DIST=0`) because it was evaluated as "actively harmful" — net -10 on 18 abstain queries at threshold 1.2. Root cause: embeddinggemma compresses 15-30 word atom descriptions into L2 distances of 1.0-1.2 regardless of semantic relevance.

Three independent improvements were investigated and combined:

## Decisions

### 1. Approach angles for atoms (HyPE pattern)

Generate 3 synthetic questions per atom using `generate_approach_angles()` from memory_tree.py. Store in `atom_approach_angles` table with ANN-indexed `atom_angle_embeddings` vec0 table. Transforms retrieval from statement-vs-question (poor) to question-vs-question (good).

**Validated:** 50-atom experiment showed recall 0.444 to 1.000 on subset. Full backfill: `--backfill-atom-angles` (1,342 atoms, ~45 min via Ollama).

**Rowid encoding:** `atom_id * ATOM_ANGLE_COUNT + angle_idx`. Changing ATOM_ANGLE_COUNT (default 3) requires a full rebuild.

### 2. Cross-encoder reranker (two-stage retrieval)

Stage 1 (embeddinggemma bi-encoder) retrieves top-20 candidates via ANN. Stage 2 (`cross-encoder/ms-marco-MiniLM-L-6-v2`, 22M params) jointly scores each (query, atom) pair through full attention. Cross-encoder discrimination is 42x better than any bi-encoder on this corpus.

**Why two stages:** Cross-encoder can't replace the bi-encoder because scoring all 1,342 atoms would take ~2s per query. Scoring 20 candidates takes ~50ms.

**Why the cross-encoder can't replace approach angles:** Without angles, the correct atoms often aren't in the top-20 ANN candidates at all (recall stays at 0.500). Angles fix first-stage recall; cross-encoder fixes second-stage precision.

### 3. BM25 atom rescue

Atoms were already indexed in FTS5 but explicitly excluded from retrieval (`_fts_query` had `AND e.type != 'atom'`). This was a legacy safety guard from early development. With corroboration scoring, confidence priors, and cross-encoder reranking, the exclusion was actively harmful.

Added `_fts_atom_query()` for keyword-matched atom rescue. Contributes +8 queries on the 135-query expanded benchmark.

### 4. Graph-sibling expansion + entity-overlap boosting

When an atom scores well, fetch entity-connected siblings via `atom_entities` junction. When query entities match atom entities, boost the atom's rank. Both use the existing entity graph (521 entities, 4,890 edges).

### 5. Extraction prompt rewrite (forward-only)

Rewrote `extract_atoms()` prompt to produce entity-rich, discriminative atoms instead of formulaic "The user prefers X" style. Validated: 80% entity density (was 17%), 0% "The user" prefix (was 66.8%). Forward-only — existing atoms keep old phrasing.

## Benchmark results

| Stage | Recall@5 (30q) | Recall@5 (135q) |
|-------|---------------|-----------------|
| Baseline (raw ANN only) | 0.500 | — |
| + angles + siblings + entity | 0.800 | — |
| + cross-encoder reranker | 0.867 | — |
| + threshold tuning (1.0 → 1.1) | 0.900 | — |
| + BM25 rescue + wider pool | 0.900 | 0.882 |

### Embedding model shootout

Tested embeddinggemma, snowflake-arctic-embed2, nomic-embed-text, bge-m3, mxbai-embed-large, and cross-encoder on the same 30 queries. Embeddinggemma is the best bi-encoder for this corpus. Cross-encoder discrimination is 42x better (separation 7.92 vs 0.17).

**Do not swap embedding models** without running the full shootout script (`scripts/embedding_shootout.py`).

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `DEUS_ATOM_ANGLES` | `1` | Enable approach angle retrieval |
| `DEUS_ATOM_ANGLE_MIN` | `1.1` | Max L2 distance for angle rescue |
| `DEUS_ATOM_ANGLE_ALPHA` | `0.0` | Blending weight (0 = pure angle score) |
| `DEUS_RERANKER` | `1` | Enable cross-encoder reranking |
| `DEUS_RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model |
| `DEUS_RERANKER_CANDIDATES` | `20` | First-stage candidate pool size |
| `DEUS_SIBLING_DISCOUNT` | `0.15` | Distance penalty for sibling atoms |
| `DEUS_SIBLING_MAX` | `5` | Max sibling atoms to add |
| `DEUS_ENTITY_BOOST` | `0.1` | Distance reduction per entity overlap |

## Post-merge setup

After merging, users must run:
```bash
python3 scripts/memory_indexer.py --backfill-atom-angles
```
This generates approach angles for all atoms (~45 min via Ollama). The cross-encoder model downloads automatically on first use (~80MB). BM25 rescue requires no action.

## Disproved approaches (during this investigation)

| Approach | Result | Why it failed |
|----------|--------|---------------|
| Symmetric task prefixes (embeddinggemma) | recall -0.133 | Model becomes too conservative |
| Expanding cross-encoder candidate pool beyond 10 | No improvement | Plateau at 10 candidates; misses are first-stage recall failures |
| snowflake-arctic-embed2 model swap | recall 0.933 (vs 1.000 embeddinggemma) | Worse on short personal knowledge descriptions |
| mxbai-embed-large model swap | recall 0.967 (vs 1.000) | Lower discrimination despite higher MTEB score |
