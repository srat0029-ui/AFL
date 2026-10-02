from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class NbaTeam(TimestampMixin, Base):
    __tablename__ = "nba_teams"
    __table_args__ = (UniqueConstraint("name", name="uq_nba_team_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "Boston Celtics"
    abbreviation: Mapped[str] = mapped_column(String(8), nullable=False, index=True)  # e.g. "BOS"
    # Each provider's own id for this team, e.g. {"the_odds_api": "Boston Celtics"}.
    # Enrichment for resolving provider rows; dedup is by name.
    external_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<NbaTeam {self.abbreviation}>"
