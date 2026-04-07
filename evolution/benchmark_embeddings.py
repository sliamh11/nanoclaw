"""
Benchmark EmbeddingGemma (Ollama) against Gemini Embedding API.

Measures latency, retrieval accuracy (Recall@3, Recall@5, MRR), and dimension
handling for both providers. Includes a diverse test corpus with Hebrew text
pairs to test multilingual capability.

Usage:
    python3 -m evolution.benchmark_embeddings [--provider gemini|ollama|all] [--model MODEL] [--rounds N]
"""
import argparse
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# ── Test corpus ───────────────────────────────────────────────────────────────

# ~20 diverse queries spanning engineering, study, personal, marketing + Hebrew
QUERIES: list[str] = [
    # Engineering
    "How do I fix a memory leak in a Node.js application?",
    "What is the difference between TCP and UDP protocols?",
    "Explain the CAP theorem in distributed systems",
    "How does a binary search tree self-balance?",
    "Best practices for securing REST API endpoints",
    # Study
    "What are the key principles of spaced repetition learning?",
    "How does the Pomodoro technique improve focus?",
    "Explain the concept of retrieval practice in education",
    # Personal / life
    "How to build a consistent morning routine",
    "Tips for managing work-life balance when working from home",
    "Best strategies for tracking personal finances",
    # Marketing
    "How to write a compelling product launch announcement",
    "Strategies for growing an early-stage startup's user base",
    "How to measure the success of a content marketing campaign",
    # Hebrew — engineering
    "כיצד ניתן לאבטח API מפני התקפות injection?",
    "מה ההבדל בין process ו-thread בתכנות?",
    # Hebrew — study
    "מהי שיטת למידה בפיזור זמן ולמה היא יעילה?",
    "כיצד הזיכרון לטווח ארוך שונה מהזיכרון לטווח קצר?",
    # Hebrew — personal
    "איך לפתח הרגלים חדשים ולהיצמד אליהם לאורך זמן?",
    "מהן הדרכים הטובות ביותר להתמודד עם לחץ ועומס עבודה?",
]

# ~20 document texts that should be retrieved by some queries above
DOCUMENTS: list[str] = [
    # Engineering docs
    "Memory leaks in Node.js can be diagnosed with heap snapshots. Common causes include closures holding references, event listeners not removed, and global caches growing unbounded.",
    "TCP provides reliable, ordered delivery with handshaking. UDP is connectionless and faster but with no delivery guarantees — useful for video streaming or DNS.",
    "CAP theorem states a distributed system can guarantee at most two of: Consistency, Availability, Partition tolerance. Most distributed DBs sacrifice consistency under partition.",
    "AVL trees and Red-Black trees maintain balance after insertions/deletions via rotations, ensuring O(log n) search, insert, and delete operations.",
    "Secure REST APIs with HTTPS/TLS, JWT authentication, rate limiting, input validation, and OWASP-aligned CORS policies to prevent injection and CSRF attacks.",
    # Study docs
    "Spaced repetition schedules reviews at increasing intervals based on memory decay curves (Ebbinghaus). Tools like Anki implement this to maximize long-term retention.",
    "The Pomodoro Technique uses 25-minute focused work intervals followed by 5-minute breaks, reducing mental fatigue and improving sustained concentration.",
    "Retrieval practice (testing effect) shows that recalling information from memory strengthens it far more than re-reading. Active recall beats passive review.",
    # Personal docs
    "Building a morning routine: anchor it to an existing habit, start small (5 minutes), track streaks, and gradually add components over weeks.",
    "Work-life balance while remote: set fixed work hours, create a dedicated workspace, use a shutdown ritual, and communicate boundaries with your team.",
    "Personal finance tracking with zero-based budgeting: assign every dollar a job, review weekly, use apps like YNAB or a simple spreadsheet.",
    # Marketing docs
    "A product launch post should open with the pain point, show the solution concisely, include social proof, and end with a single clear call-to-action.",
    "Early-stage startup growth: focus on one acquisition channel, talk to users weekly, optimize onboarding to reduce drop-off, and build word-of-mouth loops.",
    "Content marketing metrics: track organic traffic, time-on-page, lead conversions, and keyword rankings weekly. Tie each piece to a stage of the funnel.",
    # Hebrew engineering docs
    "אבטחת API מפני SQL injection ו-XSS: יש לאמת קלט, להשתמש ב-prepared statements, ולהחזיר שגיאות גנריות בלבד.",
    "תהליך (process) הוא יחידת הפעלה עצמאית עם זיכרון נפרד. חוט (thread) משתף את זיכרון ה-process שלו עם חוטים אחרים.",
    # Hebrew study docs
    "למידה בפיזור זמן מבוססת על עקומת שכחה של אביינגהאוס: תזמון חזרות במרווחים גדלים מאפשר שמירה ארוכת-טווח עם פחות זמן לימוד.",
    "הזיכרון לטווח קצר (STM) מחזיק 7±2 פריטים לכמה שניות, בעוד הזיכרון לטווח ארוך (LTM) הוא כמעט בלתי מוגבל ומתקבע דרך שינה וחזרה.",
    # Hebrew personal docs
    "בניית הרגלים חדשים לפי ג'יימס קליר: זיהוי cue, שגרה קטנה וברורה, תגמול מיידי, ושרשור הרגלים (habit stacking) כדי להיצמד אליהם.",
    "התמודדות עם עומס: תעדוף לפי מטריצת אייזנהאואר (דחוף/חשוב), פריקת משימות ל-task list, ומנוחות קצרות בין בלוקי עבודה.",
]

