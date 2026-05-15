"""Unit tests for ``scripts/_agent_cli.py``.

Covers the 3 helpers: ``agent_native_enabled``, ``should_emit_json``,
``classify_exception``. Live CLI invocations are exercised separately
(see verification section of the plan); these are pure-logic tests.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest.mock
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_AGENT_CLI_PATH = _SCRIPTS_DIR / "_agent_cli.py"


def _load_agent_cli():
    """Load _agent_cli.py without polluting sys.modules across tests."""
    spec = importlib.util.spec_from_file_location("_agent_cli", _AGENT_CLI_PATH)
    assert spec and spec.loader, f"cannot load spec from {_AGENT_CLI_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_agent_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def acli():
    return _load_agent_cli()


class _FakeStream:
    """Minimal stdout-like with explicit isatty control."""

    def __init__(self, *, isatty: bool):
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


# ---------------------------------------------------------------------------
# should_emit_json — the 4-quadrant behavior matrix
# ---------------------------------------------------------------------------


def test_should_emit_json_explicit_flag_wins(acli):
    """args.json=True → JSON regardless of TTY or env."""
    # Even with env off and a TTY, explicit --json wins.
    with unittest.mock.patch.dict(os.environ, {}, clear=True):
        assert acli.should_emit_json(True, stdout=_FakeStream(isatty=True)) is True
        assert acli.should_emit_json(True, stdout=_FakeStream(isatty=False)) is True
    # Same with env on.
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "1"}):
        assert acli.should_emit_json(True, stdout=_FakeStream(isatty=True)) is True


def test_should_emit_json_env_off_returns_false_on_non_tty(acli):
    """Default behavior: env unset, pipe → human-readable (no breakage)."""
    with unittest.mock.patch.dict(os.environ, {}, clear=True):
        assert acli.should_emit_json(False, stdout=_FakeStream(isatty=False)) is False
        # Also confirm a wrong env value (e.g., "0", "true", "yes") stays off.
        with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "0"}):
            assert acli.should_emit_json(False, stdout=_FakeStream(isatty=False)) is False
        with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "true"}):
            assert acli.should_emit_json(False, stdout=_FakeStream(isatty=False)) is False


def test_should_emit_json_env_on_non_tty_returns_true(acli):
    """Opt-in works: env=1 + piped stdout → JSON."""
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "1"}):
        assert acli.should_emit_json(False, stdout=_FakeStream(isatty=False)) is True


def test_should_emit_json_env_on_tty_returns_false(acli):
    """Humans aren't surprised: env=1 + TTY → still human-readable."""
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "1"}):
        assert acli.should_emit_json(False, stdout=_FakeStream(isatty=True)) is False


def test_should_emit_json_missing_isatty_treats_as_non_tty(acli):
    """Streams without isatty() (rare, e.g., wrapped pipes) → treat as non-TTY."""
    class NoIsatty:
        pass
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "1"}):
        assert acli.should_emit_json(False, stdout=NoIsatty()) is True


def test_should_emit_json_closed_stream_returns_false(acli):
    """If isatty() raises (stream closed mid-call) → safe default human-readable."""
    class ClosedStream:
        def isatty(self) -> bool:
            raise OSError("stream closed")
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "1"}):
        # Even with env on, an unreadable stream falls back to human-readable.
        # This is the "safe default" path inside the except block.
        assert acli.should_emit_json(False, stdout=ClosedStream()) is False


# ---------------------------------------------------------------------------
# classify_exception — known-types mapping
# ---------------------------------------------------------------------------


def test_classify_exception_maps_known_types(acli):
    """FileNotFoundError→3, PermissionError→4, OSError→4, RuntimeError→10."""
    assert acli.classify_exception(FileNotFoundError("x")) == acli.EXIT_NOT_FOUND
    assert acli.classify_exception(PermissionError("x")) == acli.EXIT_IO_ERROR
    assert acli.classify_exception(OSError("x")) == acli.EXIT_IO_ERROR
    assert acli.classify_exception(RuntimeError("x")) == acli.EXIT_INTERNAL


def test_classify_exception_default_internal(acli):
    """Generic Exception → 10 (no special handling)."""
    assert acli.classify_exception(Exception("generic")) == acli.EXIT_INTERNAL
    assert acli.classify_exception(ValueError("oops")) == acli.EXIT_INTERNAL
    assert acli.classify_exception(KeyError("missing")) == acli.EXIT_INTERNAL


def test_classify_exception_does_not_handle_keyboard_interrupt(acli):
    """KeyboardInterrupt falls through to EXIT_INTERNAL.

    Intentional non-mapping: SIGINT is caught at main() level FIRST and
    returns EXIT_INTERRUPTED (130, POSIX). If this helper specially mapped
    it, callers using only this helper (no main() wrapper) would get a
    typed code instead — undesirable. This test pins the contract so future
    refactors don't accidentally add the mapping.
    """
    # KeyboardInterrupt inherits from BaseException, not Exception.
    # classify_exception accepts BaseException so it WILL be invoked if
    # callers pass it, but it should fall through to EXIT_INTERNAL.
    assert acli.classify_exception(KeyboardInterrupt()) == acli.EXIT_INTERNAL


# ---------------------------------------------------------------------------
# agent_native_enabled — env var parsing
# ---------------------------------------------------------------------------


def test_agent_native_enabled_strict_value(acli):
    """Only the exact string '1' enables; '0', 'true', 'yes' do not."""
    with unittest.mock.patch.dict(os.environ, {}, clear=True):
        assert acli.agent_native_enabled() is False
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "1"}):
        assert acli.agent_native_enabled() is True
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "0"}):
        assert acli.agent_native_enabled() is False
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": "true"}):
        assert acli.agent_native_enabled() is False
    with unittest.mock.patch.dict(os.environ, {"DEUS_AGENT_NATIVE_CLI": ""}):
        assert acli.agent_native_enabled() is False


# ---------------------------------------------------------------------------
# Exit-code constant invariants
# ---------------------------------------------------------------------------


def test_exit_codes_are_distinct_integers(acli):
    """All EXIT_* constants are distinct ints — no accidental aliasing."""
    codes = {
        acli.EXIT_OK,
        acli.EXIT_GENERIC,
        acli.EXIT_USAGE,
        acli.EXIT_NOT_FOUND,
        acli.EXIT_IO_ERROR,
        acli.EXIT_AUTH,
        acli.EXIT_TRANSIENT,
        acli.EXIT_INTERNAL,
        acli.EXIT_INTERRUPTED,
    }
    assert len(codes) == 9, f"expected 9 distinct codes, got {len(codes)}: {codes}"
    # POSIX convention: SIGINT is 130.
    assert acli.EXIT_INTERRUPTED == 130
