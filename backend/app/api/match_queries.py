"""Shared match-listing query logic, used by both the legacy /api/matches
routes and the newer /api/afl/* routes so filtering isn't implemented twice.

Kept in the API layer (not app/ingestion or app/modelling) since this is
read/query shaping for API consumers, not part of the ingestion or modelling
pipelines — see the architecture note in app/edges/calculator.py for the
equivalent separation on the modelling side.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, Round, Season, Sport


def query_matches(
    db: Session,
    *,
    sport: str = "AFL",
    status: MatchStatus | None = None,
    season_year: int | None = None,
    round_number: int | None = None,
    team_id: int | None = None,
    order: str = "asc",
    limit: int | None = None,
) -> list[Match]:
    query = select(Match).join(Sport).where(Sport.code == sport)

    if status is not None:
        query = query.where(Match.status == status)
    if season_year is not None:
        # Explicit join condition: Season also has its own sport_id FK to
        # Sport, so with Sport already joined, an implicit .join(Season)
        # ambiguously resolves to Season-to-Sport instead of Match-to-Season.
        query = query.join(Season, Match.season_id == Season.id).where(Season.year == season_year)
    if round_number is not None:
        query = query.join(Round, Match.round_id == Round.id).where(Round.round_number == round_number)
    if team_id is not None:
        query = query.where(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))

    query = query.order_by(Match.scheduled_start.desc() if order == "desc" else Match.scheduled_start.asc())
    if limit is not None:
        query = query.limit(limit)

    return list(db.scalars(query).all())
