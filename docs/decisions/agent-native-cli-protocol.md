# ADR: Agent-native CLI protocol for `scripts/*.py`

**Date:** 2026-05-16
**Status:** Accepted (dims 1-2 shipped); dims 3-5 Proposed
**Scope:** All Python CLIs in `scripts/` that may be invoked by an agent
(LLM shelling out via `subprocess`). Hooks, internal callers, and human
users on a terminal are unaffected by default.

## Context

Deus internal CLIs (`memory_tree.py`, `memory_indexer.py`, and follow-ons)
are increasingly called by LLM agents — both inside the chat loop (via the
`Bash` tool) and from hooks. A 2026-05-15 review of
[mvanhorn/cli-printing-press](https://github.com/mvanhorn/cli-printing-press)
distilled a five-dimension protocol that agent-native CLI tools tend to
implement:

1. **Typed exit codes** — distinct numeric codes per failure mode so the
   agent can decide between retry, escalate, and abort without parsing
   stderr.
2. **Auto-JSON on non-TTY** — when stdout is piped or redirected, the CLI
   emits machine-parseable JSON instead of human-readable text. Same
   behavior as `git`, `gh`, `kubectl`.
3. **`--compact`** — token-reduced output (60–80% fewer tokens than
   default), reducing context burn when agents shell out repeatedly.
4. **`--dry-run`** — preview without side effects.
5. **`--select <field>`** — output only the named field(s); jq-like field
   filtering baked into the CLI.

A subsequent audit (two parallel Explore agents over `memory_tree.py` and
`memory_indexer.py`) confirmed that **all five dimensions** are
under-implemented or absent. Today every Python CLI error path ends in
`sys.exit(1)`; conditional `--json` flags exist but no auto-detection; and
only `sync-atom-kinds`, `--prune`, `--decay`, and `--recent`'s `--compact`
support survive as partial implementations.

The friction this causes for shell-out agents:

- An agent that gets exit code 1 can't tell whether to retry (transient
  failure), escalate (auth or config), or abort (internal bug).
- Hooks consuming markdown output via regex are brittle — every
  human-readable rendering change is a hook break.
- Agents repeating queries burn context window on formatting they don't
  need.

A full implementation across all 27 subcommands in both CLIs is multi-PR
work. This ADR documents the protocol once, then ships v1 = dims 1 + 2.

## Decision

Adopt the five-dimension agent-native CLI protocol. Ship dims 1 + 2 in v1;
defer dims 3 + 4 + 5 to follow-up PRs.

### Dim 1 — Typed exit codes

The new exit codes are anchored to the existing 4-class error taxonomy
defined in [`error-discipline.md`](error-discipline.md) for the
TypeScript side. **We do not introduce a third vocabulary.**

| Python exit code | Symbolic constant | `error-discipline.md` class | Trigger |
|---|---|---|---|
| 0 | `EXIT_OK` | — | Success |
| 1 | `EXIT_GENERIC` | `DeusError` | **Existing** "soft failure" sites (abstain, `ok=false`) — unchanged for backward compat |
| 2 | `EXIT_USAGE` | `UserError` | Bad CLI args, missing required config, argparse default |
| 3 | `EXIT_NOT_FOUND` | `UserError` | File / path / atom not found |
| 4 | `EXIT_IO_ERROR` | `FatalError` | Permission denied, disk full, generic `OSError` |
| 5 | `EXIT_AUTH` | `FatalError` | Missing API key, OAuth failure |
| 7 | `EXIT_TRANSIENT` | `RetryableError` | 429, timeout, network blip |
| 10 | `EXIT_INTERNAL` | `DeusError` | Uncaught exception, assertion failure |
| 130 | `EXIT_INTERRUPTED` | — | POSIX SIGINT (Ctrl-C). NOT `EXIT_USAGE`. |

Constants live in `scripts/_agent_cli.py`. The helper `classify_exception()`
maps common exceptions to codes; top-level `main()` wrappers in each CLI
catch unhandled exceptions and route through `classify_exception` so the
typed code propagates to the shell.

**Conservative migration rule:** existing `sys.exit(1)` sites are LEFT
ALONE in v1. Only retype clear `sys.exit(2)` sites and add typed codes at
NEW sites with unambiguous semantics (file-not-found, vault config
missing, auth failure). The legacy soft-fail code 1 stays as
`EXIT_GENERIC` for backward compat with any caller relying on `$? == 1`.

**Known limitation (v1):** `OSError` is mapped uniformly to
`EXIT_IO_ERROR`. Network-flavored `OSError`s (`socket.timeout`,
`errno.ETIMEDOUT`, `errno.ECONNRESET`) are semantically `RetryableError`
per `error-discipline.md` and SHOULD map to `EXIT_TRANSIENT`. Callers
that need TRANSIENT semantics inspect `errno` themselves. A follow-up PR
can extend the helper with per-errno routing.

### Dim 2 — Auto-JSON on non-TTY (gated)

The helper `should_emit_json(explicit_json_flag: bool)` returns `True`
if either:

- caller passed `--json` explicitly, OR
- agent-native mode is enabled AND stdout is not a TTY (piped/redirected).

Interactive human terminal use stays human-readable even with the env
var set — interactive users are not surprised by JSON dumps.

#### Gating via `DEUS_AGENT_NATIVE_CLI=1`

