export type EnforcementEvent = 'SessionStart' | 'UserPromptSubmit' | 'Stop';

export type ObserverEvent = 'PreToolUse' | 'PostToolUse';

export interface HookContext {
  groupFolder: string;
  chatJid: string;
  backend: string;
  prompt?: string;
  sessionId?: string;
}

export interface EnforcementHookResult {
  continue: boolean;
  stopReason?: string;
  additionalContext?: string;
}

export interface ObserverHookResult {
  additionalContext?: string;
  updatedInput?: Record<string, unknown>;
}

export interface HookPipeline {
  enforce(
    event: EnforcementEvent,
    context: HookContext,
    payload: Record<string, unknown>,
  ): Promise<EnforcementHookResult>;

  observe(
    event: ObserverEvent,
    context: HookContext,
    payload: Record<string, unknown>,
  ): Promise<ObserverHookResult>;
}

export type HookEntryConfig =
  | { behavior: string; timeout?: number }
  | { script: string; timeout?: number };

export interface HooksConfig {
  version: 1;
  events: {
    SessionStart?: HookEntryConfig[];
    UserPromptSubmit?: HookEntryConfig[];
    Stop?: HookEntryConfig[];
  };
}
