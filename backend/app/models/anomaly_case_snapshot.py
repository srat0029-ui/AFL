"""Prospective case-level freeze (Prospective Alert Validation stage, item
1) — the case-level counterpart to app/models/anomaly_alert_snapshot.py's
per-ALERT snapshot from the prior stage. Every field in the FROZEN group
below is written exactly once, at the moment a case first reaches HIGH
PRIORITY or CRITICAL tier, and is never touched again — the evidentiary
record of "what did we believe, and why, before kickoff." The ROLLING
group is the one deliberate exception: it may be refreshed on each
detector pass while the match is still SCHEDULED (representing "the latest
pre-kickoff snapshot," item 2's own second data point), and is itself
locked the moment the case is settled. The OUTCOME group is written
exactly once, after the match completes and enough evidence exists to
classify what happened (see app/market_monitor/outcome_taxonomy.py).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class AnomalyCaseSnapshot(Base, TimestampMixin):
    __tablename__ = "anomaly_case_snapshots"
    __table_args__ = (UniqueConstraint("case_id", name="uq_anomaly_case_snapshot_case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)

    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    selection: Mapped[str | None] = mapped_column(String(64), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- FROZEN at first HIGH_PRIORITY/CRITICAL detection — never rewritten ---
    # capture_mode records whether the match was genuinely still SCHEDULED at
    # the moment of freeze ("prospective", the only path run_live_cycle's
    # automatic freezing can produce) or already COMPLETED ("retrospective",
    # only ever produced by a deliberate historical backfill script) - the
    # Genuine Prospective Operation stage's item 5 boundary: retrospective
    # rows must never count toward primary effectiveness metrics.
    capture_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="prospective")
    # Kennedy-pattern research tag (item 8): set once at freeze when a
    # single-book outlier is present AND the model-vs-market gap remains at
    # or above the detector's own DIVERGENCE_CRITICAL_PP even after
    # excluding that outlier's price - see root_cause.compute_research_category.
    research_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Per-player historical-volume/history-size metadata (item 6), frozen at
    # case creation from the same persisted projection row case_audit.py
    # already reads recent_form from - null for team-level (h2h) cases.
    player_prior_games: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_season_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    player_historical_volume_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alert_types: Mapped[list] = mapped_column(JSON, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False)
    priority_components: Mapped[list] = mapped_column(JSON, nullable=False)
    model_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market_consensus_probability_at_freeze: Mapped[float | None] = mapped_column(Float, nullable=True)
    bookmaker_prices_at_freeze: Mapped[list] = mapped_column(JSON, nullable=False)
    n_bookmakers_at_freeze: Mapped[int] = mapped_column(Integer, nullable=False)
    earliest_quote_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_quote_at_freeze: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lineup_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    context_state: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model_risk_flags: Mapped[list] = mapped_column(JSON, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    persistence_n_snapshots_at_freeze: Mapped[int] = mapped_column(Integer, nullable=False)
    time_to_kickoff_hours_at_freeze: Mapped[float | None] = mapped_column(Float, nullable=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- ROLLING while still SCHEDULED and unsettled — "latest pre-kick snapshot" ---
    market_consensus_probability_latest: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_probability_latest: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_bookmakers_latest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    n_prekickoff_refreshes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_state_latest: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hours_to_kickoff_latest: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- OUTCOME — written exactly once, after settlement ---
    outcome_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    outlier_converged: Mapped[bool | None] = mapped_column(nullable=True)
    stale_market_repriced: Mapped[bool | None] = mapped_column(nullable=True)
    curve_anomaly_resolved: Mapped[bool | None] = mapped_column(nullable=True)
    actual_stat_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_outcome_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to_resolution_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])
    team: Mapped["Team | None"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<AnomalyCaseSnapshot {self.case_id} resolved={self.resolved_at is not None}>"
