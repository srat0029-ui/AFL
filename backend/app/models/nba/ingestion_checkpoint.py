"""Checkpoint for the date-by-date schedule sync: one row per (source,
league calendar date) recording that the date was fetched and what came
back.

Its only job is to make a multi-season backfill resumable. A past date
whose games were all settled (final or cancelled, or no games at all) when
it was fetched is `is_settled` and is not requested again on a re-run; a
date with anything still scheduled, in progress or postponed is always
requested again. This is bookkeeping about OUR fetching, not data about the
NBA — nothing reads it for modelling.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class NbaScheduleSyncDate(TimestampMixin, Base):
    __tablename__ = "nba_schedule_sync_dates"
    __table_args__ = (UniqueConstraint("source", "game_date", name="uq_nba_schedule_sync_date_source_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    games_kept: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<NbaScheduleSyncDate {self.source} {self.game_date} settled={self.is_settled}>"