Auto-JSON-on-non-TTY is a breaking change for any existing hook or script
that pipes these CLIs and expects markdown output. SessionStart's
auto-retrieved-memory hook is one such consumer. To avoid silently breaking
production behavior, v1 gates the auto-detection behind an opt-in env var:

```
DEUS_AGENT_NATIVE_CLI=1   →  auto-JSON on non-TTY is active
(unset / 0 / any other)   →  default human-readable on non-TTY (current behavior)
```

Default is OFF in v1. Hooks that don't set the var see no change.

#### Flip-to-default-on trigger

The default flips from opt-in to default-on after ALL FOUR of these
internal consumers have been audited and updated to consume JSON output:

- `~/.claude/hooks/memory-retrieval.sh` (calls `memory_tree.py query`)
- `~/.claude/hooks/precompact-memory.sh` (calls `memory_indexer.py --query`)
- `~/.claude/hooks/catchup-freshness.sh` (calls `memory_indexer.py --recent`)
- `scripts/codex_warden_hooks.py:858` (calls `memory_indexer.py --recent 3`
  for the freshness-check additional-context block — text-only consumer
  today, will receive JSON post-flip)

Note: `scripts/codex_warden_hooks.py:931` already passes `--json`
explicitly to `memory_tree.py query` — it's unaffected by the flip and
does NOT need to migrate.

That's a completable, measurable gate — a single follow-up PR per
consumer, then the flip.

#### Unconditional JSON emitters

Three subcommands in `memory_tree.py` emit JSON unconditionally with no
`--json` flag and no human-readable branch: `build`, `calibrate`,
`benchmark`. The `should_emit_json()` gate is NOT applied to these
sites — applying it would silence the output on TTY when the env var is
off. They stay always-JSON. Same applies to `memory_indexer.py`'s
`--atom-benchmark`.

### Dim 3 (`--compact`) — Proposed, deferred

Existing partial impl in `memory_indexer.py --recent` (auto-triggers at
`COMPACT_SESSION_THRESHOLD >= 12 sessions`, see
`memory_indexer.py:828`) stays as-is. Extending `--compact` to all
subcommands requires per-subcommand schema design (which fields to drop,
what % reduction target). Estimated effort: per-subcommand ~30 min plus
one design pass per CLI.

### Dim 4 (`--dry-run` extension) — Proposed, deferred

Existing impl: `sync-atom-kinds` (memory_tree.py), `--prune`, `--decay`
(memory_indexer.py). Extending to `--invalidate`, `--reenrich`,
`--promote`, `--compile`, `--extract` (memory_indexer) and `build`,
`reindex-external`, `backfill-angles`, `check --auto-fix`, `sync-fts-angles`
(memory_tree) requires per-subcommand mutation analysis — what's the side
effect, what's the preview format, what's the test. Each subcommand is
~15-30 min independent work.

### Dim 5 (`--select <field>`) — Proposed, deferred

Requires JSON schema definition for each subcommand. **Blocked on dim 2
production-ization** — `--select` can't filter fields from a markdown
blob. After the flip-to-default-on trigger fires, dim 5 becomes
unblocked.

## Implementation status (v1)

This PR ships:

- `scripts/_agent_cli.py` — shared module with exit constants, gate,
  classifier
- `scripts/memory_tree.py` — imports + 9 JSON-gate retrofits + 3 typed exit
  retypes + main() wrapper
- `scripts/memory_indexer.py` — imports + entry-point structural refactor
  (rename `main()` → `_main_impl()`, add typed wrapper, change `__main__`)
  + 4 typed exit retypes
- `scripts/tests/test_agent_cli.py` — 10 unit tests for the helpers
- This ADR + `INDEX.md` row

## Consequences

**Positive:**
- Agents calling Deus CLIs get a typed error vocabulary aligned with the
  TS side. Retry / escalate / abort decisions become deterministic.
- Future hooks can opt into JSON output by setting one env var, no script
  rewrite needed.
- The 5-dimension framework is named; future PRs that add dim 3 / 4 / 5
  have an explicit anchor.

**Negative:**
- Adds a shared `_agent_cli.py` module that both CLIs depend on. Same
  pattern as the existing `_time.py`, but it's a coupling.
- The `DEUS_AGENT_NATIVE_CLI` env var is a hidden behavior toggle. Users
  not aware of it may be surprised by JSON output once they set it.
- Some `sys.exit(1)` sites in both CLIs still represent semantically
  distinct errors (the audit found ~15 conflated sites). They're left
  alone in v1; agents querying those sites still see undifferentiated
  exit 1.

**Migration plan toward default-on:**
1. Audit each of the 3 production hooks; update to parse JSON. One PR per
   hook.
2. After all three ship and bake for ≥1 week, flip
   `agent_native_enabled()` to return True by default. The env var becomes
   `DEUS_AGENT_NATIVE_CLI=0` (opt-out for legacy callers that haven't
   migrated).
3. Once `=0` has zero observed callers for ≥1 month, drop the env var
   entirely.

## Cross-references

- [`error-discipline.md`](error-discipline.md) — TS-side error taxonomy
  this protocol maps onto.
- Session log: `Session-Logs/2026-05-15/cli-printing-press-review.md` in
  the vault — initial protocol identification + rationale for adopting
  patterns (not the Go tool itself).
