from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Team(TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("sport_id", "name", name="uq_team_sport_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    short_name: Mapped[str] = mapped_column(String(8), nullable=False)
    primary_colour: Mapped[str | None] = mapped_column(String(7), nullable=True)
    secondary_colour: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Matches Match.external_ids: {"squiggle": 3}. Matching is still by
    # (sport_id, name) — this is enrichment for future providers/debugging,
    # not a change to how teams are deduplicated.
    external_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    sport: Mapped["Sport"] = relationship(back_populates="teams")
    home_matches: Mapped[list["Match"]] = relationship(
        back_populates="home_team", foreign_keys="Match.home_team_id"
    )
    away_matches: Mapped[list["Match"]] = relationship(
        back_populates="away_team", foreign_keys="Match.away_team_id"
    )

    def __repr__(self) -> str:
        return f"<Team {self.short_name}>"
