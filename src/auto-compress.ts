import { spawn } from 'child_process';
import { mkdirSync, writeFileSync } from 'fs';
import path from 'path';

import { fireAndForget } from './async/index.js';
import { ASSISTANT_NAME, PROJECT_ROOT } from './config.js';
import { getMessagesSince } from './db.js';
import { logger } from './logger.js';
import { PYTHON_BIN } from './platform.js';
import { resolveVaultPath } from './solutions/index.js';
import type { RegisteredGroup } from './types.js';

const MEMORY_INDEXER_PATH = path.join(
  PROJECT_ROOT,
  'scripts',
  'memory_indexer.py',
);

/**
 * Save the current session's conversation to the vault before an idle reset
 * clears it. Lightweight: no LLM calls, no atom extraction — just raw
 * conversation as a session log + fire-and-forget embedding indexing.
 */
export async function autoCompressSession(
  group: RegisteredGroup,
  chatJid: string,
  effectiveIdleHours: number,
): Promise<void> {
  const vaultPath = resolveVaultPath();
  if (!vaultPath) {
    logger.debug('Auto-compress skipped: no vault configured');
    return;
  }

  // 2× the idle window captures the full session including pre-idle activity
  const sinceTimestamp = new Date(
    Date.now() - effectiveIdleHours * 2 * 3_600_000,
  ).toISOString();

  const messages = getMessagesSince(
    chatJid,
    sinceTimestamp,
    ASSISTANT_NAME,
    500,
    true,
  );

  if (messages.length === 0) {
    return;
  }

  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10);
  const timeStr = now.toISOString().slice(11, 16).replace(':', '');
  const safeFolder = path.basename(group.folder);
  const fileName = `auto-${safeFolder}-${timeStr}.md`;
  const dir = path.join(vaultPath, 'Session-Logs', dateStr);
  const savedPath = path.join(dir, fileName);

  const firstUserMsg = messages.find((m) => !m.is_from_me);
  const tldr = firstUserMsg
    ? firstUserMsg.content.replace(/\n/g, ' ').slice(0, 120)
    : 'auto-compressed session';

  const body = messages
    .map((m) => {
      const time = new Date(m.timestamp).toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
      const sender = m.sender_name || m.sender || 'unknown';
      return `**${sender}** (${time}): ${m.content}`;
    })
    .join('\n\n');

  const content = `---
type: session
date: ${dateStr}
topics: [auto-compress]
tldr: |
  ${tldr}
---

${body}
`;

  mkdirSync(dir, { recursive: true });
  writeFileSync(savedPath, content, 'utf-8');

  fireAndForget(
    () =>
      new Promise<void>((resolve, reject) => {
        const child = spawn(
          PYTHON_BIN,
          [MEMORY_INDEXER_PATH, '--add', savedPath],
          {
            stdio: ['ignore', 'ignore', 'pipe'],
          },
        );
        child.on('close', (code) =>
          code === 0 ? resolve() : reject(new Error(`indexer exit ${code}`)),
        );
        child.on('error', reject);
      }),
    { name: 'auto-compress-index' },
  );

  logger.info(
    { group: group.name, path: savedPath },
    'Auto-compress: session saved',
  );
}
