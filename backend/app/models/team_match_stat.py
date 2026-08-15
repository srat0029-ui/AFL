"""Raw, per-team, per-match statistics from an external stats source (see
app/providers/afl/afltables.py). Deliberately mirrors the source's own raw
fields rather than storing only rolling averages — averages/features get
recomputed from this table as needed (see app/modelling/features.py), so a
future change to the feature-engineering logic never requires re-scraping.

Some fields are genuinely unavailable for some rows (e.g. Brownlow votes are
never awarded in finals) or from certain sources — those stay NULL rather
than being guessed.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class TeamMatchStat(TimestampMixin, Base):
    __tablename__ = "team_match_stats"
    __table_args__ = (
        UniqueConstraint("match_id", "team_id", "source", name="uq_team_match_stat_match_team_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    # Nullable: useful for display/joins, but redundant with match_id + team_id
    # (the match's other team) — kept explicit so a feature/report query
    # doesn't need to re-derive it via Match every time.
    opponent_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "afltables"
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # e.g. {"afltables_game_url": "games/2024/012020240316.html"} — AFL Tables
    # has no id scheme shared with Squiggle's, so this is for provenance/
    # dedup within this source, not cross-source match resolution.
    external_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Core disposal/scoring stats
    kicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    marks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handballs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disposals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Player-summed goals/behinds from this source — NOT necessarily identical
    # to Match.home_goals/home_behinds (Squiggle's official final score
    # breakdown). Behinds in particular can differ by a few because "rushed"
    # defensive behinds aren't attributed to any player in AFL Tables' player
    # stats this total is built from. See the validation check that compares
    # the two and documents this as an expected, understood discrepancy, not
    # a data error. Match.home_goals/home_behinds remain the authoritative
    # scoring figures used elsewhere in the app.
    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    behinds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    hitouts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tackles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rebound_50s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inside_50s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clangers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frees_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frees_against: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL for finals matches — the Brownlow Medal is a home-and-away-season-only award.
    brownlow_votes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contested_possessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uncontested_possessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contested_marks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    marks_inside_50: Mapped[int | None] = mapped_column(Integer, nullable=True)
    one_percenters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bounces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_assists: Mapped[int | None] = mapped_column(Integer, nullable=True)

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    team: Mapped["Team"] = relationship(foreign_keys=[team_id])
    opponent_team: Mapped["Team | None"] = relationship(foreign_keys=[opponent_team_id])

    def __repr__(self) -> str:
        return f"<TeamMatchStat match={self.match_id} team={self.team_id} source={self.source!r}>"
