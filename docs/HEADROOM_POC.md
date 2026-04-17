# Headroom POC — Scope

Follow-up experiment scoped from
[`chopratejas/headroom`](https://github.com/chopratejas/headroom).

## What Headroom is

Headroom is a "context optimization" proxy / SDK that sits between an agent
and an LLM provider and compresses the content the agent feeds the model:
tool outputs, database results, RAG chunks, file reads, API responses,
conversation history. Usable as a transparent HTTP proxy (zero code
changes) or called programmatically (`compress()` in Python / TypeScript).

Public benchmarks from the author claim 70–80% reduction on token usage
for Claude Code / Cursor sessions dominated by heavy tool output.

## Why it's worth evaluating

The current `@anthropic-ai/claude-agent-sdk` (0.2.76 / 0.2.112) does not
expose a hook for rewriting built-in Bash/Read/Grep tool results. The
`PostToolUseHookSpecificOutput` surface only has `updatedMCPToolOutput`,
limited to MCP tools. This is documented in `docs/TOKEN_OPTIMIZATION.md` as
a follow-up pending SDK surface change.

Headroom solves the same problem at the network layer instead. If it works
reliably for our traffic profile, it covers the biggest remaining token
leak we can't otherwise address.

## Traffic profile caveat

Deus container agents are personal-assistant use cases (short WhatsApp /
Telegram queries, short responses, a handful of tool calls per turn). The
70–80% savings Headroom advertises are measured on coding-agent sessions
where tool outputs are the dominant context (huge git diffs, full-test
logs, long READMEs). Our traffic probably sees much smaller savings. The
POC's first job is to quantify *our* savings, not to assume the public
numbers apply.

## POC shape

### Phase A — measure without integrating (1 hr)

Before any integration:

1. Instrument the container agent-runner to log per-turn aggregated sizes
   of each tool result. A PostToolUse hook that only logs (no rewrite) gives
   us real data: how many tokens are actually spent on tool outputs in live
   sessions over a few days?
2. Sample 20–50 real sessions (with user consent — vault-level data).
3. Decide: is the tool-output share big enough that 50–80% compression
   would matter? Minimum worthwhile threshold suggestion: tool outputs
   consuming ≥ 20% of input-token budget across the sample.

If the sample shows tool outputs are already small (<10% of input tokens),
**abandon the POC** — not worth the infra cost.

### Phase B — transparent proxy trial (2–3 hr)

Only if Phase A clears the bar:

1. Stand up Headroom as a proxy inside the existing container network
   (between agent-runner and the Anthropic API). The existing OAuth
   credential-proxy at `:3001` already sits in this path — Headroom
   chains behind it or integrates with it.
2. Run a parallel canary: one WhatsApp channel routed through Headroom,
   one routed directly. Same user, same time period, different routing.
3. For each side, capture:
   - Input tokens per turn (from Anthropic usage metadata)
   - Output tokens per turn
   - Response quality score (OllamaJudge via `evolution/judge/`)
   - Latency (p50, p95)
4. Run for 100+ turns per side. Compare.

### Phase C — decision criteria

Ship Headroom integration to all channels only if all four hold:

- Input token reduction ≥ 15% averaged across the canary sample
- Judge quality score regression ≤ 2% vs control
- p95 latency regression ≤ 500ms vs control
- No user-reported quality issues during canary

If any fails: keep Phase A logging in place as a long-term metric, drop
Headroom, document the decision in an ADR, and revisit if the SDK exposes
built-in-tool rewrite hooks.

## What this POC does NOT answer

- Whether Headroom helps **host Claude Code sessions** (i.e., the dev
  sessions where an engineer uses `claude` CLI in ~/deus). Headroom
  installation for those is a separate question.
- Whether Headroom's compression interacts poorly with Claude's prompt
  cache (compressed input = different cache key = invalidated cache read).
  Worth checking the Headroom docs / implementation before Phase B.
- Whether the per-turn compression latency adds up when the container
  spawns parallel subagents (Agent tool / TeamCreate).

## Cost estimate

- Phase A: 1 hour (a hook + a logging sink + read the output).
- Phase B: 2–3 hours (stand up proxy, route one channel, instrument).
- Phase C: passive monitoring, 100+ turns of natural usage.

Total: ~4 hours of eng time + one week of canary data.

## Exit conditions

- **Ship it:** all four decision criteria met in Phase C.
- **Park it:** Phase A shows tool outputs are <10% of token budget, OR
  Phase C fails any criterion. Document in an ADR and keep the Phase A
  logging for future revisit.
- **Cheaper alternative appears:** SDK exposes an `updatedToolOutput` hook
  for built-in tools → wire truncation in directly instead, no proxy.
