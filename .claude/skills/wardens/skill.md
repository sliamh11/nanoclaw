---
name: wardens
description: View, toggle, and configure wardens — the quality gates that review plans, code, and security
user_invocable: true
---

Run `python3 scripts/wardens.py` with any arguments the user passed after `/wardens`.

For `customize <name>`, don't launch `claude -p` — you're already in a session. Instead, read the current `custom_instructions` from `.claude/wardens/config.json` for the named warden, ask the user what behavior they want to customize, help them write it, then save it back to `config.json`.

Show the raw terminal output to the user — don't reformat it.
