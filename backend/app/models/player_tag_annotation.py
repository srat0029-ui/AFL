"""Verified/manual tagging-annotation foundation.

AFL Tables-style box-score data (PlayerMatchStat) cannot tell you who was
assigned a defensive tagging role or how long it lasted - that fact simply
isn't published in structured form anywhere this project ingests from
(confirmed by a repo-wide search: no tagging model, ingestion source, or
service concept exists before this table). This table exists so a
genuinely VERIFIED tag fact (an official post-match confirmation, a
reputable outlet's tagging report, or a manually reviewed video note) CAN
be recorded once a real source exists, without a later schema change -
it is deliberately NOT populated by any ingestion pipeline today.
app/player_modelling/tag_watch.py reports "insufficient_verified_data"
until enough real rows exist for a given player, which today means
always. Never derive a row here from AFL Tables' own possession/tackle
counts - that would be inventing the exact fact this table exists to keep
honest.

confidence reuses ContextConfidence (app/models/match_context.py) rather
than inventing a parallel enum - the same official/reputable-source/
unverified source-authority distinction applies here.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PlayerTagAnnotation(TimestampMixin, Base):
    __tablename__ = "player_tag_annotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    tagged_player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    # Nullable: a source may confirm "Player X was tagged" without
    # identifying who did it.
    tagger_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True, index=True)

    confidence: Mapped[str] = mapped_column(String(32), nullable=False)  # ContextConfidence value
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "manual_review", "club_announcement"
    # Whole-match percentage the tag was actually applied for, if known
    # (a tag is often only part of a match).
    tagged_duration_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    tagged_player: Mapped["Player"] = relationship(foreign_keys=[tagged_player_id])
    tagger_player: Mapped["Player | None"] = relationship(foreign_keys=[tagger_player_id])

    def __repr__(self) -> str:
        return f"<PlayerTagAnnotation match={self.match_id} tagged={self.tagged_player_id}>"
