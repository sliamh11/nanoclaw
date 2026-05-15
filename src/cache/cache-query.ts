/**
 * Read-only query interface for the Google Calendar SQLite cache.
 * All queries filter orphaned_at IS NULL (per no-db-deletion.md).
 * FTS5 content-table queries JOIN events to enforce soft-delete filter —
 * FTS indexes do not automatically exclude orphaned rows.
 */

import Database from 'better-sqlite3';

export interface CachedEvent {
  id: string;
  summary: string | null;
  start_time: string;
  end_time: string;
  location: string | null;
  description: string | null;
  html_link: string | null;
  organizer: string | null;
  status: string;
  updated_at: string;
  synced_at: string;
}

export interface SearchOptions {
  limit?: number;
}

export interface BusyWindow {
  start_time: string;
  end_time: string;
  summary: string | null;
}

/**
 * Full-text search over cached events using the FTS5 index.
 * JOIN to events enforces orphaned_at IS NULL — FTS indexes track all rows
 * including orphaned ones; the JOIN is the correct filter point.
 */
export function searchEvents(
  db: Database.Database,
  query: string,
  options: SearchOptions = {},
): CachedEvent[] {
  const limit = options.limit ?? 20;
  return db
    .prepare(
      `
    SELECT e.id, e.summary, e.start_time, e.end_time, e.location,
           e.description, e.html_link, e.organizer, e.status,
           e.updated_at, e.synced_at
    FROM events_fts
    JOIN events e ON events_fts.rowid = e.rowid
    WHERE events_fts MATCH ? AND e.orphaned_at IS NULL
    ORDER BY rank LIMIT ?
  `,
    )
    .all(query, limit) as CachedEvent[];
}

/** Return upcoming events within a look-ahead window, sorted by start time. */
export function getUpcomingEvents(
  db: Database.Database,
  days: number = 7,
): CachedEvent[] {
  const now = new Date().toISOString();
  const until = new Date(
    Date.now() + Math.max(1, Math.floor(days)) * 24 * 60 * 60 * 1000,
  ).toISOString();
  return db
    .prepare(
      `
    SELECT id, summary, start_time, end_time, location, description,
           html_link, organizer, status, updated_at, synced_at
    FROM events
    WHERE start_time >= ? AND start_time <= ? AND orphaned_at IS NULL
    ORDER BY start_time ASC
  `,
    )
    .all(now, until) as CachedEvent[];
}

/**
 * Return all events overlapping [startDate, endDate].
 * Useful for busy-window detection (event.start < endDate AND event.end > startDate).
 */
export function getBusyWindows(
  db: Database.Database,
  startDate: string,
  endDate: string,
): BusyWindow[] {
  return db
    .prepare(
      `
    SELECT start_time, end_time, summary
    FROM events
    WHERE start_time < ? AND end_time > ?
      AND orphaned_at IS NULL AND status != 'cancelled'
    ORDER BY start_time ASC
  `,
    )
    .all(endDate, startDate) as BusyWindow[];
}
