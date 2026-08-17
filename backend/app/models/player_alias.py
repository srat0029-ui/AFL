"""Explicit, human-curated alternate name spellings for a Player (Section 9
of the live-operations stage brief) — e.g. a bookmaker provider returning
"Cameron Rayner" for a player we hold as "Cam Rayner" in a form the existing
nickname table (see prop_player_resolution.py's _GIVEN_NAME_NICKNAMES)
doesn't cover, or any other verified real-world naming discrepancy actually
observed in provider data.

Deliberately NOT a fuzzy-matching mechanism: every row here is a specific,
reviewed claim ("this exact string IS this exact player"), entered by a
human after seeing the real mismatch — never inferred algorithmically. This
is what makes it safe to trust at the same tier as an exact name match
(see prop_player_resolution.py's RESOLUTION_ALIAS tier).

`source` optionally scopes an alias to the provider that uses this name
form (e.g. "the_odds_api") — left NULL for an alias useful everywhere (e.g.
a common media nickname). Multiple aliases may point at the same player;
one alias_name always resolves to exactly one player (enforced by the
unique constraint below, scoped to source so two different providers could
theoretically use the same alias text for two different reasons without
colliding, though within one source it must still be unambiguous).
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PlayerAlias(TimestampMixin, Base):
    __tablename__ = "player_aliases"
    __table_args__ = (
        UniqueConstraint("alias_name", "source", name="uq_player_alias_name_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    alias_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # NULL = applies regardless of provider; otherwise scopes the alias to
    # one specific source (e.g. "the_odds_api") so two providers could use
    # the same alias text without a naming collision.
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    player: Mapped["Player"] = relationship(foreign_keys=[player_id])

    def __repr__(self) -> str:
        return f"<PlayerAlias {self.alias_name!r} -> player={self.player_id} source={self.source}>"
