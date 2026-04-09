---
governs:
  - .claude/skills
---
# Pattern: skill-add

## What a skill PR contains

- `SKILL.md` — required
- `agent.ts` — if the skill adds container-side MCP tools

## What must never be committed in a skill PR

| File | Reason |
|------|--------|
| `host.ts` / `host.js` | Private host-side implementation |
| `scripts/` | Subprocess scripts |
| `node_modules/`, `package-lock.json` | Local dependencies |

CI rejects skill PRs that also touch `src/`.

## Core source changes

If a skill genuinely requires `src/` changes, open a separate source PR first. Explain in the PR description why the feature cannot be a skill.

## Tests

Every change to `agent.ts` requires a test. Run `npm test` before committing.

## Commit format

`feat(skills): <description>` — scope is `skills`.
