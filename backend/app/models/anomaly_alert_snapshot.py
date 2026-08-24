"""Frozen anomaly-alert snapshots (B2B Market Anomaly / Trading QA Engine,
item 8) — the prospective-evaluation counterpart to PricingSnapshot, same
discipline: freeze an alert's full content the moment it's detected for a
still-future match, never rewrite it, and only ever ADD the later-settled
evaluation fields once (all nullable until settled).

Uniqueness mirrors PricingSnapshot's identity idea, extended with
alert_type/reason_code (the same market can produce more than one alert
type, and re-running the detector before kickoff must not duplicate an
already-frozen finding at the same identity): a genuinely NEW finding at
this identity (e.g. a new reason_code because the divergence grew) gets
its own row; the same one re-detected is a no-op (see
anomaly_snapshot_service.py's freeze_anomaly_alerts).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class AnomalyAlertSnapshot(Base, TimestampMixin):
    __tablename__ = "anomaly_alert_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "match_id", "alert_type", "market_type", "selection", "threshold", "line_value", "reason_code",
            name="uq_anomaly_alert_snapshot_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)

    alert_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(String(1000), nullable=False)

    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    selection: Mapped[str | None] = mapped_column(String(64), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_fair_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_consensus_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lineup_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    context_state: Mapped[str | None] = mapped_column(String(500), nullable=True)
    freshness: Mapped[str | None] = mapped_column(String(16), nullable=True)

    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- later-settled prospective evaluation (item 8) — all nullable
    # until settled, written exactly once, never revisited after that. ---
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consensus_moved_toward_model: Mapped[bool | None] = mapped_column(nullable=True)
    outlier_converged: Mapped[bool | None] = mapped_column(nullable=True)
    stale_market_repriced: Mapped[bool | None] = mapped_column(nullable=True)
    curve_anomaly_resolved: Mapped[bool | None] = mapped_column(nullable=True)
    evaluation_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])
    team: Mapped["Team | None"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<AnomalyAlertSnapshot match={self.match_id} {self.alert_type} {self.reason_code}>"
