"""Tests for OllamaEmbeddingProvider retry-with-backoff logic."""
import json
import socket
import urllib.error
from unittest.mock import MagicMock, call, patch

import pytest

from evolution.providers.embeddings import OllamaEmbeddingProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMBED_DIM = 768  # must match config.EMBED_DIM
_FAKE_VEC = [0.1] * _EMBED_DIM


def _ok_response(vec: list[float] = _FAKE_VEC) -> MagicMock:
    """Build a context-manager mock that returns a valid embed response."""
    body = json.dumps({"embeddings": [vec]}).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Happy-path: no retry needed
# ---------------------------------------------------------------------------


def test_embed_success_no_retry():
    """Single successful call returns the vector without any retry."""
    provider = OllamaEmbeddingProvider(model="test-model", host="http://localhost:11434")
    with patch("urllib.request.urlopen", return_value=_ok_response()) as mock_open:
        result = provider.embed("hello")
    mock_open.assert_called_once()
    assert result == _FAKE_VEC


# ---------------------------------------------------------------------------
# Retry: TimeoutError twice then success
# ---------------------------------------------------------------------------


def test_embed_retries_on_timeout_error():
    """urlopen raises TimeoutError twice, then succeeds; embed returns the vector."""
    provider = OllamaEmbeddingProvider(model="test-model", host="http://localhost:11434")
    side_effects = [TimeoutError("timed out"), TimeoutError("timed out"), _ok_response()]

    with patch("urllib.request.urlopen", side_effect=side_effects) as mock_open, \
         patch("time.sleep") as mock_sleep:
        result = provider.embed("hello")

    assert mock_open.call_count == 3
    # Two sleeps: 1s after attempt 1, 2s after attempt 2
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list == [call(1.0), call(2.0)]
    assert result == _FAKE_VEC


def test_embed_retries_on_socket_timeout():
    """socket.timeout is also treated as a transient timeout."""
    provider = OllamaEmbeddingProvider(model="test-model", host="http://localhost:11434")
    side_effects = [socket.timeout("timed out"), _ok_response()]

    with patch("urllib.request.urlopen", side_effect=side_effects) as mock_open, \
         patch("time.sleep"):
        result = provider.embed("hello")

    assert mock_open.call_count == 2
    assert result == _FAKE_VEC


def test_embed_retries_on_urlerror_wrapping_timeout():
    """urllib.error.URLError wrapping a socket.timeout is treated as transient."""
    provider = OllamaEmbeddingProvider(model="test-model", host="http://localhost:11434")
    wrapped = urllib.error.URLError(reason=socket.timeout("timed out"))
    side_effects = [wrapped, _ok_response()]

    with patch("urllib.request.urlopen", side_effect=side_effects) as mock_open, \
         patch("time.sleep"):
        result = provider.embed("hello")

    assert mock_open.call_count == 2
    assert result == _FAKE_VEC


# ---------------------------------------------------------------------------
# Exhaust retries: final error is re-raised (fail-loud rule)
# ---------------------------------------------------------------------------


def test_embed_raises_after_all_retries_exhausted():
    """After MAX_ATTEMPTS timeouts the original exception propagates (fail-loud)."""
    provider = OllamaEmbeddingProvider(model="test-model", host="http://localhost:11434")
    exc = TimeoutError("timed out")
    side_effects = [exc, TimeoutError("timed out again"), TimeoutError("still timing out")]

    with patch("urllib.request.urlopen", side_effect=side_effects) as mock_open, \
         patch("time.sleep"):
        with pytest.raises(TimeoutError):
            provider.embed("hello")

    # All 3 attempts were made
    assert mock_open.call_count == 3


# ---------------------------------------------------------------------------
# Non-timeout errors are NOT retried
# ---------------------------------------------------------------------------


def test_embed_does_not_retry_on_http_error():
    """HTTP 500 is a hard failure — no retry, exception propagates immediately."""
    provider = OllamaEmbeddingProvider(model="test-model", host="http://localhost:11434")
    http_err = urllib.error.HTTPError(
        url="http://localhost:11434/api/embed",
        code=500,
        msg="Internal Server Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_err) as mock_open, \
         patch("time.sleep") as mock_sleep:
        with pytest.raises(urllib.error.HTTPError):
            provider.embed("hello")

    mock_open.assert_called_once()
    mock_sleep.assert_not_called()


def test_embed_does_not_retry_on_connection_refused():
    """A URLError whose reason is not a timeout is not retried."""
    provider = OllamaEmbeddingProvider(model="test-model", host="http://localhost:11434")
    conn_err = urllib.error.URLError(reason=ConnectionRefusedError("Connection refused"))

    with patch("urllib.request.urlopen", side_effect=conn_err) as mock_open, \
         patch("time.sleep") as mock_sleep:
        with pytest.raises(urllib.error.URLError):
            provider.embed("hello")

    mock_open.assert_called_once()
    mock_sleep.assert_not_called()
