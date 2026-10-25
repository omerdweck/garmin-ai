"""
Progress of the one-time import of a user's past year from Garmin.

One row per user. The import walks backwards one day at a time, from the
newest day towards `oldest_date`, so the Mini App's week and month views
fill in first and the year view last. Every finished day moves `next_date`
back by one and is committed on its own: a crash, a deploy or Garmin
throttling in the middle costs nothing, the next run continues from
`next_date`.

Days that already have a DailyMetric row (from the routine syncs) are
skipped without asking Garmin - the import only fills holes.
"""

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

STATUS_PENDING = "pending"   # created, nothing fetched yet
STATUS_RUNNING = "running"   # partly done; resumes on the next night run
STATUS_DONE = "done"
STATUS_PAUSED = "paused"     # Garmin rejected the login; waits for a relink


class HistoryImport(SQLModel, table=True):
    __tablename__ = "history_import"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)

    # The range, fixed when the row is created: from the day before it
    # started back to `oldest_date`. Fixed rather than "a year before today"
    # so a multi-night import has a stable 100% to count towards.
    newest_date: date_type
    oldest_date: date_type
    # The next day to fetch. Starts at newest_date and moves backwards;
    # the import is complete once it passes oldest_date.
    next_date: date_type

    # Activities come from a paged list, not per day, so they are imported
    # once, in the first run, before the days.
    activities_done: bool = Field(default=False)
    activities_imported: int = Field(default=0)

    days_total: int
    days_done: int = Field(default=0)
    days_fetched: int = Field(default=0)  # days that needed Garmin (not skipped)

    status: str = Field(default=STATUS_PENDING, index=True)
    last_error: Optional[str] = None

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def percent(self) -> int:
        if self.status == STATUS_DONE or self.days_total <= 0:
            return 100
        return min(99, round(100 * self.days_done / self.days_total))
