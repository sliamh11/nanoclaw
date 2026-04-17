/**
 * Maps an emoji reaction to a user feedback signal.
 *
 * Channel-agnostic — WhatsApp, Telegram, and future channels funnel their
 * reaction events through this mapper before reaching the evolution loop.
 * Mirrors the return shape of detectUserSignal() so both signal sources
 * converge on the same user_signal column in the Python evolution store.
 */

const POSITIVE_EMOJIS: ReadonlySet<string> = new Set([
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
]);

const NEGATIVE_EMOJIS: ReadonlySet<string> = new Set([
  '👎',
  '😞',
  '😢',
  '😡',
  '🤦',
  '💔',
  '❌',
  '🙁',
  '😕',
]);

export function emojiToSignal(emoji: string): 'positive' | 'negative' | null {
  if (!emoji) return null;
  if (POSITIVE_EMOJIS.has(emoji)) return 'positive';
  if (NEGATIVE_EMOJIS.has(emoji)) return 'negative';
  return null;
}