# Ground-truth relevance: list of (query_idx, doc_idx) pairs
# Each query should ideally retrieve its semantically matching document
RELEVANCE_PAIRS: list[tuple[int, int]] = [
    (0, 0),   # Node.js memory leak → Node.js memory leak doc
    (1, 1),   # TCP vs UDP → TCP/UDP doc
    (2, 2),   # CAP theorem → CAP theorem doc
    (3, 3),   # Binary search tree balance → AVL/RB tree doc
    (4, 4),   # Secure REST API → REST API security doc
    (5, 5),   # Spaced repetition → Spaced repetition doc
    (6, 6),   # Pomodoro → Pomodoro doc
    (7, 7),   # Retrieval practice → retrieval practice doc
    (8, 8),   # Morning routine → morning routine doc
    (9, 9),   # Work-life balance → work-life balance doc
    (10, 10), # Personal finance → personal finance doc
    (11, 11), # Product launch → product launch doc
    (12, 12), # Startup growth → startup growth doc
    (13, 13), # Content marketing metrics → content marketing doc
    (14, 14), # Hebrew: API security → Hebrew API security doc
    (15, 15), # Hebrew: process vs thread → Hebrew process/thread doc
    (16, 16), # Hebrew: spaced repetition → Hebrew spaced repetition doc
    (17, 17), # Hebrew: memory types → Hebrew memory types doc
    (18, 18), # Hebrew: habits → Hebrew habits doc
    (19, 19), # Hebrew: stress/workload → Hebrew stress doc
]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ProviderResult:
    provider_name: str
    model: str
    native_dim: int
    truncated_to: int  # EMBED_DIM (768) or native_dim if smaller
    # Latency tracking (seconds per embed call)
    embed_latencies: list[float] = field(default_factory=list)
    # Retrieval metrics (native dim)
    recall_at_3_native: float = 0.0
    recall_at_5_native: float = 0.0
    mrr_native: float = 0.0
    # Retrieval metrics (truncated to 768, or same if dim <= 768)
    recall_at_3_trunc: float = 0.0
    recall_at_5_trunc: float = 0.0
    mrr_trunc: float = 0.0
    # Token/char stats
    total_chars: int = 0
    errors: int = 0

    @property
    def avg_latency(self) -> float:
        return statistics.mean(self.embed_latencies) if self.embed_latencies else 0.0

    @property
    def p50_latency(self) -> float:
        if not self.embed_latencies:
            return 0.0
        return statistics.median(self.embed_latencies)

    @property
    def p95_latency(self) -> float:
        if len(self.embed_latencies) < 2:
            return self.embed_latencies[0] if self.embed_latencies else 0.0
        idx = int(math.ceil(0.95 * len(self.embed_latencies))) - 1
        return sorted(self.embed_latencies)[idx]


# ── Cosine similarity ─────────────────────────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        min_len = min(len(a), len(b))
        a, b = a[:min_len], b[:min_len]
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _truncate(vec: list[float], dim: int) -> list[float]:
    """Truncate a vector to `dim` dimensions."""
    return vec[:dim] if len(vec) > dim else vec


