"""Centralized timezone-aware datetime helpers for scripts/.

Two policies, both enforced by the docs/decisions/error-discipline.md
"PR #8 addendum: datetime-TZ policy" section:

  utc_now()   — for INTERNAL timestamps (db rows, log retention cutoffs,
                age comparisons against st_mtime, ISO frontmatter values
                that should be moment-correct across timezones).
  local_now() — for USER-FACING strings (date stamps, filenames, weekday
                checks, day-grouped indexing — Liam expects "today" to
                match his calendar day in Asia/Jerusalem).

Picking the wrong one is silent. A naive datetime around midnight will
produce off-by-one calendar days, and mixing aware + naive raises
TypeError at runtime. Always pick one explicitly; never call bare
datetime.now() in scripts/.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEUS_TZ = ZoneInfo("Asia/Jerusalem")


def utc_now() -> datetime:
    """Internal timestamps and comparisons against UTC st_mtime."""
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    """User-facing strings, filenames, weekday checks."""
    return datetime.now(DEUS_TZ)
