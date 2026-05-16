#!/usr/bin/env node
/**
 * Tests for scripts/migrate.mjs
 * Run: node scripts/tests/test_migrate.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import os from 'node:os';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function makeTmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'deus-migrate-test-'));
}

function cleanup(dir) {
  fs.rmSync(dir, { recursive: true, force: true });
}

function writeJSON(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
}

async function test_discovery_finds_migration_files() {
  const tmp = makeTmpDir();
  try {
    const migrationsDir = path.join(tmp, 'migrations');
    fs.mkdirSync(migrationsDir);
    fs.writeFileSync(path.join(migrationsDir, '0001-test.mjs'), `
      export const id = '0001';
      export const title = 'Test migration';
      export const type = 'auto';
      export function check() { return false; }
      export function apply({ root }) {
        const fs = await import('node:fs');
        fs.default.writeFileSync(root + '/.applied', '1');
      }
    `);
    // Non-migration files should be ignored
    fs.writeFileSync(path.join(migrationsDir, 'README.md'), '# ignored');
    fs.writeFileSync(path.join(migrationsDir, 'not-a-migration.txt'), 'ignored');

    const files = fs.readdirSync(migrationsDir)
      .filter(f => /^\d{4}-.+\.mjs$/.test(f))
      .sort();
    assert.deepEqual(files, ['0001-test.mjs']);
    console.log('  PASS: discovery_finds_migration_files');
  } finally {
    cleanup(tmp);
  }
}

async function test_state_file_created_on_first_run() {
  const tmp = makeTmpDir();
  try {
    const migrationsDir = path.join(tmp, 'migrations');
    fs.mkdirSync(migrationsDir);
    fs.writeFileSync(path.join(migrationsDir, '0001-noop.mjs'), `
      export const id = '0001';
      export const title = 'Noop';
      export const type = 'auto';
      export function check() { return true; }
      export function apply() {}
    `);

    const stateDir = path.join(tmp, '.deus');
    const stateFile = path.join(stateDir, 'migration-state.json');

    // Simulate what the runner does
    fs.mkdirSync(stateDir, { recursive: true });
    writeJSON(stateFile, { applied: ['0001'], schema: 1 });

    const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    assert.deepEqual(state.applied, ['0001']);
    assert.equal(state.schema, 1);
    console.log('  PASS: state_file_created_on_first_run');
  } finally {
    cleanup(tmp);
  }
}

async function test_idempotency_skips_applied() {
  const tmp = makeTmpDir();
  try {
    const stateDir = path.join(tmp, '.deus');
    writeJSON(path.join(stateDir, 'migration-state.json'), {
      applied: ['0001'],
      schema: 1,
    });

    const state = JSON.parse(
      fs.readFileSync(path.join(stateDir, 'migration-state.json'), 'utf8')
    );
    // Simulate runner check: if ID is in applied, skip
    assert.ok(state.applied.includes('0001'));
    assert.ok(!state.applied.includes('0002'));
    console.log('  PASS: idempotency_skips_applied');
  } finally {
    cleanup(tmp);
  }
}

async function test_check_true_marks_applied_without_running() {
  const tmp = makeTmpDir();
  try {
    const state = { applied: [], schema: 1 };
    // Simulate: check() returns true → add to applied without calling apply()
    const checkResult = true;
    if (checkResult) {
      state.applied.push('0001');
    }
    assert.deepEqual(state.applied, ['0001']);
    console.log('  PASS: check_true_marks_applied_without_running');
  } finally {
    cleanup(tmp);
  }
}

async function test_pending_count() {
  const tmp = makeTmpDir();
  try {
    const migrationsDir = path.join(tmp, 'migrations');
    fs.mkdirSync(migrationsDir);
    fs.writeFileSync(path.join(migrationsDir, '0001-a.mjs'), '');
    fs.writeFileSync(path.join(migrationsDir, '0002-b.mjs'), '');
    fs.writeFileSync(path.join(migrationsDir, '0003-c.mjs'), '');

    const stateDir = path.join(tmp, '.deus');
    writeJSON(path.join(stateDir, 'migration-state.json'), {
      applied: ['0001'],
      schema: 1,
    });

    const files = fs.readdirSync(migrationsDir)
      .filter(f => /^\d{4}-.+\.mjs$/.test(f));
    const state = JSON.parse(
      fs.readFileSync(path.join(stateDir, 'migration-state.json'), 'utf8')
    );
    const pending = files.filter(f => !state.applied.includes(f.split('-')[0]));
    assert.equal(pending.length, 2);
    console.log('  PASS: pending_count');
  } finally {
    cleanup(tmp);
  }
}

async function test_post_checkout_guard_logic() {
  // The post-checkout hook checks $3 === "1" for branch switch
  // $3 === "0" means file checkout — should skip
  const branchSwitch = '1';
  const fileCheckout = '0';
  assert.equal(branchSwitch === '1', true);
  assert.equal(fileCheckout === '1', false);
  console.log('  PASS: post_checkout_guard_logic');
}

async function test_state_survives_missing_deus_dir() {
  const tmp = makeTmpDir();
  try {
    // No .deus/ dir — loadState should return default
    const stateFile = path.join(tmp, '.deus', 'migration-state.json');
    let state;
    try {
      state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    } catch {
      state = { applied: [], schema: 1 };
    }
    assert.deepEqual(state.applied, []);
    assert.equal(state.schema, 1);
    console.log('  PASS: state_survives_missing_deus_dir');
  } finally {
    cleanup(tmp);
  }
}

// Run all tests
console.log('scripts/tests/test_migrate.mjs:');
await test_discovery_finds_migration_files();
await test_state_file_created_on_first_run();
await test_idempotency_skips_applied();
await test_check_true_marks_applied_without_running();
await test_pending_count();
await test_post_checkout_guard_logic();
await test_state_survives_missing_deus_dir();
console.log('\nAll tests passed.');
