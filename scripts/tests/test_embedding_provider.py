"""Tests for the embedding provider selection and defaults."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _reset_provider():
    """Reset the module-level singleton so each test starts clean."""
    import evolution.providers.embeddings as mod
    mod._provider = None


@pytest.fixture(autouse=True)
def _clean_provider():
    _reset_provider()
    yield
    _reset_provider()


class TestDefaults:
    """Verify default model and auto-selection priority."""

    def test_default_ollama_embed_model_is_embeddinggemma(self):
        from evolution.providers.embeddings import OLLAMA_EMBED_MODEL
        assert OLLAMA_EMBED_MODEL == "embeddinggemma"

    def test_auto_prefers_ollama_when_available(self):
        from evolution.providers.embeddings import (
            OllamaEmbeddingProvider,
            get_embedding_provider,
        )
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "auto"}, clear=False):
            with patch(
                "evolution.providers.embeddings._is_ollama_available", return_value=True
            ):
                _reset_provider()
                provider = get_embedding_provider()
                assert isinstance(provider, OllamaEmbeddingProvider)

    def test_auto_falls_back_to_gemini_when_ollama_unavailable(self):
        from evolution.providers.embeddings import (
            GeminiEmbeddingProvider,
            get_embedding_provider,
        )
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "auto"}, clear=False):
            with patch(
                "evolution.providers.embeddings._is_ollama_available", return_value=False
            ):
                mock_client = MagicMock()
                with patch("google.genai.Client", return_value=mock_client):
                    _reset_provider()
                    provider = get_embedding_provider()
                    assert isinstance(provider, GeminiEmbeddingProvider)

    def test_auto_raises_when_nothing_available(self):
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "auto"}, clear=False):
            with patch(
                "evolution.providers.embeddings._is_ollama_available", return_value=False
            ):
                with patch(
                    "evolution.providers.embeddings.GeminiEmbeddingProvider",
                    side_effect=RuntimeError("no key"),
                ):
                    _reset_provider()
                    with pytest.raises(RuntimeError, match="No embedding provider"):
                        from evolution.providers.embeddings import get_embedding_provider
                        get_embedding_provider()

    def test_explicit_ollama_backend(self):
        from evolution.providers.embeddings import (
            OllamaEmbeddingProvider,
            get_embedding_provider,
        )
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "ollama"}, clear=False):
            _reset_provider()
            provider = get_embedding_provider()
            assert isinstance(provider, OllamaEmbeddingProvider)

    def test_explicit_gemini_backend(self):
        from evolution.providers.embeddings import (
            GeminiEmbeddingProvider,
            get_embedding_provider,
        )
        mock_client = MagicMock()
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "gemini"}, clear=False):
            with patch("google.genai.Client", return_value=mock_client):
                _reset_provider()
                provider = get_embedding_provider()
                assert isinstance(provider, GeminiEmbeddingProvider)


class TestEmbedMode:
    """Test EmbedMode prefix injection."""

    @staticmethod
    def _mock_http_response(body: bytes):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = body
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        return mock_conn

    @staticmethod
    def _provider_and_conn(n_vecs=1):
        from evolution.providers.embeddings import OllamaEmbeddingProvider
        from evolution.config import EMBED_DIM
        vecs = [[0.1] * EMBED_DIM for _ in range(n_vecs)]
        body = json.dumps({"embeddings": vecs}).encode()
        provider = OllamaEmbeddingProvider(model="test")
        mock_conn = TestEmbedMode._mock_http_response(body)
        return provider, mock_conn

    def test_query_mode_prepends_prefix(self, monkeypatch):
        import evolution.providers.embeddings as mod
        from evolution.providers.embeddings import EmbedMode, QUERY_PREFIX
        monkeypatch.setattr(mod, "_PREFIXES_ENABLED", True)
        provider, mock_conn = self._provider_and_conn()
        with patch.object(provider, "_get_conn", return_value=mock_conn):
            provider.embed("hello", mode=EmbedMode.QUERY)
        call_body = json.loads(mock_conn.request.call_args[1]["body"])
        assert call_body["input"] == [QUERY_PREFIX + "hello"]

    def test_document_mode_prepends_prefix(self, monkeypatch):
        import evolution.providers.embeddings as mod
        from evolution.providers.embeddings import EmbedMode, DOCUMENT_PREFIX
        monkeypatch.setattr(mod, "_PREFIXES_ENABLED", True)
        provider, mock_conn = self._provider_and_conn()
        with patch.object(provider, "_get_conn", return_value=mock_conn):
            provider.embed("hello", mode=EmbedMode.DOCUMENT)
        call_body = json.loads(mock_conn.request.call_args[1]["body"])
        assert call_body["input"] == [DOCUMENT_PREFIX + "hello"]

    def test_raw_mode_no_prefix(self, monkeypatch):
        import evolution.providers.embeddings as mod
        from evolution.providers.embeddings import EmbedMode
        monkeypatch.setattr(mod, "_PREFIXES_ENABLED", True)
        provider, mock_conn = self._provider_and_conn()
        with patch.object(provider, "_get_conn", return_value=mock_conn):
            provider.embed("hello", mode=EmbedMode.RAW)
        call_body = json.loads(mock_conn.request.call_args[1]["body"])
        assert call_body["input"] == ["hello"]

    def test_env_var_disables_prefix(self, monkeypatch):
        import evolution.providers.embeddings as mod
        from evolution.providers.embeddings import EmbedMode
        monkeypatch.setattr(mod, "_PREFIXES_ENABLED", False)
        provider, mock_conn = self._provider_and_conn()
        with patch.object(provider, "_get_conn", return_value=mock_conn):
            provider.embed("hello", mode=EmbedMode.QUERY)
        call_body = json.loads(mock_conn.request.call_args[1]["body"])
        assert call_body["input"] == ["hello"]

    def test_batch_respects_mode(self, monkeypatch):
        import evolution.providers.embeddings as mod
        from evolution.providers.embeddings import EmbedMode, DOCUMENT_PREFIX
        monkeypatch.setattr(mod, "_PREFIXES_ENABLED", True)
        provider, mock_conn = self._provider_and_conn(n_vecs=2)
        with patch.object(provider, "_get_conn", return_value=mock_conn):
            provider.embed_batch(["a", "b"], mode=EmbedMode.DOCUMENT)
        call_body = json.loads(mock_conn.request.call_args[1]["body"])
        assert call_body["input"] == [DOCUMENT_PREFIX + "a", DOCUMENT_PREFIX + "b"]


class TestOllamaEmbeddingProvider:
    """Test OllamaEmbeddingProvider vector handling."""

    @staticmethod
    def _mock_http_response(body: bytes):
        """Create a mock HTTPConnection whose getresponse() returns body."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = body
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        return mock_conn

    def test_truncates_long_vectors(self):
        from evolution.providers.embeddings import OllamaEmbeddingProvider
        from evolution.config import EMBED_DIM

        long_vec = list(range(EMBED_DIM + 100))
        fake_response = json.dumps({"embeddings": [long_vec]}).encode()

        provider = OllamaEmbeddingProvider(model="test")
        mock_conn = self._mock_http_response(fake_response)
        with patch.object(provider, "_get_conn", return_value=mock_conn):
            result = provider.embed("test")
            assert len(result) == EMBED_DIM

    def test_pads_short_vectors(self):
        from evolution.providers.embeddings import OllamaEmbeddingProvider
        from evolution.config import EMBED_DIM

        short_vec = [1.0] * 10
        fake_response = json.dumps({"embeddings": [short_vec]}).encode()

        provider = OllamaEmbeddingProvider(model="test")
        mock_conn = self._mock_http_response(fake_response)
        with patch.object(provider, "_get_conn", return_value=mock_conn):
            result = provider.embed("test")
            assert len(result) == EMBED_DIM
            assert result[:10] == [1.0] * 10
            assert all(v == 0.0 for v in result[10:])
