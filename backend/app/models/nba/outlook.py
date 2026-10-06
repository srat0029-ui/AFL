"""Frozen prospective outlook snapshots.

One NbaOutlookSnapshot row per (game, snapshot label, tip-off as scheduled at
the cutoff) - e.g. the game's "T-24h" snapshot. It records WHEN the snapshot
was taken, what the system knew then (roster observations per team, when the
injury feed was last read, the latest box score the features could use), which
model runs produced it, and whether it was produced at all. A snapshot that
could not be produced honestly (missing models, no roster evidence, window
already passed) is still recorded, with status and reason, and has no player
rows - nothing is back-filled with later data.

The player rows are NbaRotationPrediction rows carrying `snapshot_id`. Both
tables are append-only (protected in app/models/nba/__init__.py): later
evidence creates a LATER snapshot, it never edits an earlier one.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin

SNAPSHOT_PRODUCED = "produced"
SNAPSHOT_STALE = "stale"  # rows written, but some required evidence was stale - not a normal success
SNAPSHOT_PARTIAL = "partial"  # produced for some teams; others lacked a usable roster
SNAPSHOT_MISSING_DATA = "missing_data"  # nothing produced: required evidence absent at the cutoff
SNAPSHOT_MISSED_WINDOW = "missed_window"  # the runner reached the game too late for this label


class NbaOutlookSnapshot(TimestampMixin, Base):
    __tablename__ = "nba_outlook_snapshots"
    __table_args__ = (UniqueConstraint("game_id", "snapshot_label", "scheduled_tipoff", name="uq_nba_outlook_snapshot_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("nba_games.id"), nullable=False, index=True)
    snapshot_label: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # e.g. "T-24h"
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)  # minutes before tip-off
    scheduled_tipoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # tip-off as known at the cutoff
    target_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # scheduled_tipoff - offset
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)  # the instant actually used
    box_score_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # latest tip-off a feature box score may have
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {team_id: {"roster_observed_at", "roster_confirmed_at", "observation_id", "players", "age_minutes"} or null}
    roster_evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    availability_feed_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # {"minutes": {"run_id", "model_version", "model_name", "artifact_sha256"}, "participation": {...}, "rotation": {...}}
    model_versions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Per source: age at the cutoff and fresh / stale / unavailable class, plus the limits applied.
    evidence_freshness: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<NbaOutlookSnapshot game={self.game_id} {self.snapshot_label} {self.status}>"
