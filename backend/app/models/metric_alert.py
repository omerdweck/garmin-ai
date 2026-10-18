"""
What is normal for this athlete, and when they stepped outside it.

MetricBaseline - mean and spread of one metric over the athlete's own
                 recent weeks. "Resting HR 58" means nothing on its own;
                 58 against a usual 51 is the whole story.
MetricAlert    - one episode of a rule firing: when it started, when it was
                 last seen, when it ended. An episode is announced once,
                 however many days it lasts - which is what keeps "your HRV
                 is still low" from becoming a daily message.
"""

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class MetricBaseline(SQLModel, table=True):
    __tablename__ = "metric_baseline"
    __table_args__ = (UniqueConstraint("user_id", "metric", name="uq_metric_baseline_user_metric"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    metric: str
    mean: float
    std: float
    samples: int
    window_start: date_type
    window_end: date_type
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MetricAlert(SQLModel, table=True):
    __tablename__ = "metric_alert"
    # At most one open episode per rule - the database says so, not just
    # the code, so a bug cannot open a second one and announce it again.
    __table_args__ = (
        Index(
            "uq_metric_alert_open",
            "user_id",
            "rule",
            unique=True,
            postgresql_where=text("ended_on IS NULL"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    rule: str
    started_on: date_type
    last_seen_on: date_type
    ended_on: Optional[date_type] = None
    # The numbers behind it, as of the last day it fired.
    details: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    # The coach event that announced it; None = not announced yet.
    event_id: Optional[int] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
