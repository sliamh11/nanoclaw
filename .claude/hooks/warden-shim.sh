#!/bin/bash
# Thin shim for Claude Code hooks → scripts/codex_warden_hooks.py.
# macOS/Linux only; Windows users: use `codex_warden_hooks.py install`.
set -e
exec python3 "${CLAUDE_PROJECT_DIR:-.}/scripts/codex_warden_hooks.py" run "$@" \
  --repo-root "${CLAUDE_PROJECT_DIR:-.}"
