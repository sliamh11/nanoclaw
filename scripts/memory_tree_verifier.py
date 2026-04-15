"""Second-stage verifier for memory-tree retrieval (Phase 8).

Takes stage-1 candidates (cosine top-k) and asks a local Ollama model
whether each candidate document actually answers the query. Returns
per-candidate labels: yes / partial / no / unknown.

Design goals:
  - O(k) per query, independent of corpus size N
  - Zero API cost (runs against local Ollama)
  - Fails open — if Ollama is unreachable or malformed, return every
    candidate as 'unknown' so the caller's original ranking stands
  - Single batched prompt per query, not k separate calls
  - Pure: no DB coupling. Caller supplies the candidate text.

See docs/research/memory-tree-verifier-plan.md for motivation and
predicted metric impact.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable


DEFAULT_VERIFIER_MODEL = "gemma4:e2b"
DEFAULT_VERIFIER_URL = "http://localhost:11434/api/generate"
VERIFIER_TIMEOUT_S = 15.0
MAX_TEXT_CHARS_PER_CANDIDATE = 500

# Keep the prompt compact — the verifier only needs the question and a
# title+description excerpt per candidate. Longer context hurts e2b latency
# without improving accuracy on this task.
_PROMPT_TEMPLATE = """\
You are a relevance judge. For each CANDIDATE below, decide whether it \
answers the QUESTION. Respond with exactly one line per candidate in the \
format:

  PATH|LABEL|REASON

LABEL is one of:
  - yes     : directly answers the question
  - partial : relevant topic but does not fully answer
  - no      : off-topic or does not contain the answer

QUESTION:
{query}

CANDIDATES:
{candidates_block}

Output ONLY the PATH|LABEL|REASON lines. No preamble, no markdown."""


class VerifierUnreachable(Exception):
    """Raised when the Ollama endpoint cannot be reached or times out."""


def verify_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    model: str = DEFAULT_VERIFIER_MODEL,
    ollama_url: str = DEFAULT_VERIFIER_URL,
    timeout: float = VERIFIER_TIMEOUT_S,
    transport: Callable[[str, dict[str, Any], float], str] | None = None,
) -> list[dict[str, Any]]:
    """Ask the verifier which candidates answer the query.

    Each candidate must be a dict with at least {"path": str, "text": str}.
    Additional keys (id, score, etc.) are preserved on output.

    Returns a list parallel to input with added keys:
      - label: "yes" | "partial" | "no" | "unknown"
      - reason: str

    `transport` is an injection point for tests: given
    (ollama_url, payload, timeout) → response_text. The default uses
    urllib to hit Ollama's /api/generate endpoint.

    Raises VerifierUnreachable if the endpoint is unreachable. Callers
    in production should catch and fall through to the unverified ranking.
    """
    if not candidates:
        return []

    prompt = _PROMPT_TEMPLATE.format(
        query=query.strip(),
        candidates_block=_format_candidates(candidates),
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # gemma4 family has "thinking" capability; without think=false the model
        # spends its budget on hidden reasoning and returns an empty response.
        "think": False,
        "options": {"temperature": 0.0, "num_predict": 256},
    }
    try:
        raw = (transport or _http_transport)(ollama_url, payload, timeout)
    except VerifierUnreachable:
        raise
    except Exception as exc:
        raise VerifierUnreachable(f"verifier transport failed: {exc}") from exc

    labels = _parse_response(raw, [c["path"] for c in candidates])
    out: list[dict[str, Any]] = []
    for cand in candidates:
        entry = dict(cand)
        parsed = labels.get(cand["path"], {"label": "unknown", "reason": "no response"})
        entry["label"] = parsed["label"]
        entry["reason"] = parsed["reason"]
        out.append(entry)
    return out


def _format_candidates(candidates: Iterable[dict[str, Any]]) -> str:
    blocks = []
    for cand in candidates:
        text = (cand.get("text") or "").strip().replace("\n", " ")
        if len(text) > MAX_TEXT_CHARS_PER_CANDIDATE:
            text = text[:MAX_TEXT_CHARS_PER_CANDIDATE].rstrip() + "…"
        blocks.append(f"- PATH: {cand['path']}\n  TEXT: {text}")
    return "\n".join(blocks)


_LINE_RE = re.compile(r"^\s*([^\s|][^|]*?)\s*\|\s*(\w+)\s*\|\s*(.*?)\s*$")
_VALID_LABELS = {"yes", "partial", "no"}


def _parse_response(text: str, known_paths: list[str]) -> dict[str, dict[str, str]]:
    """Parse `PATH|LABEL|REASON` lines. Unknown labels → 'unknown'.

    Only records paths that appeared in the input — the verifier occasionally
    hallucinates extra lines with invented paths. Those are ignored.
    """
    known = set(known_paths)
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        path, label, reason = m.group(1), m.group(2).lower(), m.group(3)
        if path not in known:
            continue
        if label not in _VALID_LABELS:
            label = "unknown"
        out[path] = {"label": label, "reason": reason[:200]}
    return out


def _http_transport(url: str, payload: dict[str, Any], timeout: float) -> str:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VerifierUnreachable(str(exc)) from exc
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as exc:
        raise VerifierUnreachable(f"malformed JSON from Ollama: {exc}") from exc
    return obj.get("response", "")


def rerank_by_verifier(
    ranked: list[tuple[Any, ...]],
    labeled: list[dict[str, Any]],
) -> tuple[list[tuple[Any, ...]], list[str]]:
    """Filter ranked candidates by verifier labels.

    `ranked` is the tuple-of-tuples shape retrieve() uses internally:
      (id, path, title, score, route)
    `labeled` is the output of verify_candidates.

    Drops tuples whose path was labelled 'no'. Keeps 'yes', 'partial',
    'unknown'. Returns (filtered_ranked, dropped_paths).
    """
    by_path = {e["path"]: e["label"] for e in labeled}
    kept = []
    dropped: list[str] = []
    for row in ranked:
        path = row[1]
        label = by_path.get(path, "unknown")
        if label == "no":
            dropped.append(path)
            continue
        kept.append(row)
    return kept, dropped
