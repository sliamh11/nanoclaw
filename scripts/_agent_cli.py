"""Agent-native CLI helpers for ``scripts/*.py`` — typed exit codes + auto-JSON
output detection.

This module exists so agents (Claude, Codex, etc.) that shell out to Deus
internal CLIs get machine-readable error semantics and machine-readable
output by default. See ``docs/decisions/agent-native-cli-protocol.md`` for
the full 5-dimension protocol; this module ships dimensions 1 (typed exit
codes) and 2 (auto-JSON on non-TTY) in v1.

The exit-code values are anchored to the 4-class error taxonomy already
defined in ``docs/decisions/error-discipline.md`` for the TypeScript side —
this module does NOT introduce a third vocabulary. The mapping table is
authoritative in the ADR.

  Python exit code     Symbolic              error-discipline.md class
  ----------------     --------              -------------------------
  0                    EXIT_OK               (success)
  1                    EXIT_GENERIC          DeusError (legacy abstain/soft fail)
  2                    EXIT_USAGE            UserError
  3                    EXIT_NOT_FOUND        UserError
  4                    EXIT_IO_ERROR         FatalError
  5                    EXIT_AUTH             FatalError
  7                    EXIT_TRANSIENT        RetryableError
  10                   EXIT_INTERNAL         DeusError

Picking the wrong code is silent — an agent that retries on 1 will spin on
permanent failures; an agent that doesn't retry on 7 will give up on
transient ones. Always pick from the table explicitly; never invent new
non-zero codes ad-hoc.
"""
from __future__ import annotations

import os
import sys
from typing import TextIO

# === Typed exit codes ===
EXIT_OK: int = 0
EXIT_GENERIC: int = 1
EXIT_USAGE: int = 2
EXIT_NOT_FOUND: int = 3
EXIT_IO_ERROR: int = 4
EXIT_AUTH: int = 5
EXIT_TRANSIENT: int = 7
EXIT_INTERNAL: int = 10

# POSIX SIGINT convention. NOT EXIT_USAGE — Ctrl-C is not a usage error.
# See main() wrappers in memory_tree.py / memory_indexer.py.
EXIT_INTERRUPTED: int = 130


# === Auto-JSON gate ===
_ENV_FLAG = "DEUS_AGENT_NATIVE_CLI"


def agent_native_enabled() -> bool:
    """True iff ``DEUS_AGENT_NATIVE_CLI=1`` in env.

    Default off in v1 — the gate flips to default-on only after all three
    production hooks (``memory-retrieval.sh``, ``precompact-memory.sh``,
    ``catchup-freshness.sh``) consume JSON output. Concrete trigger in ADR.
    """
    return os.environ.get(_ENV_FLAG) == "1"


def should_emit_json(explicit_json_flag: bool, *, stdout: TextIO | None = None) -> bool:
    """Decide whether to emit JSON for this output site.

    JSON output if EITHER:
      (a) caller passed ``--json`` explicitly (``args.json=True``), OR
      (b) agent-native enabled (``DEUS_AGENT_NATIVE_CLI=1``) AND stdout is
          NOT a TTY (i.e., piped / redirected).

    Human terminal use stays human-readable even with the env var set —
    interactive users aren't surprised by JSON dumps. The opt-in is for
    agents that shell out and pipe, not for ergonomic shell sessions.
    """
    if explicit_json_flag:
        return True
    if not agent_native_enabled():
        return False
    stream = stdout if stdout is not None else sys.stdout
    # `isatty` may be missing on exotic streams; treat absence as "not a TTY".
    isatty = getattr(stream, "isatty", None)
    if isatty is None:
        return True
    try:
        return not isatty()
    except (OSError, ValueError):
        # Stream may be closed mid-call; safe default is human-readable.
        return False


# === Exception → exit code helper ===
def classify_exception(exc: BaseException) -> int:
    """Map common exceptions to typed exit codes per ``error-discipline.md``.

    KNOWN LIMITATION (v1): ``OSError`` is mapped to ``EXIT_IO_ERROR``
    uniformly. Network-flavored ``OSError``s (``socket.timeout``,
    ``errno.ETIMEDOUT``, ``errno.ECONNRESET``) are semantically
    ``RetryableError`` per ``error-discipline.md`` and SHOULD map to
    ``EXIT_TRANSIENT``. Callers that need TRANSIENT semantics must inspect
    ``errno`` themselves. A follow-up PR can extend this helper with
    per-errno routing; v1 keeps the helper simple.

    ``KeyboardInterrupt`` is not specially mapped here — if you pass one
    through, it falls through to ``EXIT_INTERNAL``. Callers should catch
    ``KeyboardInterrupt`` at ``main()`` level FIRST and return
    ``EXIT_INTERRUPTED`` (130, POSIX SIGINT convention); only delegate to
    this helper for non-SIGINT exceptions.
    """
    if isinstance(exc, FileNotFoundError):
        return EXIT_NOT_FOUND
    if isinstance(exc, PermissionError):
        return EXIT_IO_ERROR
    if isinstance(exc, OSError):
        return EXIT_IO_ERROR  # see KNOWN LIMITATION above
    return EXIT_INTERNAL
