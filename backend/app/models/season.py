from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Season(TimestampMixin, Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("sport_id", "year", name="uq_season_sport_year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_id: Mapped[int] = mapped_column(ForeignKey("sports.id"), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    sport: Mapped["Sport"] = relationship(back_populates="seasons")
    rounds: Mapped[list["Round"]] = relationship(back_populates="season")
    matches: Mapped[list["Match"]] = relationship(back_populates="season")

    def __repr__(self) -> str:
        return f"<Season {self.year}>"
