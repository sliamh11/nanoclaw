import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { NewMessage, RegisteredGroup } from './types.js';

const mockMkdirSync = vi.fn();
const mockWriteFileSync = vi.fn();
vi.mock('fs', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    mkdirSync: mockMkdirSync,
    writeFileSync: mockWriteFileSync,
  };
});

const mockResolveVaultPath = vi.fn<() => string | null>();
vi.mock('./solutions/index.js', () => ({
  resolveVaultPath: mockResolveVaultPath,
}));

const mockGetMessagesSince = vi.fn<() => NewMessage[]>();
vi.mock('./db.js', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, getMessagesSince: mockGetMessagesSince };
});

const mockFireAndForget = vi.fn();
vi.mock('./async/index.js', () => ({
  fireAndForget: mockFireAndForget,
}));

vi.mock('./logger.js', () => ({
  logger: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

const { autoCompressSession } = await import('./auto-compress.js');
const { logger } = await import('./logger.js');

function makeGroup(folder = 'whatsapp_main'): RegisteredGroup {
  return {
    name: 'Test Group',
    folder,
    channels: [],
    isControlGroup: false,
  } as unknown as RegisteredGroup;
}

function makeMessage(overrides: Partial<NewMessage> = {}): NewMessage {
  return {
    id: '1',
    chat_jid: 'test@s.whatsapp.net',
    sender: '123@s.whatsapp.net',
    sender_name: 'Alice',
    content: 'Hello, this is a test message',
    timestamp: '2026-05-12T10:00:00.000Z',
    is_from_me: false,
    ...overrides,
  } as NewMessage;
}

describe('autoCompressSession', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns silently when no vault is configured', async () => {
    mockResolveVaultPath.mockReturnValue(null);

    await autoCompressSession(makeGroup(), 'test@jid', 8);

    expect(mockWriteFileSync).not.toHaveBeenCalled();
    expect(logger.debug).toHaveBeenCalledWith(
      'Auto-compress skipped: no vault configured',
    );
  });

  it('returns silently when conversation is empty', async () => {
    mockResolveVaultPath.mockReturnValue('/tmp/vault');
    mockGetMessagesSince.mockReturnValue([]);

    await autoCompressSession(makeGroup(), 'test@jid', 8);

    expect(mockWriteFileSync).not.toHaveBeenCalled();
  });

  it('writes session log with correct path and YAML frontmatter', async () => {
    mockResolveVaultPath.mockReturnValue('/tmp/vault');
    mockGetMessagesSince.mockReturnValue([makeMessage()]);

    await autoCompressSession(makeGroup(), 'test@jid', 8);

    expect(mockMkdirSync).toHaveBeenCalledWith(
      expect.stringContaining('Session-Logs'),
      { recursive: true },
    );
    expect(mockWriteFileSync).toHaveBeenCalledTimes(1);
    const [filePath, content] = mockWriteFileSync.mock.calls[0] as [
      string,
      string,
      string,
    ];
    expect(filePath).toMatch(/auto-whatsapp_main-\d{4}\.md$/);
    expect(content).toContain('type: session');
    expect(content).toContain('topics: [auto-compress]');
    expect(content).toContain('tldr:');
    expect(content).toContain('date:');
  });

  it('includes both user and bot messages in output', async () => {
    mockResolveVaultPath.mockReturnValue('/tmp/vault');
    mockGetMessagesSince.mockReturnValue([
      makeMessage({
        sender_name: 'Alice',
        content: 'Hi there',
        is_from_me: false,
      }),
      makeMessage({ sender_name: 'Deus', content: 'Hello!', is_from_me: true }),
    ]);

    await autoCompressSession(makeGroup(), 'test@jid', 8);

    const content = mockWriteFileSync.mock.calls[0]![1] as string;
    expect(content).toContain('**Alice**');
    expect(content).toContain('**Deus**');
    expect(content).toContain('Hi there');
    expect(content).toContain('Hello!');
  });

  it('resolves successfully even if indexer spawn would fail', async () => {
    mockResolveVaultPath.mockReturnValue('/tmp/vault');
    mockGetMessagesSince.mockReturnValue([makeMessage()]);

    await expect(
      autoCompressSession(makeGroup(), 'test@jid', 8),
    ).resolves.toBeUndefined();

    expect(mockFireAndForget).toHaveBeenCalledWith(expect.any(Function), {
      name: 'auto-compress-index',
    });
  });

  it('throws when file write fails', async () => {
    mockResolveVaultPath.mockReturnValue('/tmp/vault');
    mockGetMessagesSince.mockReturnValue([makeMessage()]);
    mockWriteFileSync.mockImplementation(() => {
      throw new Error('EACCES: permission denied');
    });

    await expect(
      autoCompressSession(makeGroup(), 'test@jid', 8),
    ).rejects.toThrow('EACCES: permission denied');
  });
});

describe('getMessagesSince includeBotMessages', () => {
  it('excludes bot messages by default (backward compat)', async () => {
    const { getMessagesSince } = await import('./db.js');
    const real = vi.mocked(getMessagesSince);

    real.mockReturnValue([makeMessage()]);
    const result = real('jid', '2026-01-01', 'Deus', 50);

    expect(result).toHaveLength(1);
    expect(real).toHaveBeenCalledWith('jid', '2026-01-01', 'Deus', 50);
  });
});
