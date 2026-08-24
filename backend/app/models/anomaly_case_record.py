"""Case-level persistence + lifecycle tracking (Alert Precision + Trader
Prioritisation stage, items 4 & 10) — deliberately a SEPARATE table from
app/models/anomaly_alert_snapshot.py's AnomalyAlertSnapshot, which exists
for a narrower purpose (item 8's prior-stage "freeze evidence before
kickoff, evaluate once after" prospective dataset, scoped to SCHEDULED
matches only and never rewritten). This table instead tracks ongoing
detection FREQUENCY for any case regardless of match status — how many
times has this exact case_key been observed across detector runs, when did
it first/last appear — which is what item 4's persistence score and item
10's lifecycle status both need. Only first_seen_at/case identity are
truly immutable; last_seen_at/n_snapshots/resolved_at/manual_status are
expected to change as this stage's own record-keeping, not a violation of
the prior stage's "never rewritten" evidence discipline (that discipline
still applies unchanged to AnomalyAlertSnapshot itself).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class AnomalyCaseRecord(Base, TimestampMixin):
    __tablename__ = "anomaly_case_records"
    __table_args__ = (UniqueConstraint("case_key", name="uq_anomaly_case_record_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    n_snapshots: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Future/manual (item 10) — deliberately unauthenticated for now (the
    # item's own explicit allowance): "reviewed" | "acknowledged" |
    # "dismissed" | None. Never set by any detector/scoring code.
    manual_status: Mapped[str | None] = mapped_column(String(24), nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])

    def __repr__(self) -> str:
        return f"<AnomalyCaseRecord {self.case_key} n_snapshots={self.n_snapshots}>"
