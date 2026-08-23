"""Append-only promotion audit trail (Model Registry stage): one row per
promotion decision, ever. Never updated or deleted — a later promotion in
the same market adds a NEW row, it never edits an earlier one, so the full
history of "what used to be champion, and why it changed" is always
reconstructable exactly as it happened.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class ModelPromotionEvent(Base, TimestampMixin):
    __tablename__ = "model_promotion_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # e.g. "player_disposals"

    previous_champion_model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_champion_model_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    new_champion_model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    new_champion_model_version: Mapped[str] = mapped_column(String(160), nullable=False)

    promoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    evaluation_metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<ModelPromotionEvent {self.market}: {self.previous_champion_model_name} -> {self.new_champion_model_name} at {self.promoted_at}>"
