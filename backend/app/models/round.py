from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Round(TimestampMixin, Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("season_id", "round_number", name="uq_round_season_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    season: Mapped["Season"] = relationship(back_populates="rounds")
    matches: Mapped[list["Match"]] = relationship(back_populates="round")

    def __repr__(self) -> str:
        return f"<Round {self.round_number}>"
