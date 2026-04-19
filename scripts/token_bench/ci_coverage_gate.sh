#!/usr/bin/env bash
# CLAUDE.md keyword-coverage gate for CI.
#
# Runs keyword_bench.py against any user-agnostic CLAUDE.md whose facts file
# also lives in this repo. Fails if critical-fact coverage drops below
# THRESHOLD on any changed file.
#
# Skips files that aren't touched by the diff — fast no-op on unrelated PRs.
#
# Usage:
#   scripts/token_bench/ci_coverage_gate.sh                      # diff vs origin/main
#   scripts/token_bench/ci_coverage_gate.sh origin/feature-x     # custom base ref
#   THRESHOLD=92 scripts/token_bench/ci_coverage_gate.sh         # custom floor
#   FORCE_ALL=1  scripts/token_bench/ci_coverage_gate.sh         # gate every pair regardless of diff
set -euo pipefail

BASE_REF="${1:-origin/main}"
THRESHOLD="${THRESHOLD:-90}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# File ↔ facts mapping. Add a row when a new user-agnostic CLAUDE.md grows
# a curated facts file under scripts/token_bench/facts/.
PAIRS=(
  "CLAUDE.md|scripts/token_bench/facts/root_claudemd.txt"
  "groups/global/CLAUDE.md.template|scripts/token_bench/facts/global_template.txt"
  "groups/main/CLAUDE.md.template|scripts/token_bench/facts/main_template.txt"
)

if [[ "${FORCE_ALL:-0}" = "1" ]]; then
  CHANGED=""  # treat every pair as changed
else
  if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    echo "Base ref '$BASE_REF' not found; fetching." >&2
    git fetch origin "${BASE_REF#origin/}" --depth=1 >/dev/null 2>&1 || true
  fi
  CHANGED="$(git diff --name-only "$BASE_REF"...HEAD || true)"
fi

failed=0
ran=0
for pair in "${PAIRS[@]}"; do
  doc="${pair%%|*}"
  facts="${pair##*|}"

  if [[ "${FORCE_ALL:-0}" != "1" ]]; then
    if ! grep -qx -e "$doc" -e "$facts" <<<"$CHANGED"; then
      continue
    fi
  fi

  ran=$((ran + 1))
  echo "::group::keyword_bench: $doc (threshold ${THRESHOLD}%)"
  if ! python3 scripts/token_bench/keyword_bench.py \
      --label "$(basename "$doc")" \
      --compressed "$doc" \
      --facts "$facts" \
      --threshold "$THRESHOLD"; then
    failed=$((failed + 1))
  fi
  echo "::endgroup::"
done

if [[ "$ran" -eq 0 ]]; then
  echo "No gated CLAUDE.md or facts file in this diff — skipping."
  exit 0
fi

if [[ "$failed" -gt 0 ]]; then
  echo
  echo "$failed file(s) below ${THRESHOLD}% critical coverage. Audit each MISS as paraphrase before merging."
  echo "If the MISS is a real omission, restore the missing rule. If it's preserved in paraphrase, add a 'kw=' override to the facts file."
  exit 1
fi

echo "All gated CLAUDE.md files preserve critical facts (≥ ${THRESHOLD}%)."
