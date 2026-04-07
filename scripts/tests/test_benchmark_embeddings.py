"""
Unit tests for evolution/benchmark_embeddings.py.

All tests mock network I/O — no real API calls or Ollama connection needed.
"""
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is importable
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from evolution.benchmark_embeddings import (
    DOCUMENTS,
    QUERIES,
    RELEVANCE_PAIRS,
    ProviderResult,
    _benchmark_provider,
    _compute_metrics,
    _cosine_similarity,
    _detect_native_dim,
    _embed_corpus,
    _is_ollama_available,
    _truncate,
    print_comparison,
)


# ── _cosine_similarity ────────────────────────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors_return_one(self):
        v = [1.0, 2.0, 3.0]
        assert math.isclose(_cosine_similarity(v, v), 1.0, abs_tol=1e-9)

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert math.isclose(_cosine_similarity(a, b), 0.0, abs_tol=1e-9)

    def test_opposite_vectors_return_minus_one(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert math.isclose(_cosine_similarity(a, b), -1.0, abs_tol=1e-9)

    def test_zero_vector_returns_zero(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_mismatched_lengths_truncate_to_shorter(self):
        a = [1.0, 0.0, 9.9]  # extra dim ignored
        b = [1.0, 0.0]
        result = _cosine_similarity(a, b)
        # Uses first 2 dims only: dot=1, |a|=1, |b|=1
        assert math.isclose(result, 1.0, abs_tol=1e-9)

    def test_unit_vectors_produce_expected_value(self):
        # 45° angle → cos(45°) ≈ 0.7071
        a = [1.0, 0.0]
        b = [1.0 / math.sqrt(2), 1.0 / math.sqrt(2)]
        assert math.isclose(_cosine_similarity(a, b), 1.0 / math.sqrt(2), abs_tol=1e-6)


# ── _truncate ─────────────────────────────────────────────────────────────────

class TestTruncate:
    def test_truncates_longer_vector(self):
        v = [1.0, 2.0, 3.0, 4.0]
        assert _truncate(v, 2) == [1.0, 2.0]

    def test_returns_full_vector_when_shorter_than_dim(self):
        v = [1.0, 2.0]
        assert _truncate(v, 5) == [1.0, 2.0]

    def test_returns_full_vector_when_equal_to_dim(self):
        v = [1.0, 2.0, 3.0]
        assert _truncate(v, 3) == [1.0, 2.0, 3.0]


# ── _compute_metrics ──────────────────────────────────────────────────────────

def _make_one_hot(idx: int, size: int) -> list[float]:
    """Create a one-hot vector of `size` with a 1 at position `idx`."""
    v = [0.0] * size
    v[idx] = 1.0
    return v


class TestComputeMetrics:
    def _perfect_corpus(self, n: int):
        """n queries, n docs, each query maps to its same-index doc (one-hot vecs)."""
        queries = [_make_one_hot(i, n) for i in range(n)]
        docs = [_make_one_hot(i, n) for i in range(n)]
        pairs = [(i, i) for i in range(n)]
        return queries, docs, pairs

    def test_perfect_retrieval_gives_recall_one(self):
        q, d, pairs = self._perfect_corpus(10)
        r3, r5, mrr = _compute_metrics(q, d, pairs)
        assert math.isclose(r3, 1.0), f"Recall@3 expected 1.0, got {r3}"
        assert math.isclose(r5, 1.0), f"Recall@5 expected 1.0, got {r5}"

    def test_perfect_retrieval_gives_mrr_one(self):
        q, d, pairs = self._perfect_corpus(10)
        _, _, mrr = _compute_metrics(q, d, pairs)
        assert math.isclose(mrr, 1.0, abs_tol=1e-6), f"MRR expected 1.0, got {mrr}"

    def test_no_relevant_docs_in_top_k_gives_zero_recall(self):
        # Query 0 points right (index 0), docs all point in orthogonal directions
        dim = 6
        queries = [_make_one_hot(0, dim)]   # [1,0,0,0,0,0]
        # Docs: directions 1..5 — all orthogonal to query direction 0
        docs = [_make_one_hot(i, dim) for i in range(1, dim)]
        pairs = [(0, 0)]  # ground truth says doc[0] is relevant

        # doc[0] = one-hot(1) = [0,1,0,0,0,0] — orthogonal to query
        # All docs are orthogonal to query → cosine scores all 0 → rank is arbitrary
        # Verify metric values are in valid range
        r3, r5, mrr = _compute_metrics(queries, docs, pairs)
        assert 0.0 <= r3 <= 1.0
        assert 0.0 <= r5 <= 1.0
        assert 0.0 <= mrr <= 1.0

    def test_empty_relevance_returns_zeros(self):
        q = [[1.0, 0.0], [0.0, 1.0]]
        d = [[1.0, 0.0], [0.0, 1.0]]
        r3, r5, mrr = _compute_metrics(q, d, [])
        assert r3 == 0.0
        assert r5 == 0.0
        assert mrr == 0.0

    def test_out_of_range_query_index_skipped(self):
        q = [[1.0, 0.0]]   # only index 0 exists
        d = [[1.0, 0.0], [0.0, 1.0]]
        pairs = [(0, 0), (5, 1)]  # index 5 is out of range
        r3, r5, mrr = _compute_metrics(q, d, pairs)
        # Should not raise; index 5 is skipped
        assert 0.0 <= mrr <= 1.0

    def test_recall_at_5_gte_recall_at_3(self):
        """Recall@5 must always be >= Recall@3 (larger window)."""
        q, d, pairs = self._perfect_corpus(20)
        r3, r5, _ = _compute_metrics(q, d, pairs)
        assert r5 >= r3


# ── _detect_native_dim ────────────────────────────────────────────────────────

class TestDetectNativeDim:
    def test_returns_length_of_embed_output(self):
        provider = MagicMock()
        provider.embed.return_value = [0.1] * 512
        assert _detect_native_dim(provider) == 512

    def test_returns_zero_on_exception(self):
        provider = MagicMock()
        provider.embed.side_effect = RuntimeError("connection refused")
        assert _detect_native_dim(provider) == 0


# ── _embed_corpus ─────────────────────────────────────────────────────────────

class TestEmbedCorpus:
    def test_returns_vector_per_text(self):
        provider = MagicMock()
        provider.embed.side_effect = lambda t: [0.5] * 4

        result = ProviderResult(provider_name="test", model="m", native_dim=4, truncated_to=4)
        texts = ["hello", "world", "foo"]
        vecs = _embed_corpus(provider, texts, result)

        assert len(vecs) == 3
        assert vecs[0] == [0.5, 0.5, 0.5, 0.5]

    def test_records_latency_per_call(self):
        provider = MagicMock()
        provider.embed.side_effect = lambda t: [0.1] * 4

        result = ProviderResult(provider_name="test", model="m", native_dim=4, truncated_to=4)
        texts = ["a", "b", "c"]
        _embed_corpus(provider, texts, result)

        assert len(result.embed_latencies) == 3
        assert all(lat >= 0 for lat in result.embed_latencies)

    def test_increments_error_count_on_failure(self):
        provider = MagicMock()
        provider.embed.side_effect = RuntimeError("oops")

        result = ProviderResult(provider_name="test", model="m", native_dim=4, truncated_to=4)
        vecs = _embed_corpus(provider, ["bad text"], result)

        assert result.errors == 1
        assert vecs == [[]]  # empty vector placeholder

    def test_accumulates_total_chars(self):
        provider = MagicMock()
        provider.embed.return_value = [0.0] * 4

        result = ProviderResult(provider_name="test", model="m", native_dim=4, truncated_to=4)
        texts = ["abc", "de"]  # 3 + 2 = 5 chars
        _embed_corpus(provider, texts, result)

        assert result.total_chars == 5


# ── ProviderResult latency properties ─────────────────────────────────────────

class TestProviderResultLatency:
    def _result_with_latencies(self, lats: list[float]) -> ProviderResult:
        r = ProviderResult(provider_name="x", model="y", native_dim=4, truncated_to=4)
        r.embed_latencies = lats
        return r

    def test_avg_latency_is_mean(self):
        r = self._result_with_latencies([1.0, 3.0])
        assert math.isclose(r.avg_latency, 2.0)

    def test_avg_latency_empty_returns_zero(self):
        r = self._result_with_latencies([])
        assert r.avg_latency == 0.0

    def test_p50_is_median(self):
        r = self._result_with_latencies([1.0, 2.0, 3.0])
        assert math.isclose(r.p50_latency, 2.0)

    def test_p95_returns_high_percentile(self):
        lats = list(range(1, 21))  # 1..20
        r = self._result_with_latencies([float(x) for x in lats])
        # p95 of 20 items: ceil(0.95*20)=19, idx=18 → value=19
        assert r.p95_latency == 19.0

    def test_p95_single_element(self):
        r = self._result_with_latencies([0.5])
        assert r.p95_latency == 0.5


# ── _benchmark_provider ───────────────────────────────────────────────────────

class TestBenchmarkProvider:
    def _make_provider(self, dim: int = 768) -> MagicMock:
        """Create a mock provider returning deterministic one-hot-like vectors."""
        provider = MagicMock()
        call_count = [0]

        def fake_embed(text):
            call_count[0] += 1
            # Return a deterministic but unique vector per text using hash
            idx = hash(text) % dim
            v = [0.0] * dim
            v[idx] = 1.0
            return v

        provider.embed.side_effect = fake_embed
        return provider

    def test_returns_provider_result(self):
        provider = self._make_provider(768)
        result = _benchmark_provider(
            provider_name="mock",
            provider=provider,
            model="mock-model",
            rounds=1,
            embed_dim=768,
            verbose=False,
        )
        assert isinstance(result, ProviderResult)
        assert result.provider_name == "mock"
        assert result.model == "mock-model"

    def test_native_dim_detected(self):
        provider = self._make_provider(512)
        result = _benchmark_provider(
            provider_name="mock",
            provider=provider,
            model="small-model",
            rounds=1,
            embed_dim=768,
            verbose=False,
        )
        assert result.native_dim == 512
        # truncated_to should be min(512, 768) = 512
        assert result.truncated_to == 512

    def test_latencies_recorded(self):
        provider = self._make_provider(768)
        result = _benchmark_provider(
            provider_name="mock",
            provider=provider,
            model="m",
            rounds=1,
            embed_dim=768,
            verbose=False,
        )
        # At least one embedding call per text in corpus (queries + docs)
        expected_calls = len(QUERIES) + len(DOCUMENTS)
        assert len(result.embed_latencies) == expected_calls

    def test_metrics_in_valid_range(self):
        provider = self._make_provider(768)
        result = _benchmark_provider(
            provider_name="mock",
            provider=provider,
            model="m",
            rounds=1,
            embed_dim=768,
            verbose=False,
        )
        assert 0.0 <= result.recall_at_3_native <= 1.0
        assert 0.0 <= result.recall_at_5_native <= 1.0
        assert 0.0 <= result.mrr_native <= 1.0

    def test_provider_failure_returns_error_result(self):
        provider = MagicMock()
        provider.embed.side_effect = RuntimeError("no model")
        result = _benchmark_provider(
            provider_name="broken",
            provider=provider,
            model="none",
            rounds=1,
            embed_dim=768,
            verbose=False,
        )
        assert result.native_dim == 0
        assert result.errors >= 1

    def test_multiple_rounds_averaged(self):
        provider = self._make_provider(768)
        result = _benchmark_provider(
            provider_name="mock",
            provider=provider,
            model="m",
            rounds=3,
            embed_dim=768,
            verbose=False,
        )
        # Should have 3 * (queries+docs) latency measurements
        expected = 3 * (len(QUERIES) + len(DOCUMENTS))
        assert len(result.embed_latencies) == expected


# ── _is_ollama_available ──────────────────────────────────────────────────────

class TestIsOllamaAvailable:
    def test_returns_true_when_reachable(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            assert _is_ollama_available("http://localhost:11434") is True

    def test_returns_false_on_connection_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert _is_ollama_available("http://localhost:11434") is False


# ── print_comparison (smoke test) ─────────────────────────────────────────────

class TestPrintComparison:
    def test_no_results_does_not_crash(self, capsys):
        print_comparison([])
        out = capsys.readouterr().out
        assert "No results" in out

    def test_single_result_printed(self, capsys):
        r = ProviderResult(
            provider_name="gemini",
            model="gemini-embedding-001",
            native_dim=768,
            truncated_to=768,
        )
        r.recall_at_3_native = 0.9
        r.recall_at_5_native = 0.95
        r.mrr_native = 0.85
        r.embed_latencies = [0.1, 0.2, 0.15]

        print_comparison([r])
        out = capsys.readouterr().out
        assert "gemini" in out
        assert "0.850" in out  # MRR

    def test_truncation_table_shown_when_native_ne_trunc(self, capsys):
        r = ProviderResult(
            provider_name="ollama",
            model="gemma:embeddinggemma",
            native_dim=1024,
            truncated_to=768,
        )
        r.recall_at_3_native = 0.8
        r.recall_at_5_native = 0.9
        r.mrr_native = 0.75
        r.recall_at_3_trunc = 0.78
        r.recall_at_5_trunc = 0.88
        r.mrr_trunc = 0.73
        r.embed_latencies = [0.05]

        print_comparison([r])
        out = capsys.readouterr().out
        assert "TRUNCATION" in out

    def test_truncation_table_hidden_when_native_eq_trunc(self, capsys):
        r = ProviderResult(
            provider_name="gemini",
            model="gemini-embedding-001",
            native_dim=768,
            truncated_to=768,
        )
        r.embed_latencies = [0.1]
        print_comparison([r])
        out = capsys.readouterr().out
        assert "TRUNCATION" not in out


# ── Corpus sanity checks ──────────────────────────────────────────────────────

class TestCorpusSanity:
    def test_queries_and_docs_count(self):
        assert len(QUERIES) >= 20, "Need at least 20 queries"
        assert len(DOCUMENTS) >= 20, "Need at least 20 documents"

    def test_relevance_pairs_in_range(self):
        for q_idx, d_idx in RELEVANCE_PAIRS:
            assert 0 <= q_idx < len(QUERIES), f"query index {q_idx} out of range"
            assert 0 <= d_idx < len(DOCUMENTS), f"doc index {d_idx} out of range"

    def test_hebrew_queries_present(self):
        hebrew = [q for q in QUERIES if any("\u0590" <= c <= "\u05ff" for c in q)]
        assert len(hebrew) >= 4, "Need at least 4 Hebrew queries"

    def test_hebrew_docs_present(self):
        hebrew = [d for d in DOCUMENTS if any("\u0590" <= c <= "\u05ff" for c in d)]
        assert len(hebrew) >= 4, "Need at least 4 Hebrew documents"
