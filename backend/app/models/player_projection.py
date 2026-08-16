"""Persisted live pre-match player projections — the product-facing
counterpart to app/models/player_model_run.py's PlayerDisposalPrediction /
PlayerGoalPrediction (which store HISTORICAL eval-period predictions for
backtesting). These tables store CURRENT projections for upcoming matches,
one row per (match, player), upserted in place each time
`python -m app.player_modelling.cli project-upcoming` runs — never
appended, since there is only ever one "current" projection per
player/match (see app/player_modelling/live_engine.py).

Distribution parameters are stored, not pre-computed threshold
probabilities — the same convention PlayerDisposalPrediction/
PlayerGoalPrediction already use, and for the same reason (Section 4 of the
live-projection brief): storing (predicted_mean, nb_alpha) or
(p_score, mu_scored, alpha_scored) lets any arbitrary threshold be priced on
demand via disposal_distribution.py/goal_distribution.py, rather than
hardcoding a fixed list of lines.

Freshness fields (Section 5): `model_version` changes only when the
underlying promoted model is retrained (see live_engine.py); `data_cutoff`
is the latest completed match timestamp actually used to build this
player's features, so it moves forward every time new results are ingested
even if the model itself hasn't changed; `lineup_status_at_generation`
freezes what the expected-lineup status was AT GENERATION TIME, so a later
change to ExpectedLineup can be detected as staleness without needing a
second read (see live_staleness.py). `team_model_version` is a 4th,
independent freshness axis (team-selection stage, Section 6): the Elo/
Poisson team-context CONFIG version used to build this player's team
features — distinct from `data_cutoff` (new completed results), since
re-tuning/re-promoting Elo or Poisson with the SAME set of completed
matches changes team context without moving data_cutoff at all.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PlayerDisposalProjection(TimestampMixin, Base):
    __tablename__ = "player_disposal_projections"
    __table_args__ = (UniqueConstraint("match_id", "player_id", name="uq_player_disposal_projection_match_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    team_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lineup_status_at_generation: Mapped[str] = mapped_column(String(16), nullable=False)

    games_of_history: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_mean: Mapped[float] = mapped_column(Float, nullable=False)
    distribution_method: Mapped[str] = mapped_column(String(32), nullable=False)  # "nb" | "empirical"
    nb_alpha: Mapped[float | None] = mapped_column(Float, nullable=True)

    confidence_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # The raw feature values that fed this projection, for the model
    # transparency drawer (Section 17) — deliberately the actual stored
    # inputs, not a generated explanation.
    input_features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    team: Mapped["Team"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<PlayerDisposalProjection match={self.match_id} player={self.player_id} mean={self.predicted_mean:.1f}>"


class PlayerGoalProjection(TimestampMixin, Base):
    __tablename__ = "player_goal_projections"
    __table_args__ = (UniqueConstraint("match_id", "player_id", name="uq_player_goal_projection_match_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    team_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lineup_status_at_generation: Mapped[str] = mapped_column(String(16), nullable=False)

    games_of_history: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_mean: Mapped[float] = mapped_column(Float, nullable=False)
    distribution_kind: Mapped[str] = mapped_column(String(32), nullable=False)  # "nb" | "hurdle"
    nb_alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    mu_scored: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_scored: Mapped[float | None] = mapped_column(Float, nullable=True)

    scoring_archetype: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    input_features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    team: Mapped["Team"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<PlayerGoalProjection match={self.match_id} player={self.player_id} mean={self.predicted_mean:.2f}>"
