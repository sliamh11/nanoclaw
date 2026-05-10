import { describe, it, expect, vi } from 'vitest';

import { routeOutbound, findChannel } from './router.js';
import type { Channel } from './types.js';

function makeChannel(
  name: string,
  ownedJids: string[],
  connected: boolean,
): Channel {
  return {
    name,
    connect: vi.fn(),
    disconnect: vi.fn(),
    isConnected: vi.fn(() => connected),
    ownsJid: vi.fn((jid: string) => ownedJids.includes(jid)),
    sendMessage: vi.fn(() => Promise.resolve()),
  };
}

describe('routeOutbound', () => {
  it('should throw when no connected channel matches JID', () => {
    const channels: Channel[] = [
      makeChannel('whatsapp', ['group-a@g.us'], true),
    ];
    expect(() => routeOutbound(channels, 'unknown@g.us', 'hello')).toThrow(
      'No channel for JID: unknown@g.us',
    );
  });

  it('should skip disconnected channels when routing outbound', () => {
    const jid = 'group-b@g.us';
    const channels: Channel[] = [
      makeChannel('connected', ['group-a@g.us'], true),
      makeChannel('disconnected', [jid], false),
    ];
    expect(() => routeOutbound(channels, jid, 'hello')).toThrow(
      `No channel for JID: ${jid}`,
    );
  });

  it('should route to the correct channel when multiple are connected', async () => {
    const jidA = 'group-a@g.us';
    const jidB = 'group-b@g.us';
    const channelA = makeChannel('channelA', [jidA], true);
    const channelB = makeChannel('channelB', [jidB], true);

    await routeOutbound([channelA, channelB], jidB, 'hello');

    expect(channelB.sendMessage).toHaveBeenCalledWith(jidB, 'hello');
    expect(channelA.sendMessage).not.toHaveBeenCalled();
  });
});

describe('findChannel', () => {
  it('should return the channel matching the JID prefix', () => {
    const jid = 'group-x@g.us';
    const match = makeChannel('telegram', [jid], true);
    const other = makeChannel('whatsapp', ['other@g.us'], true);

    const result = findChannel([other, match], jid);

    expect(result).toBe(match);
  });

  it('should return undefined when no channel owns the JID', () => {
    const channels: Channel[] = [
      makeChannel('whatsapp', ['group-a@g.us'], true),
    ];
    expect(findChannel(channels, 'nobody@g.us')).toBeUndefined();
  });
});
