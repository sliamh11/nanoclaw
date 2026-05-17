import { spawn } from 'child_process';
import path from 'path';

import { killProcess, PYTHON_BIN } from '../platform.js';
import { logger } from '../logger.js';
import type {
  EnforcementEvent,
  EnforcementHookResult,
  HookContext,
  HookEntryConfig,
} from './types.js';

const DEFAULT_HOOK_TIMEOUT_MS = 10_000;

export function buildEventPayload(
  event: EnforcementEvent,
  context: HookContext,
): Record<string, unknown> {
  const base = {
    hook_event_name: event,
    cwd: context.groupFolder,
    session_id: context.sessionId ?? '',
  };

  if (event === 'UserPromptSubmit') {
    return { ...base, prompt: context.prompt ?? '' };
  }

  return base;
}

export function parseHookOutput(raw: string): EnforcementHookResult {
  const trimmed = raw.trim();
  if (!trimmed) return { continue: true };

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { continue: true };
  }

  if (typeof parsed !== 'object' || parsed === null) return { continue: true };
  const obj = parsed as Record<string, unknown>;

  const hso = obj['hookSpecificOutput'];
  if (typeof hso === 'object' && hso !== null) {
    const hsoObj = hso as Record<string, unknown>;
    if (hsoObj['permissionDecision'] === 'deny') {
      return {
        continue: false,
        stopReason: String(
          hsoObj['permissionDecisionReason'] ?? 'Hook denied operation',
        ),
      };
    }
    if (typeof hsoObj['additionalContext'] === 'string') {
      return { continue: true, additionalContext: hsoObj['additionalContext'] };
    }
  }

  if (typeof obj['systemMessage'] === 'string') {
    return { continue: true, additionalContext: obj['systemMessage'] };
  }

  return { continue: true };
}

function runProcess(
  args: string[],
  stdinJson: Record<string, unknown>,
  timeoutMs: number,
): Promise<string> {
  return new Promise((resolve) => {
    let stdout = '';
    let settled = false;

    const proc = spawn(args[0], args.slice(1), {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });

    proc.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString();
    });

    proc.stderr.on('data', (chunk: Buffer) => {
      const msg = chunk.toString().trim();
      if (msg) logger.debug({ hook: args.join(' ') }, msg);
    });

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        if (proc.pid) killProcess(proc.pid);
        logger.warn({ hook: args.join(' '), timeoutMs }, 'Hook timed out');
        resolve('');
      }
    }, timeoutMs);

    proc.on('close', (code) => {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      if (code !== 0 && code !== null) {
        logger.warn({ hook: args.join(' '), code }, 'Hook exited non-zero');
      }
      resolve(stdout);
    });

    proc.on('error', (err) => {
      clearTimeout(timer);
      if (settled) return;
      settled = true;
      logger.error({ hook: args.join(' '), err }, 'Hook spawn error');
      resolve('');
    });

    try {
      proc.stdin.write(JSON.stringify(stdinJson));
      proc.stdin.end();
    } catch {
      // stdin may already be closed
    }
  });
}

export async function runHookEntry(
  entry: HookEntryConfig,
  event: EnforcementEvent,
  context: HookContext,
  payload: Record<string, unknown>,
  repoRoot: string,
): Promise<EnforcementHookResult> {
  const timeoutMs = (entry.timeout ?? 10) * 1000 || DEFAULT_HOOK_TIMEOUT_MS;
  const stdinJson = buildEventPayload(event, context);

  let args: string[];
  if ('behavior' in entry) {
    const scriptPath = path.join(repoRoot, 'scripts', 'codex_warden_hooks.py');
    args = [
      PYTHON_BIN,
      scriptPath,
      'run',
      entry.behavior,
      '--repo-root',
      repoRoot,
    ];
  } else {
    const scriptPath = path.isAbsolute(entry.script)
      ? entry.script
      : path.join(repoRoot, entry.script);
    args = [PYTHON_BIN, scriptPath];
  }

  try {
    const raw = await runProcess(args, stdinJson, timeoutMs);
    return parseHookOutput(raw);
  } catch (err) {
    logger.error({ err, hook: args.join(' ') }, 'Hook execution error');
    return { continue: true };
  }
}
