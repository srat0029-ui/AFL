"""Point-in-time history of the model's OWN computed values — the one
genuine gap this project's prospective-freeze architecture didn't already
cover. `PricingSnapshot` freezes a price once per model_version (team
ratings drift continuously between promotions, so that alone can't capture
week-to-week movement); `PlayerDisposalProjection`/`PlayerGoalProjection`/
`ExpectedLineup` are all upserted in place with zero history. Without this
table there is nothing to diff "team win probability last cycle" against
"team win probability now."

Same append-only, insert-only-on-a-real-change discipline as `OddsQuote`/
`PlayerPropMarket` (see app/player_modelling/team_odds_ingestion.py and
prop_odds_ingestion.py): a new row is written only when the value actually
moved (rounded to a sane precision so float noise doesn't create rows —
see app/player_modelling/model_value_observations.py), never overwritten.
Materiality ("is this movement worth a trading desk's attention") is
decided at analysis time (app/player_modelling/model_movement.py), not at
capture time — full fidelity is preserved here, exactly like bookmaker
price history.

SGM joint probability is deliberately NOT tracked here — it already has
its own multi-horizon history in `SgmPriceSnapshot`; duplicating it here
would be a second, inconsistent copy of the same fact.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

VALUE_TEAM_WIN_PROBABILITY = "team_win_probability"
VALUE_TEAM_FAIR_ODDS = "team_fair_odds"
VALUE_PLAYER_DISPOSAL_PROBABILITY = "player_disposal_probability"
VALUE_PLAYER_GOAL_PROBABILITY = "player_goal_probability"
VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN = "player_disposal_projected_mean"
VALUE_PLAYER_GOAL_PROJECTED_MEAN = "player_goal_projected_mean"

KIND_PROBABILITY = "probability"
KIND_FAIR_ODDS = "fair_odds"
KIND_PROJECTED_MEAN = "projected_mean"


class ModelValueObservation(TimestampMixin, Base):
    __tablename__ = "model_value_observations"
    # No DB-level identity constraint (deliberately, matching OddsQuote's own
    # choice — see that model's docstring): a genuine value change always
    # gets a new row, so a unique constraint on the identity alone would be
    # wrong. Idempotency is enforced in application code (latest-row lookup
    # + equality check) — see model_value_observations.py.
    __table_args__ = (
        UniqueConstraint(
            "match_id", "player_id", "value_type", "selection", "threshold", "recorded_at",
            name="uq_model_value_observation_no_exact_dupe",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)

    value_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    value_kind: Mapped[str] = mapped_column(String(24), nullable=False)  # "probability" | "fair_odds" | "projected_mean"
    selection: Mapped[str | None] = mapped_column(String(64), nullable=True)  # team name for team_* rows, null for player_* rows
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)  # player markets only - which preset line this is

    value: Mapped[float] = mapped_column(Float, nullable=False)
    # Frozen alongside for player rows - the byproduct that makes "confidence:
    # provisional -> confirmed" reportable without a second new table (see
    # app/player_modelling/model_movement.py's lineup-status-change reporting).
    lineup_status: Mapped[str | None] = mapped_column(String(24), nullable=True)

    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])

    def __repr__(self) -> str:
        return f"<ModelValueObservation match={self.match_id} {self.value_type}={self.value:.4f} at={self.recorded_at.isoformat()}>"
