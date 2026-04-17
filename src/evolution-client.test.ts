import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock child_process BEFORE importing the module under test.
vi.mock('child_process', () => ({
  spawn: vi.fn(),
  execFile: vi.fn(),
}));

import { spawn } from 'child_process';
import { logReactionSignal } from './evolution-client.js';

const mockSpawn = vi.mocked(spawn);

function _fakeChild() {
  return {
    stderr: { on: vi.fn() },
    on: vi.fn(),
  } as unknown as ReturnType<typeof spawn>;
}

beforeEach(() => {
  mockSpawn.mockReset();
  mockSpawn.mockImplementation(() => _fakeChild());
  delete process.env.EVOLUTION_ENABLED;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('logReactionSignal', () => {
  it('spawns log_interaction with positive signal for 👍', () => {
    logReactionSignal({ emoji: '👍', groupFolder: 'whatsapp_main' });

    expect(mockSpawn).toHaveBeenCalledOnce();
    const args = mockSpawn.mock.calls[0][1] as string[];
    const payload = JSON.parse(args[args.length - 1]);
    expect(payload.user_signal).toBe('positive');
    expect(payload.group_folder).toBe('whatsapp_main');
    expect(payload.prompt).toBe('[reaction]');
    expect(payload.response).toBe('');
  });

  it('spawns log_interaction with negative signal for 👎', () => {
    logReactionSignal({ emoji: '👎', groupFolder: 'telegram_main' });

    expect(mockSpawn).toHaveBeenCalledOnce();
    const args = mockSpawn.mock.calls[0][1] as string[];
    const payload = JSON.parse(args[args.length - 1]);
    expect(payload.user_signal).toBe('negative');
  });

  it('does NOT spawn for neutral emoji', () => {
    logReactionSignal({ emoji: '😂', groupFolder: 'whatsapp_main' });
    expect(mockSpawn).not.toHaveBeenCalled();
  });

  it('does NOT spawn for empty emoji (reaction-removed)', () => {
    logReactionSignal({ emoji: '', groupFolder: 'whatsapp_main' });
    expect(mockSpawn).not.toHaveBeenCalled();
  });

  it('forwards sessionId when provided', () => {
    logReactionSignal({
      emoji: '❤️',
      groupFolder: 'whatsapp_main',
      sessionId: 'sess_abc123',
    });

    const args = mockSpawn.mock.calls[0][1] as string[];
    const payload = JSON.parse(args[args.length - 1]);
    expect(payload.session_id).toBe('sess_abc123');
    expect(payload.user_signal).toBe('positive');
  });

  it('generates a fresh UUID id for each call', () => {
    logReactionSignal({ emoji: '🔥', groupFolder: 'whatsapp_main' });
    logReactionSignal({ emoji: '🔥', groupFolder: 'whatsapp_main' });

    const id1 = JSON.parse(
      (mockSpawn.mock.calls[0][1] as string[]).slice(-1)[0],
    ).id;
    const id2 = JSON.parse(
      (mockSpawn.mock.calls[1][1] as string[]).slice(-1)[0],
    ).id;
    expect(id1).not.toBe(id2);
    expect(id1).toMatch(/^[0-9a-f-]{36}$/);
  });
});
