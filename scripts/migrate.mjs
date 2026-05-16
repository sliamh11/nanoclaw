#!/usr/bin/env node
/**
 * Idempotent migration runner for post-pull local state reconciliation.
 * Usage: node scripts/migrate.mjs [--dry-run] [--verbose] [--quiet]
 *
 * Cross-platform: uses only portable Node.js APIs (fs, path, os, url).
 * No path.sep tricks, no process.env.HOME — use os.homedir() if needed.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const MIGRATIONS_DIR = path.join(ROOT, 'migrations');
const STATE_FILE = path.join(ROOT, '.deus', 'migration-state.json');

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
  } catch {
    return { applied: [], schema: 1 };
  }
}

function saveState(state) {
  const dir = path.dirname(STATE_FILE);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2) + '\n');
}

export async function run(options = {}) {
  const { dryRun = false, verbose = false, quiet = false } = options;

  if (!fs.existsSync(MIGRATIONS_DIR)) {
    if (verbose) console.log('[deus-migrate] No migrations/ directory found.');
    return { applied: 0, skipped: 0, manual: 0 };
  }

  const files = fs.readdirSync(MIGRATIONS_DIR)
    .filter(f => /^\d{4}-.+\.mjs$/.test(f))
    .sort();

  if (files.length === 0) {
    if (verbose) console.log('[deus-migrate] No migration files found.');
    return { applied: 0, skipped: 0, manual: 0 };
  }

  const state = loadState();
  let applied = 0;
  let skipped = 0;
  const manual = [];

  for (const file of files) {
    const migrationUrl = pathToFileURL(path.join(MIGRATIONS_DIR, file)).href;
    const migration = await import(migrationUrl);

    if (state.applied.includes(migration.id)) {
      skipped++;
      continue;
    }

    let satisfied = false;
    try {
      satisfied = migration.check({ root: ROOT });
    } catch (err) {
      if (verbose) console.log(`  ? ${migration.id} — check() threw: ${err.message}`);
    }

    if (satisfied) {
      state.applied.push(migration.id);
      skipped++;
      if (verbose) console.log(`  ✓ ${migration.id} — already satisfied`);
      continue;
    }

    if (migration.type === 'manual') {
      manual.push(migration);
      continue;
    }

    if (dryRun) {
      console.log(`  [dry-run] Would apply: ${migration.id} — ${migration.title}`);
      continue;
    }

    try {
      migration.apply({ root: ROOT });
      state.applied.push(migration.id);
      applied++;
      if (!quiet) console.log(`  ✓ Applied: ${migration.id} — ${migration.title}`);
    } catch (err) {
      console.error(`  ✗ Failed: ${migration.id} — ${err.message}`);
      if (!quiet) process.exitCode = 1;
      break;
    }
  }

  if (!dryRun) saveState(state);

  if (applied > 0 && !quiet) {
    console.log(`\n[deus] ${applied} migration(s) applied.`);
  }

  if (manual.length > 0 && !quiet) {
    console.log(`\n[deus] ${manual.length} manual migration(s) pending:`);
    for (const m of manual) {
      console.log(`  → ${m.id}: ${m.title}`);
      if (m.description) console.log(`    ${m.description}`);
    }
  }

  return { applied, skipped, manual: manual.length };
}

/**
 * Count pending migrations without applying. Used by session hooks.
 */
export function pendingCount() {
  if (!fs.existsSync(MIGRATIONS_DIR)) return 0;

  const files = fs.readdirSync(MIGRATIONS_DIR)
    .filter(f => /^\d{4}-.+\.mjs$/.test(f));

  if (files.length === 0) return 0;

  const state = loadState();
  let pending = 0;

  for (const file of files) {
    const id = file.split('-')[0];
    if (!state.applied.includes(id)) pending++;
  }
  return pending;
}

// --- CLI entry ---
if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const args = process.argv.slice(2);

  if (args.includes('--help') || args.includes('-h')) {
    console.log(`Usage: node scripts/migrate.mjs [options]

Options:
  --dry-run   Show what would be applied without making changes
  --verbose   Show all migrations including skipped ones
  --quiet     Suppress output and always exit 0 (for git hooks)
  --pending   Print pending count and exit
  --help      Show this message`);
    process.exit(0);
  }

  if (args.includes('--pending')) {
    const count = pendingCount();
    if (count > 0) console.log(`[deus] ${count} pending migration(s). Run: npm run migrate`);
    process.exit(0);
  }

  const quiet = args.includes('--quiet');
  try {
    await run({
      dryRun: args.includes('--dry-run'),
      verbose: args.includes('--verbose'),
      quiet,
    });
  } catch (err) {
    if (!quiet) console.error(`[deus-migrate] ${err.message}`);
  }
  if (quiet) process.exitCode = 0;
}