# ── Retrieval metrics ─────────────────────────────────────────────────────────

def _compute_metrics(
    query_vecs: list[list[float]],
    doc_vecs: list[list[float]],
    relevance: list[tuple[int, int]],
) -> tuple[float, float, float]:
    """
    Compute Recall@3, Recall@5, and MRR for a set of query/doc vectors.

    Args:
        query_vecs: Embedded queries.
        doc_vecs: Embedded documents.
        relevance: Ground-truth (query_idx, doc_idx) pairs.

    Returns:
        (recall_at_3, recall_at_5, mrr)
    """
    # Build ground truth map: query_idx → set of relevant doc indices
    gt: dict[int, set[int]] = {}
    for q_idx, d_idx in relevance:
        gt.setdefault(q_idx, set()).add(d_idx)

    recall_3_hits = 0
    recall_5_hits = 0
    reciprocal_ranks: list[float] = 0
    reciprocal_ranks = []

    for q_idx, relevant_docs in gt.items():
        if q_idx >= len(query_vecs):
            continue
        q_vec = query_vecs[q_idx]

        # Score all documents
        scores = [
            (_cosine_similarity(q_vec, doc_vecs[d_idx]), d_idx)
            for d_idx in range(len(doc_vecs))
        ]
        scores.sort(key=lambda x: x[0], reverse=True)
        ranked_doc_ids = [d_idx for _, d_idx in scores]

        # Recall@3
        if any(d in relevant_docs for d in ranked_doc_ids[:3]):
            recall_3_hits += 1

        # Recall@5
        if any(d in relevant_docs for d in ranked_doc_ids[:5]):
            recall_5_hits += 1

        # MRR: first relevant rank
        for rank, d_id in enumerate(ranked_doc_ids, start=1):
            if d_id in relevant_docs:
                reciprocal_ranks.append(1.0 / rank)
                break
        else:
            reciprocal_ranks.append(0.0)

    n = len(gt)
    if n == 0:
        return 0.0, 0.0, 0.0

    recall_3 = recall_3_hits / n
    recall_5 = recall_5_hits / n
    mrr = statistics.mean(reciprocal_ranks) if reciprocal_ranks else 0.0
    return recall_3, recall_5, mrr


# ── Provider embedding helpers ────────────────────────────────────────────────

def _embed_corpus(
    provider,
    texts: list[str],
    result: ProviderResult,
    native_dim: Optional[int] = None,
) -> list[list[float]]:
    """
    Embed all texts using the provider, recording latencies and errors.

    Returns the raw (native-dim) vectors. If native_dim is provided and differs
    from provider output, truncation is applied in the caller.
    """
    vecs = []
    for text in texts:
        result.total_chars += len(text)
        t0 = time.monotonic()
        try:
            vec = provider.embed(text)
            elapsed = time.monotonic() - t0
            result.embed_latencies.append(elapsed)
            vecs.append(vec)
        except Exception as exc:
            result.errors += 1
            print(f"  [warn] embed error: {exc}", file=sys.stderr)
            vecs.append([])
    return vecs


def _detect_native_dim(provider, sample_text: str = "test") -> int:
    """Detect the native output dimension of a provider."""
    try:
        vec = provider.embed(sample_text)
        return len(vec)
    except Exception:
        return 0


# ── Benchmark runner ──────────────────────────────────────────────────────────

