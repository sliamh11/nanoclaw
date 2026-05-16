/**
 * Bootstrap migration — initializes the migration state file.
 * This is a no-op; its existence confirms the system is wired up.
 */
export const id = '0001';
export const title = 'Initialize migration system';
export const type = 'auto';

export function check() {
  return true;
}

export function apply() {}
