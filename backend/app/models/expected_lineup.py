"""Expected-lineup status for an upcoming match — the mechanism the live
player-projection stage needs since, unlike historical backtesting (which
knows exactly who played from PlayerMatchStat), an upcoming match has no
such record yet.

Two status fields, deliberately kept separate (Section 2/7 of the
team-selection stage brief):

- `status` (ExpectedLineupStatus): the COARSE, model-facing signal —
  "should this player be projected at all" — expected_in / expected_out /
  uncertain. live_engine.py's eligibility check depends on exactly these
  three values; kept unchanged across the team-selection stage so nothing
  downstream needed to change.
- `selection_status` (SelectionStatus): the richer, AFL-specific
  selection-lifecycle state a real source (or a careful manual entry)
  actually distinguishes — a wide squad naming is not the same as a final
  confirmed team, and an emergency is not the same as a rostered player.
  `derive_coarse_status()` maps one onto the other; a manual direct edit of
  `status` alone (without a matching selection_status) is still honoured.

One row per (match, player), upserted in place (not append-only history).
Provenance (Section 3): `source` names where a row came from ("manual",
"manual_bulk", or a future automated provider's name); `source_timestamp`
is when the SOURCE itself published the information (None for a plain
manual entry with no real announcement backing it); `recorded_at`
(inherited usage, see below) is when THIS row was last written — serving
the "fetched_at" role the brief asks for, not duplicated as a second
column since the two would always be identical for how this system writes
rows (every write IS a fetch). `is_manual_override` marks a row a human
edited directly; automated/bulk ingestion must not silently overwrite an
overridden row unless explicitly told to (see
team_selection_ingestion.py). `source_reference` is an optional free-form
identifier/URL/note about exactly where a row's information came from.

A player with no row here for an upcoming match is simply not projected —
the system never guesses that a historical player is playing just because
they played the previous match.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class ExpectedLineupStatus(str, enum.Enum):
    EXPECTED_IN = "expected_in"
    EXPECTED_OUT = "expected_out"
    UNCERTAIN = "uncertain"


class SelectionStatus(str, enum.Enum):
    PLACEHOLDER = "placeholder"  # a guessed/reference roster (e.g. most recent match), not sourced from any real announcement
    NAMED_IN_SQUAD = "named_in_squad"  # a wider squad naming, before the final team is cut down
    CONFIRMED_SELECTED = "confirmed_selected"  # final official team, playing
    EMERGENCY = "emergency"
    SUBSTITUTE = "substitute"  # named as the medical substitute / potential substitute
    CONFIRMED_OUT = "confirmed_out"
    UNCERTAIN = "uncertain"  # explicitly flagged as unclear, distinct from "no information at all" (no row)


# Only a genuinely confirmed-to-play selection counts as "confirmed" for
# display/gating purposes - CONFIRMED_OUT is equally definitive but is a
# confirmed NON-selection, tracked separately via selection_status itself
# rather than folded into this flag.
CONFIRMED_SELECTION_STATUSES = frozenset({SelectionStatus.CONFIRMED_SELECTED.value})

# Default selection_status -> coarse status mapping, applied by the
# ingestion service (see team_selection_ingestion.py) whenever a row is
# written WITHOUT an explicit coarse-status override. A human editing
# `status` directly (e.g. via the plain PUT endpoint) is unaffected by this
# table - that write sets both fields to whatever was explicitly requested.
DEFAULT_COARSE_STATUS_BY_SELECTION = {
    SelectionStatus.CONFIRMED_SELECTED.value: ExpectedLineupStatus.EXPECTED_IN.value,
    SelectionStatus.SUBSTITUTE.value: ExpectedLineupStatus.EXPECTED_IN.value,
    SelectionStatus.NAMED_IN_SQUAD.value: ExpectedLineupStatus.UNCERTAIN.value,
    SelectionStatus.EMERGENCY.value: ExpectedLineupStatus.UNCERTAIN.value,
    SelectionStatus.PLACEHOLDER.value: ExpectedLineupStatus.UNCERTAIN.value,
    SelectionStatus.UNCERTAIN.value: ExpectedLineupStatus.UNCERTAIN.value,
    SelectionStatus.CONFIRMED_OUT.value: ExpectedLineupStatus.EXPECTED_OUT.value,
}


def derive_coarse_status(selection_status: str) -> str:
    return DEFAULT_COARSE_STATUS_BY_SELECTION.get(selection_status, ExpectedLineupStatus.UNCERTAIN.value)


class ExpectedLineup(TimestampMixin, Base):
    __tablename__ = "expected_lineups"
    __table_args__ = (UniqueConstraint("match_id", "player_id", name="uq_expected_lineup_match_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ExpectedLineupStatus value
    selection_status: Mapped[str] = mapped_column(String(24), nullable=False, default=SelectionStatus.PLACEHOLDER.value)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Optional extra context a manual entry can carry — all nullable, since
    # most entries will just be a plain status.
    substitute_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    returning_from_injury: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    role_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Percentage-point adjustment to the model's own TOG expectation (e.g.
    # -15.0 for "expected to play a reduced role") — NOT used to override the
    # historical TOG features fed into the model (those must stay leakage-safe
    # rolling history), only surfaced alongside a projection as manually
    # supplied context. Left for a later stage to actually wire into the
    # projection maths; stored now so the schema doesn't need to change when
    # that lands.
    expected_tog_adjustment: Mapped[float | None] = mapped_column(Float, nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    player: Mapped["Player"] = relationship(foreign_keys=[player_id])
    team: Mapped["Team"] = relationship(foreign_keys=[team_id])

    def __repr__(self) -> str:
        return f"<ExpectedLineup match={self.match_id} player={self.player_id} status={self.status} selection={self.selection_status}>"