def _benchmark_provider(
    provider_name: str,
    provider,
    model: str,
    rounds: int = 1,
    embed_dim: int = 768,
    verbose: bool = True,
) -> ProviderResult:
    """Run the full benchmark for one provider."""
    if verbose:
        print(f"\n{'='*60}")
        print(f"Benchmarking: {provider_name} ({model})")
        print(f"{'='*60}")

    native_dim = _detect_native_dim(provider)
    if native_dim == 0:
        print(f"  ERROR: Could not get embedding from provider. Skipping.", file=sys.stderr)
        return ProviderResult(
            provider_name=provider_name,
            model=model,
            native_dim=0,
            truncated_to=0,
            errors=1,
        )

    trunc_dim = min(native_dim, embed_dim)
    result = ProviderResult(
        provider_name=provider_name,
        model=model,
        native_dim=native_dim,
        truncated_to=trunc_dim,
    )

    if verbose:
        print(f"  Native dim: {native_dim} | Truncated to: {trunc_dim}")

    all_texts = QUERIES + DOCUMENTS
    n_queries = len(QUERIES)

    # Accumulate metrics over multiple rounds
    r3_native_acc: list[float] = []
    r5_native_acc: list[float] = []
    mrr_native_acc: list[float] = []
    r3_trunc_acc: list[float] = []
    r5_trunc_acc: list[float] = []
    mrr_trunc_acc: list[float] = []

    for r in range(rounds):
        if verbose and rounds > 1:
            print(f"  Round {r+1}/{rounds}...")

        raw_vecs = _embed_corpus(provider, all_texts, result)

        query_vecs_native = raw_vecs[:n_queries]
        doc_vecs_native = raw_vecs[n_queries:]

        # Native dim metrics
        r3, r5, mrr = _compute_metrics(query_vecs_native, doc_vecs_native, RELEVANCE_PAIRS)
        r3_native_acc.append(r3)
        r5_native_acc.append(r5)
        mrr_native_acc.append(mrr)

        # Truncated-to-768 metrics (only differs if native_dim > embed_dim)
        if native_dim != trunc_dim:
            query_vecs_trunc = [_truncate(v, trunc_dim) for v in query_vecs_native]
            doc_vecs_trunc = [_truncate(v, trunc_dim) for v in doc_vecs_native]
            r3t, r5t, mrrt = _compute_metrics(query_vecs_trunc, doc_vecs_trunc, RELEVANCE_PAIRS)
        else:
            r3t, r5t, mrrt = r3, r5, mrr
        r3_trunc_acc.append(r3t)
        r5_trunc_acc.append(r5t)
        mrr_trunc_acc.append(mrrt)

    result.recall_at_3_native = statistics.mean(r3_native_acc)
    result.recall_at_5_native = statistics.mean(r5_native_acc)
    result.mrr_native = statistics.mean(mrr_native_acc)
    result.recall_at_3_trunc = statistics.mean(r3_trunc_acc)
    result.recall_at_5_trunc = statistics.mean(r5_trunc_acc)
    result.mrr_trunc = statistics.mean(mrr_trunc_acc)

    if verbose:
        print(f"  Recall@3 (native): {result.recall_at_3_native:.3f}")
        print(f"  Recall@5 (native): {result.recall_at_5_native:.3f}")
        print(f"  MRR (native):      {result.mrr_native:.3f}")
        if native_dim != trunc_dim:
            print(f"  Recall@3 (trunc):  {result.recall_at_3_trunc:.3f}")
            print(f"  Recall@5 (trunc):  {result.recall_at_5_trunc:.3f}")
            print(f"  MRR (trunc):       {result.mrr_trunc:.3f}")
        print(f"  Avg latency: {result.avg_latency:.3f}s  "
              f"p50: {result.p50_latency:.3f}s  "
              f"p95: {result.p95_latency:.3f}s")
        print(f"  Errors: {result.errors}")

    return result


# ── Output table ──────────────────────────────────────────────────────────────

