import { readFileSync } from 'fs';

import { HOOKS_CONFIG_PATH, PROJECT_ROOT } from '../config.js';
import { logger } from '../logger.js';
import { runHookEntry } from './shell-adapter.js';
import type {
  EnforcementEvent,
  EnforcementHookResult,
  HookContext,
  HookEntryConfig,
  HooksConfig,
  HookPipeline,
  ObserverEvent,
  ObserverHookResult,
} from './types.js';

export interface HookDispatcherDeps {
  configPath?: string;
  repoRoot?: string;
}

const MAX_CONTEXT_BYTES = 24_000;

function isValidEntry(entry: unknown): entry is HookEntryConfig {
  if (typeof entry !== 'object' || entry === null) return false;
  const obj = entry as Record<string, unknown>;
  const hasBehavior =
    'behavior' in obj &&
    typeof obj['behavior'] === 'string' &&
    obj['behavior'] !== '';
  const hasScript =
    'script' in obj &&
    typeof obj['script'] === 'string' &&
    obj['script'] !== '';
  return (hasBehavior || hasScript) && !(hasBehavior && hasScript);
}

function loadHooksConfig(configPath: string): HooksConfig | null {
  let raw: string;
  try {
    raw = readFileSync(configPath, 'utf-8');
  } catch {
    return null;
  }

  const parsed = JSON.parse(raw) as Record<string, unknown>;
  if ((parsed as { version?: unknown }).version !== 1) {
    throw new Error(
      `Unsupported hooks.json version: ${(parsed as { version?: unknown }).version}`,
    );
  }

  const events = (parsed as { events?: unknown }).events;
  if (typeof events !== 'object' || events === null) {
    throw new Error('hooks.json: missing or invalid "events" field');
  }

  const eventsObj = events as Record<string, unknown>;
  for (const [eventName, entries] of Object.entries(eventsObj)) {
    if (!Array.isArray(entries)) {
      throw new Error(`hooks.json: events.${eventName} must be an array`);
    }
    for (let i = 0; i < entries.length; i++) {
      if (!isValidEntry(entries[i])) {
        throw new Error(
          `hooks.json: events.${eventName}[${i}] must have exactly one of "behavior" or "script" (non-empty string)`,
        );
      }
    }
  }

  return parsed as unknown as HooksConfig;
}

async function runSequential(
  hooks: HookEntryConfig[],
  event: EnforcementEvent,
  context: HookContext,
  payload: Record<string, unknown>,
  repoRoot: string,
): Promise<EnforcementHookResult> {
  const contextParts: string[] = [];
  let contextBytes = 0;

  for (const entry of hooks) {
    const result = await runHookEntry(entry, event, context, payload, repoRoot);

    if (result.additionalContext) {
      const chunk = result.additionalContext;
      if (contextBytes + chunk.length <= MAX_CONTEXT_BYTES) {
        contextParts.push(chunk);
        contextBytes += chunk.length;
      } else {
        logger.warn(
          { event, bytes: contextBytes + chunk.length, max: MAX_CONTEXT_BYTES },
          'Hook additionalContext exceeds budget — truncating',
        );
      }
    }

    if (!result.continue) {
      return {
        continue: false,
        stopReason: result.stopReason,
        additionalContext: contextParts.join('\n\n') || undefined,
      };
    }
  }

  return {
    continue: true,
    additionalContext: contextParts.join('\n\n') || undefined,
  };
}

// Config is read once at startup — restart required to pick up changes.
export function createHookDispatcher(
  deps: HookDispatcherDeps = {},
): HookPipeline {
  const configPath = deps.configPath ?? HOOKS_CONFIG_PATH;
  const repoRoot = deps.repoRoot ?? PROJECT_ROOT;

  let config: HooksConfig | null = null;
  try {
    config = loadHooksConfig(configPath);
  } catch (err) {
    logger.error(
      { err, configPath },
      'hooks.json parse error — hooks disabled',
    );
    config = null;
  }

  if (config) {
    const hookCount =
      (config.events.SessionStart?.length ?? 0) +
      (config.events.UserPromptSubmit?.length ?? 0) +
      (config.events.Stop?.length ?? 0);
    logger.info({ configPath, hookCount }, 'HookDispatcher loaded');
  } else {
    logger.debug('HookDispatcher: no hooks.json — zero hooks will fire');
  }

  return {
    async enforce(
      event: EnforcementEvent,
      context: HookContext,
      payload: Record<string, unknown>,
    ): Promise<EnforcementHookResult> {
      if (!config) return { continue: true };

      const hooks = config.events[event] ?? [];
      if (hooks.length === 0) return { continue: true };

      return runSequential(hooks, event, context, payload, repoRoot);
    },

    // Phase 2: Observer Layer (PreToolUse/PostToolUse via container HTTP bridge)
    async observe(
      _event: ObserverEvent,
      _context: HookContext,
      _payload: Record<string, unknown>,
    ): Promise<ObserverHookResult> {
      return {};
    },
  };
}
