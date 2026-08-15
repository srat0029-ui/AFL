"""A player's durable identity, independent of any one match or team.

Identity is anchored to (sport_id, source, source_player_id) — AFL Tables
publishes a stable per-player page URL (e.g.
"stats/players/B/Blake_Acres.html", disambiguated with a trailing digit for
name clashes, e.g. "Jack_Carroll1.html") that survives trades, retirements,
and re-debuts, so that URL — not the display name — is the real identity
key. Player NAMES are not unique (AFL has had multiple concurrent players
with the same name) and are not stable identifiers on their own.

current_team_id is a convenience/display field only — see PlayerMatchStat
for the team a player actually represented in a specific historical match,
which does not change retroactively when a player is later traded.
"""

from sqlalchemy import Boolean, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Player(TimestampMixin, Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("sport_id", "source", "source_player_id", name="uq_player_sport_source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    current_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "afltables"
    # The source's own stable per-player identifier — for AFL Tables, the
    # player page's path (e.g. "players/B/Blake_Acres.html"). This, not
    # display_name, is what dedup and re-identification is keyed on.
    source_player_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # e.g. {"afltables_name_variants": ["Acres, Blake"]} — raw published name
    # forms seen, for debugging source-name formatting differences, not used
    # for matching.
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Null until determinable: computed at ingestion time from whether the
    # player has a match row in the most recently ingested season — not a
    # field AFL Tables publishes directly, so left NULL rather than guessed
    # until there's evidence either way.
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    current_team: Mapped["Team | None"] = relationship(foreign_keys=[current_team_id])

    def __repr__(self) -> str:
        return f"<Player {self.display_name!r} source={self.source}:{self.source_player_id}>"
