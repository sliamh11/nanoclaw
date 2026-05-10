# ADR: Atom Fallback Evaluation

**Status:** Accepted  
**Date:** 2026-05-10  
**Scope:** scripts/memory_query.py  
**Supersedes:** None  
**Related:** embedding-model-selection.md, memory-tree.md, threshold-calibration-sweep.md

## Context

PR #350 added a two-stage atom fallback: when the memory tree abstains, query
1,229 atoms from `memory_indexer` as a second pass. Atoms within an L2
distance threshold (`DEUS_ATOM_DIST`, default 1.2) are returned as context.
The threshold was shipped as a starting heuristic, pending calibration.

## Evaluation

Tested all 18 queries where the tree abstains (13 correct, 5 wrong) against
the atom corpus. Results at threshold 1.2:

- **13/13 correct abstains** would be wrongly rescued with irrelevant atoms
  (e.g., "fix a leaking faucet" → "Always uses /frontend-design skill")
- **Only 2-3 of 5 wrong abstains** had genuinely matching atoms
- Net impact: actively harmful — injects garbage context more often than it
  helps

Threshold sweep across 0.85-1.20:

| Threshold | Correct Rescues | Wrong Rescues | Net |
|-----------|----------------|---------------|-----|
| 0.95      | 1              | 0             | +1  |
| 1.00      | 1              | 0             | +1  |
| 1.05      | 2              | 1             | 0   |
| 1.10      | 4              | 6             | -3  |
| 1.20      | 4              | 13            | -10 |

Root cause: `embeddinggemma` compresses atom-length text (single sentences)
into L2 distances of ~1.0-1.2 regardless of relevance. The same compression
was observed with `nomic-embed-text` during the embedding model comparison.

## Decision

Disable atom fallback by default (`DEUS_ATOM_DIST=0`). The feature remains
available via env var for future re-evaluation if:

- A better embedding model with wider atom-level score separation is adopted
- Atom-specific approach angles are added to improve distance discrimination
- A reranker or LLM judge replaces distance-based gating

Atoms remain useful in two other contexts:
1. **Explicit search** via `deus-memory` MCP tool (LLM judges text relevance)
2. **Session catch-up** via `memory_indexer --recent`

## Constraints

- Do not remove the atom fallback code — it's correct and will be useful with
  better embeddings
- The `DEUS_ATOM_DIST` env var stays as the activation mechanism
- Any re-enablement must pass a benchmark evaluation showing positive net
  benefit before shipping a non-zero default
