"""Operational-run record for `python -m app.player_modelling.cli
run-live-cycle` (Section 13 of the live-operations stage brief) — a
persisted history of "what happened the last few times we ran the live
cycle," so the Live Status UI can show real recent-run history rather than
only "current state." One row per invocation; never updated in place after
`finished_at` is set (append-only, matching this project's convention for
history tables). No secrets are stored here — `steps` is a small JSON list
of {step, status, detail} entries, `detail` is always a short human-readable
message, never a raw exception/traceback or a credential.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin

# Section 2's three-way failure classification, plus "success" — used both
# per-step (in `steps`) and for the run's own `overall_status`.
STEP_SUCCESS = "success"
STEP_WARNING = "warning"
STEP_RECOVERABLE_FAILURE = "recoverable_failure"
STEP_BLOCKING_FAILURE = "blocking_failure"

RUN_OK = "ok"  # every step succeeded or only warned
RUN_PARTIAL = "partial"  # at least one recoverable failure, but the cycle still made progress
RUN_BLOCKED = "blocked"  # a blocking failure occurred — see live_cycle.py for exactly when that's declared


class LiveCycleRun(TimestampMixin, Base):
    __tablename__ = "live_cycle_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overall_status: Mapped[str] = mapped_column(String(16), nullable=False)  # RUN_OK | RUN_PARTIAL | RUN_BLOCKED

    # [{"step": "refresh_fixtures", "status": "success", "detail": "..."}, ...]
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    odds_credits_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    odds_credits_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matches_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quotes_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observations_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observations_settled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<LiveCycleRun {self.run_at.isoformat()} status={self.overall_status}>"
