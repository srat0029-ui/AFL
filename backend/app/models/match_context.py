"""Structured, timestamped current-context items (Current Context + Team
News Intelligence stage, Section 1) — confirmed selection changes,
injuries, late withdrawals, substitutes, role notes, weather/venue notes,
and other verified team news, kept separate from ExpectedLineup.

Why a separate table rather than folding into ExpectedLineup: ExpectedLineup
is a single upserted-in-place row per (match, player) representing the
CURRENT lineup state a projection reads (see expected_lineup.py). Context
items are append-only observations OVER TIME about what's happened/been
reported — the raw evidence a human (or a future ingestion pipeline) used
to arrive at that lineup state, plus things that never map onto a lineup
row at all (weather, venue conditions, a "limited game-time concern" note
that isn't a selection change). Keeping them separate lets history stay
fully visible (Section 12: "Preserve history") without turning
ExpectedLineup itself into an append-only log, which would break its
existing upsert-in-place contract used throughout live_engine.py.

Append-only, same philosophy as OddsQuote/WeeklyShortlistSnapshotItem: a
row is never edited or deleted after creation. Supersession (Section 12)
is NOT a stored link — it is derived at read time by grouping rows into a
"subject" (match_id, team_id, player_id) and taking the most recent by
source_timestamp (falling back to recorded_at when the source didn't
publish its own timestamp) as the current authoritative state; every
earlier row for that subject remains queryable as history. This mirrors
the existing "use ALL historical rows, latest wins" convention already
used by market_movement.py and the team OddsQuote dedup fix from the
Market Integrity stage — deliberately consistent with precedent rather
than inventing a second supersession mechanism.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class ContextType(str, enum.Enum):
    CONFIRMED_IN = "confirmed_in"
    CONFIRMED_OUT = "confirmed_out"
    INJURY = "injury"
    LATE_WITHDRAWAL = "late_withdrawal"
    NAMED_SUBSTITUTE = "named_substitute"
    EMERGENCY = "emergency"
    RETURNING_PLAYER = "returning_player"
    LIMITED_GAME_TIME_CONCERN = "limited_game_time_concern"
    WEATHER = "weather"
    VENUE_CONDITION = "venue_condition"
    MAJOR_ROLE_CHANGE = "major_role_change"
    OTHER = "other"


# Context types that describe a specific player, vs. a team-wide or
# match-wide note (weather/venue condition) — used by grouping/query
# helpers to decide whether player_id is expected to be set.
PLAYER_CONTEXT_TYPES = frozenset(
    {
        ContextType.CONFIRMED_IN.value,
        ContextType.CONFIRMED_OUT.value,
        ContextType.INJURY.value,
        ContextType.LATE_WITHDRAWAL.value,
        ContextType.NAMED_SUBSTITUTE.value,
        ContextType.EMERGENCY.value,
        ContextType.RETURNING_PLAYER.value,
        ContextType.LIMITED_GAME_TIME_CONCERN.value,
        ContextType.MAJOR_ROLE_CHANGE.value,
    }
)

CONTEXT_TYPE_LABELS: dict[str, str] = {
    ContextType.CONFIRMED_IN.value: "Confirmed in",
    ContextType.CONFIRMED_OUT.value: "Confirmed out",
    ContextType.INJURY.value: "Injury",
    ContextType.LATE_WITHDRAWAL.value: "Late withdrawal",
    ContextType.NAMED_SUBSTITUTE.value: "Named substitute",
    ContextType.EMERGENCY.value: "Emergency",
    ContextType.RETURNING_PLAYER.value: "Returning player",
    ContextType.LIMITED_GAME_TIME_CONCERN.value: "Limited game-time concern",
    ContextType.WEATHER.value: "Weather",
    ContextType.VENUE_CONDITION.value: "Venue condition",
    ContextType.MAJOR_ROLE_CHANGE.value: "Major role-change note",
    ContextType.OTHER.value: "Other verified team news",
}


class ContextConfidence(str, enum.Enum):
    # Section 2/3 preference order, collapsed to three tiers a source maps
    # onto directly: an official club/AFL announcement, a reputable named
    # media outlet report, or an unverified/manual note with no citation.
    OFFICIAL = "official"
    REPUTABLE_SOURCE = "reputable_source"
    UNVERIFIED = "unverified"


CONTEXT_CONFIDENCE_LABELS: dict[str, str] = {
    ContextConfidence.OFFICIAL.value: "Official announcement",
    ContextConfidence.REPUTABLE_SOURCE.value: "Reputable source",
    ContextConfidence.UNVERIFIED.value: "Unverified",
}


class MatchContextItem(TimestampMixin, Base):
    __tablename__ = "match_context_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)

    context_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # ContextType value
    confidence: Mapped[str] = mapped_column(String(24), nullable=False, default=ContextConfidence.UNVERIFIED.value)

    # Provenance (Section 3): a short, specific label, never a generic
    # "automated"/"manual" catch-all beyond what the entry actually is —
    # e.g. "Official team announcement", "AFL.com.au", "Club injury
    # update", "Manual".
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # When the SOURCE itself published/said this — distinct from
    # recorded_at (when this row was written). None when the source gave
    # no publish time (rare for a manual note with no real citation).
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "Fetched/recorded timestamp" (Section 1) — when this row was written,
    # always set (every write IS a fetch), same convention as
    # ExpectedLineup.recorded_at.
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    summary: Mapped[str] = mapped_column(String(500), nullable=False)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    team: Mapped["Team | None"] = relationship(foreign_keys=[team_id])
    player: Mapped["Player | None"] = relationship(foreign_keys=[player_id])

    def __repr__(self) -> str:
        return f"<MatchContextItem match={self.match_id} type={self.context_type} player={self.player_id}>"
