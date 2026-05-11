#!/bin/bash
# PreToolUse hook: blocks edits to test files during TDD GREEN phase.
# Only active when DEUS_TDD_PHASE=green is set.
# This prevents the implementation agent from modifying tests to make them pass.
set -e

[ "$DEUS_TDD_PHASE" != "green" ] && exit 0

INPUT=$(cat)
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')

case "$TOOL" in
  Edit|Write|MultiEdit|apply_patch) ;;
  *) exit 0 ;;
esac

FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')
[ -z "$FILE" ] && exit 0

BASENAME=$(basename "$FILE")

# Match common test file patterns
if echo "$BASENAME" | grep -qE '\.(test|spec)\.[a-z]+$|_test\.[a-z]+$|^test_'; then
  echo "[tdd-test-lock] BLOCKED — cannot edit test files during GREEN phase."
  echo ""
  echo "The test file '$BASENAME' is locked because DEUS_TDD_PHASE=green."
  echo "During GREEN phase, only source files can be modified."
  echo "The implementation must make the existing tests pass, not change them."
  echo ""
  echo "If you need to modify tests, switch back to RED phase:"
  echo "  export DEUS_TDD_PHASE=red"
  exit 2
fi

# Also match files under test/ or tests/ directories
if echo "$FILE" | grep -qE '/(tests?|__tests__|fixtures)/'; then
  echo "[tdd-test-lock] BLOCKED — cannot edit files in test directories during GREEN phase."
  echo ""
  echo "File: $FILE"
  echo "Switch to RED phase to modify test files: export DEUS_TDD_PHASE=red"
  exit 2
fi
