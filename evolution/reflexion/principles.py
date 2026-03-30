"""
Lightweight "top principles" extraction.

Queries the best and worst scored interactions (optionally domain-filtered),
sends them to Gemini to extract 3-5 actionable principles, and stores each
as a reflection with category='principle'.

CLI-only for v1 — no automatic post-judge hook.
"""
import json
from typing import Optional

from ..config import GEN_MODELS, JUDGE_MODEL, load_api_key
from ..ilog.interaction_log import get_recent
from .store import save_reflection

_PRINCIPLES_PROMPT = """
You are analyzing a set of AI assistant interactions — some high-scoring and some low-scoring.
Extract 3-5 actionable principles that distinguish excellent responses from poor ones.

## High-scoring interactions (what works well):
{good_examples}

## Low-scoring interactions (what goes wrong):
{bad_examples}

## Instructions

Write exactly 3-5 principles. Each principle should be:
- One sentence, actionable, specific
- Something the assistant can apply to future interactions
- A pattern, not a one-time fix

Format each as a numbered list:
1. [principle]
2. [principle]
...

Keep the total under 200 words.
"""


def extract_principles(
    domain: Optional[str] = None,
    top_k: int = 5,
    model: str = JUDGE_MODEL,
) -> Optional[str]:
    """
    Extract top principles from the best and worst interactions.
    Stores each principle as a reflection with category='principle'.
    Returns the generated principles text, or None if insufficient data.
    """
    from google import genai

    # Get best and worst scored interactions
    best = get_recent(
        min_score=0.7, limit=top_k, eval_suite=None, domain=domain,
    )
    worst = get_recent(
        max_score=0.5, limit=top_k, eval_suite=None, domain=domain,
    )

    if len(best) + len(worst) < 3:
        return None

    def _format_examples(interactions: list[dict]) -> str:
        parts = []
        for i, ix in enumerate(interactions, 1):
            score = ix.get("judge_score", "?")
            parts.append(
                f"[{i}] Score: {score}\n"
                f"  Prompt: {ix['prompt'][:200]}\n"
                f"  Response: {(ix.get('response') or '')[:200]}"
            )
        return "\n\n".join(parts) if parts else "(none)"

    formatted = _PRINCIPLES_PROMPT.format(
        good_examples=_format_examples(best),
        bad_examples=_format_examples(worst),
    )

    client = genai.Client(api_key=load_api_key())
    models_to_try = [model] + [m for m in GEN_MODELS if m != model]
    text = ""
    last_exc = None

    for m in models_to_try:
        try:
            resp = client.models.generate_content(model=m, contents=formatted)
            text = resp.text.strip()
            break
        except Exception as exc:
            last_exc = exc
            if "429" in str(exc) or "quota" in str(exc).lower():
                continue
            raise

    if not text:
        raise RuntimeError(f"All Gemini models failed generating principles. Last: {last_exc}")

    # Store each principle as a separate reflection
    lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    stored = 0
    for line in lines:
        # Strip the number prefix (e.g., "1. " or "1) ")
        content = line.lstrip("0123456789.)- ").strip()
        if len(content) > 10:
            save_reflection(
                content=content,
                category="principle",
                score_at_gen=0.0,
                group_folder=None,  # Cross-group
            )
            stored += 1

    domain_label = f" (domain: {domain})" if domain else ""
    print(f"[principles] Extracted {stored} principles{domain_label}")
    return text
