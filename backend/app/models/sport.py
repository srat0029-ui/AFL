"""Sport is the top-level entity that keeps the schema sport-agnostic.

Stage 0 only ever populates one row ('AFL'), but every team/season/match hangs
off a sport_id so a second sport can be added later without restructuring
the tables that already exist.
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Sport(TimestampMixin, Base):
    __tablename__ = "sports"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    teams: Mapped[list["Team"]] = relationship(back_populates="sport")
    seasons: Mapped[list["Season"]] = relationship(back_populates="sport")

    def __repr__(self) -> str:
        return f"<Sport {self.code}>"