def print_comparison(results: list[ProviderResult], embed_dim: int = 768) -> None:
    """Print a formatted comparison table of all benchmark results."""
    if not results:
        print("\nNo results to compare.")
        return

    # Sort by MRR (native dim) descending
    ranked = sorted(results, key=lambda r: r.mrr_native, reverse=True)

    print(f"\n{'='*100}")
    print("EMBEDDING BENCHMARK RESULTS")
    print(f"{'='*100}")
    print(
        f"{'Provider':<22} {'Model':<30} {'Dim':>6} {'Trunc':>6} "
        f"{'R@3':>6} {'R@5':>6} {'MRR':>6} "
        f"{'Avg(s)':>7} {'p50(s)':>7} {'p95(s)':>7} {'Errors':>7}"
    )
    print("-" * 100)

    for r in ranked:
        marker = " ***" if r is ranked[0] else ""
        print(
            f"{r.provider_name:<22} {r.model:<30} {r.native_dim:>6} {r.truncated_to:>6} "
            f"{r.recall_at_3_native:>6.3f} {r.recall_at_5_native:>6.3f} {r.mrr_native:>6.3f} "
            f"{r.avg_latency:>7.3f} {r.p50_latency:>7.3f} {r.p95_latency:>7.3f} "
            f"{r.errors:>7}{marker}"
        )

    # Truncation impact table (only for providers where it matters)
    trunc_affected = [r for r in ranked if r.native_dim != r.truncated_to]
    if trunc_affected:
        print(f"\n{'='*70}")
        print(f"TRUNCATION IMPACT (native → {embed_dim}d)")
        print(f"{'='*70}")
        print(f"{'Provider':<22} {'Model':<30} {'MRR native':>10} {'MRR trunc':>10} {'Delta':>8}")
        print("-" * 70)
        for r in trunc_affected:
            delta = r.mrr_trunc - r.mrr_native
            sign = "+" if delta >= 0 else ""
            print(
                f"{r.provider_name:<22} {r.model:<30} "
                f"{r.mrr_native:>10.3f} {r.mrr_trunc:>10.3f} {sign}{delta:>7.3f}"
            )

    if ranked:
        print(f"\n*** Best by MRR: {ranked[0].provider_name} / {ranked[0].model}")
        print(f"\n  To apply: export EMBEDDING_PROVIDER={'gemini' if 'gemini' in ranked[0].provider_name.lower() else 'ollama'}")
        if 'ollama' in ranked[0].provider_name.lower():
            print(f"            export OLLAMA_EMBED_MODEL={ranked[0].model}")


# ── Ollama availability check ─────────────────────────────────────────────────

def _is_ollama_available(host: str = "http://localhost:11434") -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark embedding providers: Gemini API vs Ollama/EmbeddingGemma"
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "ollama", "all"],
        default="all",
        help="Which provider(s) to benchmark (default: all)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the model name for the selected provider",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Number of benchmark rounds to average over (default: 1)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only show final comparison table, suppress per-item output",
    )
    parser.add_argument(
        "--embed-dim",
        type=int,
        default=768,
        help="Target embedding dimension for truncation comparison (default: 768)",
    )
    args = parser.parse_args()

    verbose = not args.quiet

    from .providers.embeddings import GeminiEmbeddingProvider, OllamaEmbeddingProvider
    from .config import EMBED_MODELS, OLLAMA_HOST

    results: list[ProviderResult] = []

    # ── Gemini provider ────────────────────────────────────────────────────
    if args.provider in ("gemini", "all"):
        gemini_model = args.model if args.model and args.provider == "gemini" else EMBED_MODELS[0]
        if verbose:
            print(f"\nInitializing Gemini embedding provider (model: {gemini_model})...")
        try:
            gemini_provider = GeminiEmbeddingProvider()
            result = _benchmark_provider(
                provider_name="gemini",
                provider=gemini_provider,
                model=gemini_model,
                rounds=args.rounds,
                embed_dim=args.embed_dim,
                verbose=verbose,
            )
            results.append(result)
        except Exception as exc:
            print(f"ERROR: Could not initialize Gemini provider: {exc}", file=sys.stderr)
            if args.provider == "gemini":
                sys.exit(1)

    # ── Ollama provider ────────────────────────────────────────────────────
    if args.provider in ("ollama", "all"):
        import os
        ollama_model = (
            args.model if args.model and args.provider == "ollama"
            else os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        )

        if not _is_ollama_available(OLLAMA_HOST):
            msg = f"ERROR: Ollama is not reachable at {OLLAMA_HOST}. Start it with: ollama serve"
            print(msg, file=sys.stderr)
            if args.provider == "ollama":
                sys.exit(1)
            print("  Skipping Ollama benchmark.", file=sys.stderr)
        else:
            if verbose:
                print(f"\nInitializing Ollama embedding provider (model: {ollama_model})...")
            try:
                ollama_provider = OllamaEmbeddingProvider(model=ollama_model)
                result = _benchmark_provider(
                    provider_name="ollama",
                    provider=ollama_provider,
                    model=ollama_model,
                    rounds=args.rounds,
                    embed_dim=args.embed_dim,
                    verbose=verbose,
                )
                results.append(result)
            except Exception as exc:
                print(f"ERROR: Ollama benchmark failed: {exc}", file=sys.stderr)
                if args.provider == "ollama":
                    sys.exit(1)

    print_comparison(results, embed_dim=args.embed_dim)


if __name__ == "__main__":
    main()
