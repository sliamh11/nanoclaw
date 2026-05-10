import { beforeEach, describe, expect, it, vi } from 'vitest';

// vi.hoisted runs before vi.mock factories, making these refs available inside them.
const { mockCallTool, mockConnect, mockClose, capturedHandlers } = vi.hoisted(
  () => {
    const capturedHandlers: Array<(n: unknown) => void> = [];
    const mockCallTool = vi.fn().mockResolvedValue({});
    const mockConnect = vi.fn().mockResolvedValue(undefined);
    const mockClose = vi.fn().mockResolvedValue(undefined);
    return { mockCallTool, mockConnect, mockClose, capturedHandlers };
  },
);

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));

vi.mock('@modelcontextprotocol/sdk/client/stdio.js', () => ({
  // Must use `function`, not arrow, so `new StdioClientTransport(...)` works.
  StdioClientTransport: vi.fn().mockImplementation(function () {}),
}));

vi.mock('@modelcontextprotocol/sdk/client/index.js', () => ({
  // Same rule: arrow functions cannot be called with `new`.
  Client: vi.fn().mockImplementation(function () {
    return {
      callTool: mockCallTool,
      connect: mockConnect,
      close: mockClose,
      setNotificationHandler: function (
        _schema: unknown,
        handler: (n: unknown) => void,
      ) {
        capturedHandlers.push(handler);
      },
    };
  }),
}));

const { McpChannelAdapter } = await import('./mcp-adapter.js');

function makeOpts() {
  return {
    name: 'test-channel',
    command: 'node',
    args: ['server.js'],
    onMessage: vi.fn(),
    onReaction: vi.fn(),
    onChatMetadata: vi.fn(),
    ownsJid: vi.fn().mockReturnValue(false),
  };
}

beforeEach(() => {
  capturedHandlers.length = 0;
  mockCallTool.mockReset();
  mockCallTool.mockResolvedValue({});
  mockConnect.mockReset();
  mockConnect.mockResolvedValue(undefined);
  mockClose.mockReset();
  mockClose.mockResolvedValue(undefined);
});

describe('McpChannelAdapter', () => {
  it('should set connected=true even when get_status tool call fails', async () => {
    mockCallTool.mockRejectedValueOnce(new Error('status check failed'));

    const adapter = new McpChannelAdapter(makeOpts());
    await adapter.connect();

    expect(adapter.isConnected()).toBe(true);
  });

  it('should not crash when notification data is missing chat_id', () => {
    const opts = makeOpts();
    new McpChannelAdapter(opts);
    const handler = capturedHandlers[capturedHandlers.length - 1];

    // data entirely absent — should return early without calling any callback
    expect(() =>
      handler({ params: { logger: 'incoming_message', data: undefined } }),
    ).not.toThrow();
    expect(opts.onMessage).not.toHaveBeenCalled();

    // data present but chat_id missing — should not throw
    expect(() =>
      handler({
        params: {
          logger: 'incoming_message',
          data: { content: 'hello', sender: 'foo@c.us', timestamp: '1' },
        },
      }),
    ).not.toThrow();
  });

  it('should call send_typing on the matching channel', async () => {
    const adapter = new McpChannelAdapter(makeOpts());
    await adapter.setTyping('123@c.us', true);

    expect(mockCallTool).toHaveBeenCalledWith({
      name: 'send_typing',
      arguments: { chat_id: '123@c.us', is_typing: true },
    });
  });

  it('should handle disconnect gracefully', async () => {
    const adapter = new McpChannelAdapter(makeOpts());
    await adapter.connect();
    expect(adapter.isConnected()).toBe(true);

    await adapter.disconnect();
    expect(adapter.isConnected()).toBe(false);
  });
});
