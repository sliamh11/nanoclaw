# ADR: Model-Agnostic Hook Dispatch System

**Status:** Accepted
**Date:** 2026-05-14
**Scope:** `src/message-orchestrator.ts`, `.claude/hooks/`, `scripts/codex_warden_hooks.py`
**Wardens consulted:** Brainstormer (2 rounds), Architecture Snapshot, Threat Modeler (BLOCK), Plan Agent, Advisor

## Context

Deus's 15+ hooks (quality gates, memory retrieval, safety checks, context injection) only work with Claude Code sessions. When users run Deus with OpenAI/Codex backend, local models, or any future backend, zero hooks fire. The hooks need to become model-agnostic.

## Decision

Implement a Bridge-pattern hook system where Deus owns the hook contract and backends are dumb executors. Two layers with different trust models.

## Architecture

### The Two Layers

**Layer 1 (Host-Enforced, in orchestrator):**
- Fires in `message-orchestrator.ts` before/after container execution
- Events: `SessionStart`, `UserPromptSubmit`, `Stop`
- Container CANNOT opt out -- hooks run on the host before the container exists
- ALL security-critical hooks live here
- The host decides whether the container turn proceeds

**Layer 2 (Container-Cooperative, in agent loop):**
- Fires inside the container around tool execution
- Events: `PreToolUse` (observational + context), `PostToolUse` (telemetry)
- Container calls the HookDispatchService via HTTP bridge
- NOT for security enforcement -- a compromised container could skip the call
- Used for: memory retrieval, tool-size logging, tool audit, context injection
- `PreToolUse` CAN return `updatedInput` (rewrite tool args) but NOT `deny`

**Why this split:** The threat model identified an authority inversion -- if containers initiate security hook dispatch, a compromised container can skip it. Layer 1 eliminates this by making security decisions host-initiated. Layer 2 remains useful for non-security hooks where the container cooperates in good faith.

### Discriminated HookResult

```typescript
// Layer 1 results: can deny operations
interface Layer1HookResult {
  continue: boolean;           // false = block the operation
  stopReason?: string;
  additionalContext?: string;  // injected into the prompt
}

// Layer 2 results: context and observation only
interface Layer2HookResult {
  additionalContext?: string;
  updatedInput?: Record<string, unknown>;  // rewrite tool args
}
```

No `permissionDecision` field anywhere. Layer 1 uses `continue=false` to deny. Layer 2 cannot deny. This eliminates the anti-pattern of "symmetric permission enforcement across backends."

### HookDispatchService (:3002)

Dedicated HTTP service, completely isolated from credential proxy (:3001).

**What it handles:**
- Hook script execution for Layer 2 events dispatched from containers
- Memory queries (migrated from credential proxy)
- No access to API keys, OAuth tokens, or credential files

**Security controls (resolving threat model BLOCK):**

1. **Static hook allowlist:** Only hooks declared in config can be dispatched. Container requests reference hooks by event type, not by script path. The service determines which scripts fire.

2. **Input schema validation:** Per-event schemas enforced. Container CANNOT include arbitrary fields.

3. **Output sanitization:** `$HOME`, `$PROJECT_ROOT`, absolute host paths stripped from hook output before returning to container. Only structured fields pass through.

4. **Auth:** Reuses `DEUS_PROXY_TOKEN` per-group token, validated by `group-tokens.ts`.

5. **Rate limiting:** Per-group concurrency cap (max 5 concurrent dispatches). Excess gets 429. Keyed by validated group token.

6. **Port binding:** Binds to `PROXY_BIND_HOST` only. No external exposure.

### Hook Classification

| Hook | Target Layer | Rationale |
|------|-------------|-----------|
| plan-review-gate.sh | Layer 1 | Security gate: must be host-enforced |
| code-review-gate.sh | Layer 1 | Security gate: must be host-enforced |
| threat-model-gate.sh | Layer 1 | Security gate: must be host-enforced |
| injection-scanner.ts | Layer 1 | Already in the right place |
| path-leak-detector.sh | Layer 1 | Safety check, host-only |
| code-review-invalidator.sh | Layer 1 | Marker management, host-only |
| plan-mode-invalidator.sh | Layer 1 | Marker management, host-only |
| plan-mode-session-init.sh | Layer 1 | Session lifecycle, host-only |
| vault-context-hook.py | Layer 1 | Context injection, host-only |
| standards-pack.py | Layer 1 | Context injection, host-only |
| catchup-freshness.sh | Layer 1 | Context injection, host-only |
| memory-cite.sh | Layer 1 | Host filesystem check |
| memory-cite-seed.sh | Layer 1 | Session lifecycle, host-only |
| orchestrator-preflight.sh | Layer 1 | Already orchestrator-scoped |
| plan-revise-logger.sh | Layer 1 | Logging, host-only |
| memory-retrieval.sh | **Layer 2** | Needs per-turn firing inside agent loop |
| sonnet-default-reminder.sh | **DROP** | Claude-specific, not model-agnostic |

**Result:** 14 of 15 hooks become Layer 1 (host-enforced). Only memory-retrieval stays Layer 2. 1 dropped.

### HookPipeline Interface (Bridge Pattern)

```typescript
interface HookPipeline {
  // Layer 1: fires in orchestrator, can deny
  dispatchLayer1(
    event: 'SessionStart' | 'UserPromptSubmit' | 'Stop',
    context: HookContext,
    payload: Record<string, unknown>,
  ): Promise<Layer1HookResult>;

  // Layer 2: fires in container via HTTP bridge, cannot deny
  dispatchLayer2(
    event: 'PreToolUse' | 'PostToolUse',
    context: HookContext,
    payload: Record<string, unknown>,
  ): Promise<Layer2HookResult>;
}
```

Method names make the trust difference explicit. Part of `AgentRuntime` contract.

### Bridge Pattern in Code

```
Abstraction (Deus-owned)         Implementation (Backend-specific)
========================         =================================
HookPipeline interface    <----> Claude: SDK HookCallback adapter
                          <----> OpenAI: executeBrokerTool() wrapper
                          <----> Future: TBD adapter

HookDispatcher (host)     <----> ShellHookAdapter (runs scripts)

HookDispatchService       <----> HookBridge (container HTTP client)
(host :3002)                     (container-side, fail-open)
```

### Double-Enforcement (Claude Code sessions)

When using Claude backend, Claude Code's native hooks still fire inside the SDK. Layer 1 hooks ALSO fire in the orchestrator. This is defense-in-depth:
- Layer 1 catches issues before the container runs (definitive)
- Claude CC native hooks catch inside the agent loop (backup)
- Non-Claude backends: only Layer 1 fires (still fully protected)

### Default-Off

No `hooks.json` = zero hooks fire, zero overhead. Non-technical users are unaffected. The HookDispatchService still starts (serves memory queries) but hook dispatch returns empty results.

### Incremental Deployment

**Phase 1:** Interface + Layer 1 hooks in orchestrator. All 14 Layer 1 hooks become model-agnostic. No container changes.

**Phase 2:** HookDispatchService + Layer 2 bridge. Memory retrieval works for OpenAI. Credential proxy loses memory route.

**Phase 3:** Configuration (per-group enable/disable), migration guide, documentation.

## Consequences

- All hooks work across all backends
- Security hooks are host-enforced (cannot be bypassed by container)
- Credential proxy attack surface reduced
- Non-technical users see zero overhead
- Future backends get hooks by implementing HookPipeline (2 methods)
