import { describe, it, expect } from 'vitest';
import { buildEventPayload, parseHookOutput } from './shell-adapter.js';
import type { HookContext } from './types.js';

const ctx: HookContext = {
  groupFolder: '/tmp/test-group',
  chatJid: 'test@jid',
  backend: 'openai',
  prompt: 'hello world',
  sessionId: 'sess-123',
};

describe('parseHookOutput', () => {
  it('returns continue=true for empty output', () => {
    expect(parseHookOutput('')).toEqual({ continue: true });
    expect(parseHookOutput('  \n  ')).toEqual({ continue: true });
  });

  it('returns continue=true for non-JSON output', () => {
    expect(parseHookOutput('not json')).toEqual({ continue: true });
  });

  it('parses permissionDecision deny → continue=false', () => {
    const raw = JSON.stringify({
      hookSpecificOutput: {
        permissionDecision: 'deny',
        permissionDecisionReason: 'marker absent',
      },
    });
    const result = parseHookOutput(raw);
    expect(result.continue).toBe(false);
    expect(result.stopReason).toBe('marker absent');
  });

  it('parses additionalContext from hookSpecificOutput', () => {
    const raw = JSON.stringify({
      hookSpecificOutput: {
        additionalContext: 'vault context here',
      },
    });
    const result = parseHookOutput(raw);
    expect(result.continue).toBe(true);
    expect(result.additionalContext).toBe('vault context here');
  });

  it('parses systemMessage as additionalContext', () => {
    const raw = JSON.stringify({
      systemMessage: 'warning about something',
    });
    const result = parseHookOutput(raw);
    expect(result.continue).toBe(true);
    expect(result.additionalContext).toBe('warning about something');
  });

  it('returns continue=true for unrecognized JSON shape', () => {
    const raw = JSON.stringify({ unknown: 'field' });
    expect(parseHookOutput(raw)).toEqual({ continue: true });
  });

  it('uses default stopReason when permissionDecisionReason is absent', () => {
    const raw = JSON.stringify({
      hookSpecificOutput: {
        permissionDecision: 'deny',
      },
    });
    const result = parseHookOutput(raw);
    expect(result.continue).toBe(false);
    expect(result.stopReason).toBe('Hook denied operation');
  });
});

describe('buildEventPayload', () => {
  it('builds SessionStart payload', () => {
    const payload = buildEventPayload('SessionStart', ctx);
    expect(payload).toEqual({
      hook_event_name: 'SessionStart',
      cwd: '/tmp/test-group',
      session_id: 'sess-123',
    });
  });

  it('builds UserPromptSubmit payload with prompt', () => {
    const payload = buildEventPayload('UserPromptSubmit', ctx);
    expect(payload).toEqual({
      hook_event_name: 'UserPromptSubmit',
      cwd: '/tmp/test-group',
      session_id: 'sess-123',
      prompt: 'hello world',
    });
  });

  it('builds Stop payload', () => {
    const payload = buildEventPayload('Stop', ctx);
    expect(payload).toEqual({
      hook_event_name: 'Stop',
      cwd: '/tmp/test-group',
      session_id: 'sess-123',
    });
  });

  it('uses empty string for missing sessionId', () => {
    const noSession = { ...ctx, sessionId: undefined };
    const payload = buildEventPayload('SessionStart', noSession);
    expect(payload.session_id).toBe('');
  });

  it('uses empty string for missing prompt on UserPromptSubmit', () => {
    const noPrompt = { ...ctx, prompt: undefined };
    const payload = buildEventPayload('UserPromptSubmit', noPrompt);
    expect(payload.prompt).toBe('');
  });
});
