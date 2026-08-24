"""Genuine Prospective Market-Monitor Operation stage, items 2-4: one
append-only row per (case, time-to-kickoff stage) captured while a match is
still SCHEDULED. Never rewritten once written — a case's follow-up trail is
itself evidentiary history, same discipline as AnomalyCaseSnapshot's FROZEN
group. The stage_bucket unique constraint is what "reuse existing refresh
cadence rather than adding wasteful polling" means in practice: whichever
live-cycle run first observes a case at a given stage writes that stage's
row; later runs within the same stage are no-ops."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin

STAGE_24H_PLUS = "24h_plus"
STAGE_6_24H = "6_24h"
STAGE_1_6H = "1_6h"
STAGE_UNDER_1H = "under_1h"
ALL_STAGE_BUCKETS = (STAGE_24H_PLUS, STAGE_6_24H, STAGE_1_6H, STAGE_UNDER_1H)


class AnomalyCaseFollowUp(Base, TimestampMixin):
    __tablename__ = "anomaly_case_followups"
    __table_args__ = (UniqueConstraint("snapshot_id", "stage_bucket", name="uq_anomaly_case_followup_snapshot_stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("anomaly_case_snapshots.id"), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    stage_bucket: Mapped[str] = mapped_column(String(16), nullable=False)

    hours_to_kickoff: Mapped[float] = mapped_column(Float, nullable=False)
    consensus_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_bookmakers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bookmaker_prices: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    lineup_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    context_state: Mapped[str | None] = mapped_column(String(500), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<AnomalyCaseFollowUp {self.case_id} stage={self.stage_bucket}>"
