import { describe, it, expect } from 'vitest';
import { emojiToSignal } from './reaction-signal.js';

describe('emojiToSignal', () => {
  it.each([
    '👍',
    '❤️',
    '🎉',
    '🔥',
    '👏',
    '🚀',
    '⭐',
    '✅',
    '😍',
    '🙏',
    '💯',
    '🤩',
  ])('maps %s → positive', (emoji) => {
    expect(emojiToSignal(emoji)).toBe('positive');
  });

  it.each(['👎', '😞', '😢', '😡', '🤦', '💔', '❌', '🙁', '😕'])(
    'maps %s → negative',
    (emoji) => {
      expect(emojiToSignal(emoji)).toBe('negative');
    },
  );

  it.each(['😂', '🤔', '👀', '🧐', '🙃'])(
    'returns null for neutral emoji %s',
    (emoji) => {
      expect(emojiToSignal(emoji)).toBeNull();
    },
  );

  it('returns null for empty string', () => {
    expect(emojiToSignal('')).toBeNull();
  });

  it('returns null for non-emoji text', () => {
    expect(emojiToSignal('hello')).toBeNull();
  });
});
