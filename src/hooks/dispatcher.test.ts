import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { writeFileSync, mkdirSync, rmSync } from 'fs';
import path from 'path';
import os from 'os';
import { createHookDispatcher } from './dispatcher.js';
import type { HookContext, HooksConfig } from './types.js';

const ctx: HookContext = {
  groupFolder: '/tmp/test-group',
  chatJid: 'test@jid',
  backend: 'openai',
  prompt: 'test message',
  sessionId: 'sess-1',
};

describe('createHookDispatcher', () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = path.join(os.tmpdir(), `hooks-test-${Date.now()}`);
    mkdirSync(tmpDir, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('returns continue=true when no config file exists', async () => {
    const pipeline = createHookDispatcher({
      configPath: path.join(tmpDir, 'nonexistent.json'),
      repoRoot: tmpDir,
    });
    const result = await pipeline.enforce('SessionStart', ctx, {});
    expect(result.continue).toBe(true);
    expect(result.additionalContext).toBeUndefined();
  });

  it('returns continue=true when config has no hooks for the event', async () => {
    const configPath = path.join(tmpDir, 'hooks.json');
    const config: HooksConfig = { version: 1, events: {} };
    writeFileSync(configPath, JSON.stringify(config));

    const pipeline = createHookDispatcher({ configPath, repoRoot: tmpDir });
    const result = await pipeline.enforce('UserPromptSubmit', ctx, {});
    expect(result.continue).toBe(true);
  });

  it('returns continue=true on malformed JSON', async () => {
    const configPath = path.join(tmpDir, 'hooks.json');
    writeFileSync(configPath, 'not valid json{');

    const pipeline = createHookDispatcher({ configPath, repoRoot: tmpDir });
    const result = await pipeline.enforce('SessionStart', ctx, {});
    expect(result.continue).toBe(true);
  });

  it('observe() always returns empty (Phase 2 stub)', async () => {
    const pipeline = createHookDispatcher({
      configPath: path.join(tmpDir, 'nonexistent.json'),
      repoRoot: tmpDir,
    });
    const result = await pipeline.observe('PreToolUse', ctx, {});
    expect(result).toEqual({});
  });

  it('runs hooks sequentially and concatenates additionalContext', async () => {
    const configPath = path.join(tmpDir, 'hooks.json');
    const script1 = path.join(tmpDir, 'hook1.sh');
    const script2 = path.join(tmpDir, 'hook2.sh');

    writeFileSync(
      script1,
      '#!/bin/bash\necho \'{"hookSpecificOutput":{"additionalContext":"context-one"}}\'',
      { mode: 0o755 },
    );
    writeFileSync(
      script2,
      '#!/bin/bash\necho \'{"hookSpecificOutput":{"additionalContext":"context-two"}}\'',
      { mode: 0o755 },
    );

    const config: HooksConfig = {
      version: 1,
      events: {
        SessionStart: [
          { script: script1, timeout: 5 },
          { script: script2, timeout: 5 },
        ],
      },
    };
    writeFileSync(configPath, JSON.stringify(config));

    // Override PYTHON_BIN for shell scripts — use bash directly
    const { runHookEntry } = await import('./shell-adapter.js');
    const spy = vi.spyOn(await import('./shell-adapter.js'), 'runHookEntry');
    spy.mockResolvedValueOnce({
      continue: true,
      additionalContext: 'context-one',
    });
    spy.mockResolvedValueOnce({
      continue: true,
      additionalContext: 'context-two',
    });

    const pipeline = createHookDispatcher({ configPath, repoRoot: tmpDir });
    const result = await pipeline.enforce('SessionStart', ctx, {});

    expect(result.continue).toBe(true);
    expect(result.additionalContext).toBe('context-one\n\ncontext-two');

    spy.mockRestore();
  });

  it('short-circuits on first continue=false', async () => {
    const configPath = path.join(tmpDir, 'hooks.json');
    const config: HooksConfig = {
      version: 1,
      events: {
        UserPromptSubmit: [
          { script: 'hook1.sh', timeout: 5 },
          { script: 'hook2.sh', timeout: 5 },
        ],
      },
    };
    writeFileSync(configPath, JSON.stringify(config));

    const spy = vi.spyOn(await import('./shell-adapter.js'), 'runHookEntry');
    spy.mockResolvedValueOnce({
      continue: false,
      stopReason: 'blocked by gate',
    });
    // second hook should NOT be called
    spy.mockResolvedValueOnce({
      continue: true,
      additionalContext: 'should not appear',
    });

    const pipeline = createHookDispatcher({ configPath, repoRoot: tmpDir });
    const result = await pipeline.enforce('UserPromptSubmit', ctx, {});

    expect(result.continue).toBe(false);
    expect(result.stopReason).toBe('blocked by gate');
    expect(spy).toHaveBeenCalledTimes(1);

    spy.mockRestore();
  });
});

describe('hooks.json validation', () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = path.join(os.tmpdir(), `hooks-validate-${Date.now()}`);
    mkdirSync(tmpDir, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('rejects entry with neither behavior nor script', async () => {
    const configPath = path.join(tmpDir, 'hooks.json');
    writeFileSync(
      configPath,
      JSON.stringify({
        version: 1,
        events: { SessionStart: [{ timeout: 5 }] },
      }),
    );

    const pipeline = createHookDispatcher({ configPath, repoRoot: tmpDir });
    const result = await pipeline.enforce('SessionStart', ctx, {});
    expect(result.continue).toBe(true);
  });

  it('rejects entry with empty behavior string', async () => {
    const configPath = path.join(tmpDir, 'hooks.json');
    writeFileSync(
      configPath,
      JSON.stringify({
        version: 1,
        events: { SessionStart: [{ behavior: '' }] },
      }),
    );

    const pipeline = createHookDispatcher({ configPath, repoRoot: tmpDir });
    const result = await pipeline.enforce('SessionStart', ctx, {});
    expect(result.continue).toBe(true);
  });

  it('rejects unsupported version', async () => {
    const configPath = path.join(tmpDir, 'hooks.json');
    writeFileSync(configPath, JSON.stringify({ version: 99, events: {} }));

    const pipeline = createHookDispatcher({ configPath, repoRoot: tmpDir });
    const result = await pipeline.enforce('SessionStart', ctx, {});
    expect(result.continue).toBe(true);
  });
});
