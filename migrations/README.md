# Migrations

Idempotent local-state reconciliation scripts. Run automatically after
`git pull`, `git rebase`, branch switches, and `npm install`.

## When to add a migration

Only for **local state that git doesn't deliver**:

- `.claude/settings.json` hook additions/changes
- New required `.env` variables
- System dependency requirements
- Container config changes requiring restart
- Renamed/moved config files needing local cleanup

Do NOT add migrations for new source files, patterns, docs, or scripts — git
already delivers those on pull.

## File format

```javascript
// migrations/NNNN-short-description.mjs
import fs from 'node:fs';
import path from 'node:path';

export const id = 'NNNN';
export const title = 'Human-readable title';
export const description = 'Optional longer text shown for manual migrations.';
export const type = 'auto'; // 'auto' | 'manual'

// Return true if migration is already applied. Must be side-effect-free.
export function check({ root }) {
  // root = repo root directory
  return fs.existsSync(path.join(root, '.claude/settings.json'));
}

// Apply the migration. Only called if check() returned false.
// MUST be idempotent — safe to call multiple times.
export function apply({ root }) {
  // Use read → merge → write pattern for JSON files
}
```

## Contracts

1. **Idempotency**: `apply()` must be safe to re-run. If `.deus/` is deleted,
   all auto migrations re-run — they must not corrupt existing state.
2. **Side-effect-free check()**: `check()` must only read, never write.
3. **Cross-platform**: Use only portable Node.js APIs (`fs`, `path`, `os`,
   `url`). No `path.sep` tricks, no `process.env.HOME` — use `os.homedir()`.
4. **Manual migrations**: Set `type = 'manual'`. The runner shows instructions
   but won't mark as applied until `check()` returns true.

## Running manually

```bash
npm run migrate          # Apply pending (verbose)
npm run migrate:dry-run  # Show what would be applied
npm run migrate:status   # Same as dry-run
```

## How it triggers

| User action | Trigger mechanism |
|---|---|
| `git pull` (merge) | `.husky/post-merge` |
| `git pull --rebase` | `.husky/post-rewrite` |
| `git checkout <branch>` | `.husky/post-checkout` |
| Fresh clone + `npm install` | `package.json` prepare script |
| GUI git client (no hooks) | Session nudge on next agent start |

## State tracking

Applied migrations tracked in `.deus/migration-state.json` (gitignored).
Uses migration IDs (not sequence numbers) — survives rebases and cherry-picks.
The runner uses BOTH the state file AND `check()` for idempotency.
