# `--effort` A/B probe results (2026-04-17)

## Setup

`scripts/token_bench/effort_probe.sh` runs 5 fixed prompts against the
WhatsApp main-group `CLAUDE.md` via `claude -p` in a temp cwd. Two variants:

- **default** — no `--effort` flag (Claude Code's own default)
- **low** — `--effort low`

Both run on Opus 4.7. Thinking is not explicitly enabled (SDK default: off on
Opus 4.7). Elapsed time is wall-clock including CLI startup; output captured
as JSON lines.

## Results

| Probe             | Default (ms) | Low (ms) | Δ ms  | Δ %   | Quality |
|-------------------|-------------:|---------:|------:|------:|---------|
| fmt_bold          |       7,347  |   5,219  | -2,128| -29%  | Low: cleaner, more concise |
| fmt_no_heading    |       8,755  |   8,227  |   -528|  -6%  | Tie — both pass, both well-structured |
| internal_tag      |       5,862  |   5,097  |   -765| -13%  | Tie — both answered "4", neither used tags (weak probe) |
| voice_reminder    |      13,907  |  14,531  |  +624 |  +4%  | Low: cleaner (no extra writability-warning noise) |
| persona_recall    |       5,853  |   5,104  |   -749| -13%  | Tie — both recalled correctly |
| **avg**           |     **8,345**|**7,636** |**-709**| **-8.5%** | |

**Zero regressions across all 5 probes.** On 4/5, `--effort low` is faster;
on `voice_reminder` it was 4% slower (within single-sample noise).

Output quality under `low` was **equal or better** — more concise, less
padding. Matches the migration-guide observation that Opus 4.7 calibrates
verbosity to task complexity, more aggressively at lower effort levels.

## Caveats

- n=5 probes — directional signal only, not statistical significance.
- `claude -p` CLI surface may differ slightly from container agent-runner's
  Agent SDK invocation. Test is an approximation of real container behavior.
- Opus 4.7 adaptive thinking was **not** opted into in either variant (we
  don't set `thinking: {type: 'adaptive'}` anywhere). The latency win is
  therefore attributable to effort-level output calibration, not thinking
  savings.
- Probes are single-turn. Multi-turn/tool-heavy sessions could show
  different patterns.

## Interpretation

`--effort low` on Opus 4.7 is a **small net win** for the WhatsApp/Telegram
personal-assistant use case. Short prompts, short tool chains, latency is
visible to users — exactly what `low` is designed for (per migration guide:
"Reserve for short, scoped tasks and latency-sensitive workloads that are
not intelligence-sensitive").

## Recommendation

Adopt `effort: 'low'` in `container/agent-runner/src/index.ts` for
main-group container sessions, gated by a feature flag so it can be
reverted per channel if a quality issue surfaces. Would require:

1. Add `effort: 'low'` to the `options` object passed to `query()`.
2. Feature-flag it via an env var (`DEUS_AGENT_EFFORT=low|default|high`)
   so a rollback is one env change, no deploy.
3. Monitor for a week via reflexion scores in `evolution/`.

Not in the original token-optimization PR (#179) — that PR shipped the
compression gains. Effort-tuning is a separate concern and deserves its own
rollout cycle.

## Bigger samples (follow-up)

Before flipping the flag on all channels:
- Run the probe with 20–30 more varied prompts (tool-using queries, gcal,
  voice reminders with complex detail, research queries).
- Add `gemma4:e4b` judge-scoring on the response pairs for a quantitative
  quality score, not just eyeball inspection.
- Measure token usage via the Messages API usage metadata (the CLI path
  here doesn't expose it directly).
