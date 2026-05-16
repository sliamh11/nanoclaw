/**
 * Reconcile all expected hooks in .claude/settings.json.
 *
 * This catches the backlog: any hooks added by past PRs that didn't have
 * migration files. It checks ALL hooks that should exist today and merges
 * in any missing ones without removing user additions.
 *
 * Future PRs adding hooks should ship their own 000N migration. This one
 * covers everything up to and including the migration-system PR.
 */
import fs from 'node:fs';
import path from 'node:path';

export const id = '0002';
export const title = 'Reconcile all expected settings.json hooks';
export const type = 'auto';

const EXPECTED_HOOKS = {
  SessionStart: [
    {
      hooks: [
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" session-init'", timeout: 3 },
      ],
    },
  ],
  UserPromptSubmit: [
    {
      hooks: [
        { type: 'command', command: "bash -c 'python3 \"${CLAUDE_PROJECT_DIR:-.}/scripts/memory_retrieval_hook.py\"'", timeout: 5 },
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" migration-nudge'", timeout: 3 },
      ],
    },
  ],
  PreToolUse: [
    {
      matcher: 'Write|Edit|MultiEdit|apply_patch|ExitPlanMode',
      hooks: [
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" plan-review-gate'", timeout: 5 },
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/tdd-test-lock.sh\"'", timeout: 3 },
      ],
    },
    {
      matcher: 'ExitPlanMode|Task|Agent',
      hooks: [
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" plan-mode-invalidator'", timeout: 3 },
      ],
    },
    {
      matcher: 'Bash',
      hooks: [
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" code-review-gate'", timeout: 5 },
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" verification-gate'", timeout: 5 },
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" admin-merge-gate'", timeout: 5 },
      ],
    },
  ],
  PostToolUse: [
    {
      matcher: 'Write|Edit|MultiEdit|apply_patch',
      hooks: [
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" code-review-invalidator'", timeout: 3 },
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" threat-model-gate'", timeout: 3 },
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" path-leak-detector'", timeout: 5 },
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" verification-invalidator'", timeout: 3 },
      ],
    },
    {
      matcher: 'Agent',
      hooks: [
        { type: 'command', command: "bash -c '\"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/warden-shim.sh\" warden-verdict-tracker'", timeout: 5 },
      ],
    },
  ],
};

function hookSignature(hook) {
  return hook.command || hook.script || '';
}

function findMatchingGroup(existingGroups, expectedGroup) {
  const expectedMatcher = expectedGroup.matcher || '';
  return existingGroups.find(g => (g.matcher || '') === expectedMatcher);
}

export function check({ root }) {
  const settingsPath = path.join(root, '.claude', 'settings.json');
  if (!fs.existsSync(settingsPath)) return false;

  let settings;
  try {
    settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
  } catch {
    return false;
  }

  const hooks = settings.hooks || {};

  for (const [event, expectedGroups] of Object.entries(EXPECTED_HOOKS)) {
    const existingGroups = hooks[event] || [];
    for (const expectedGroup of expectedGroups) {
      const match = findMatchingGroup(existingGroups, expectedGroup);
      if (!match) return false;

      const existingCommands = new Set((match.hooks || []).map(hookSignature));
      for (const expectedHook of expectedGroup.hooks || []) {
        if (!existingCommands.has(hookSignature(expectedHook))) return false;
      }
    }
  }
  return true;
}

export function apply({ root }) {
  const settingsPath = path.join(root, '.claude', 'settings.json');
  let settings;
  try {
    settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
  } catch (err) {
    if (fs.existsSync(settingsPath)) {
      throw new Error(`settings.json exists but is corrupt: ${err.message}`);
    }
    settings = {};
  }

  settings.hooks ??= {};

  for (const [event, expectedGroups] of Object.entries(EXPECTED_HOOKS)) {
    settings.hooks[event] ??= [];
    const existingGroups = settings.hooks[event];

    for (const expectedGroup of expectedGroups) {
      let match = findMatchingGroup(existingGroups, expectedGroup);

      if (!match) {
        // Add the entire group
        existingGroups.push(JSON.parse(JSON.stringify(expectedGroup)));
        continue;
      }

      // Merge missing hooks into existing group
      match.hooks ??= [];
      const existingCommands = new Set(match.hooks.map(hookSignature));
      for (const expectedHook of expectedGroup.hooks || []) {
        if (!existingCommands.has(hookSignature(expectedHook))) {
          match.hooks.push(JSON.parse(JSON.stringify(expectedHook)));
        }
      }
    }
  }

  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
}
