import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'fs';

import { RouterState } from './router-state.js';
import {
  getRouterState,
  setRegisteredGroup,
  getAllBackendSessions,
  getAllRegisteredGroups,
} from './db.js';

vi.mock('./config.js', () => ({
  GROUPS_DIR: '/tmp/test-deus-groups',
  DATA_DIR: '/tmp/test-deus-data',
}));

vi.mock('./logger.js', () => ({
  logger: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('./db.js', () => ({
  getRouterState: vi.fn(),
  setRouterState: vi.fn(),
  getAllBackendSessions: vi.fn(() => ({})),
  getAllRegisteredGroups: vi.fn(() => ({})),
  getAllChats: vi.fn(() => []),
  setRegisteredGroup: vi.fn(),
}));

vi.mock('fs', async () => {
  const actual = await vi.importActual<typeof import('fs')>('fs');
  return {
    ...actual,
    default: {
      ...actual,
      mkdirSync: vi.fn(),
    },
  };
});

const mockGetRouterState = vi.mocked(getRouterState);
const mockSetRegisteredGroup = vi.mocked(setRegisteredGroup);
const mockGetAllBackendSessions = vi.mocked(getAllBackendSessions);
const mockGetAllRegisteredGroups = vi.mocked(getAllRegisteredGroups);
const mockMkdirSync = vi.mocked(fs.mkdirSync);

beforeEach(() => {
  vi.clearAllMocks();
  mockGetRouterState.mockReturnValue(undefined);
  mockGetAllBackendSessions.mockReturnValue({});
  mockGetAllRegisteredGroups.mockReturnValue({});
});

describe('RouterState', () => {
  it('should not persist group when folder path escapes groups dir', () => {
    const state = new RouterState();
    state.load();

    state.registerGroup('group@g.us', {
      name: 'Escaped',
      folder: '../../etc',
      trigger: '@bot',
      added_at: '2024-01-01T00:00:00.000Z',
    });

    expect(mockSetRegisteredGroup).not.toHaveBeenCalled();
  });

  it('should reset last_agent_timestamp on corrupt JSON without throwing', () => {
    mockGetRouterState.mockImplementation((key) => {
      if (key === 'last_agent_timestamp') return '{bad-json{{';
      return undefined;
    });

    const state = new RouterState();
    expect(() => state.load()).not.toThrow();
    expect(state.getLastAgentTimestamp('any-group')).toBe('');
  });

  it('should register group and write folder to FS', () => {
    const state = new RouterState();
    state.load();

    const group = {
      name: 'Test Group',
      folder: 'testgroup',
      trigger: '@bot',
      added_at: '2024-01-01T00:00:00.000Z',
    };

    state.registerGroup('group@g.us', group);

    expect(mockSetRegisteredGroup).toHaveBeenCalledWith('group@g.us', group);
    expect(mockMkdirSync).toHaveBeenCalled();
  });

  it('should load and save session state correctly', () => {
    const state = new RouterState();
    const session = {
      backend: 'claude' as const,
      session_id: 'test-session-id',
    };

    state.setSession('my-group', session);

    expect(state.getSession('my-group')).toEqual(session);
    expect(state.getSession('my-group', 'claude')).toEqual(session);
    expect(state.getSession('my-group', 'openai')).toBeUndefined();
  });

  it('should clear session without affecting other groups', () => {
    const state = new RouterState();

    state.setSession('folder-a', {
      backend: 'claude',
      session_id: 'session-a',
    });
    state.setSession('folder-b', {
      backend: 'claude',
      session_id: 'session-b',
    });

    state.clearSession('folder-a');

    expect(state.getSession('folder-a')).toBeUndefined();
    expect(state.getSession('folder-b')).toEqual({
      backend: 'claude',
      session_id: 'session-b',
    });
  });
});
