"""Distinguishes the CURRENT-facing player display population from the
historical evaluation population (data-scoping fix, product-quality
stage): pages meant to help with the current AFL season — player search,
Player Insights, opportunity/model-vs-market diagnostics, player
dropdowns — should only surface currently active/relevant players, never a
long-retired player like Jack Watts. Historical model evaluation (backtest
metrics, bias/MAE/calibration, training data) is explicitly OUT OF SCOPE
for this module and must keep using every historical player, retired or
not — nothing here touches that code path.

Deliberately does NOT use Player.current_team_id alone as the signal: it's
a convenience/display field (see app/models/player.py's docstring) that can
go stale for a retired player whose team was simply never updated. Instead
a player counts as current if they satisfy any of several current-SEASON
signals actually backed by fresh data.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, Match, Player, PlayerDisposalProjection, PlayerGoalProjection, PlayerMatchStat, Season, Sport


def current_season_year(db: Session, *, sport: str = "AFL", now: datetime | None = None) -> int:
    """The current AFL season, taken as the current calendar year (an AFL
    season runs within one calendar year — the same convention already used
    by app/backtesting/evaluation.py's split_by_period for the analogous
    match-level "current, still-in-progress season" concept). Falls back to
    the latest season that actually exists in the database if no Season row
    for the current year has been ingested yet, so this never raises on a
    fresh/lightly-seeded database."""
    year = (now or datetime.now(timezone.utc)).year
    exists = db.scalar(
        select(Season.id).join(Sport, Season.sport_id == Sport.id).where(Sport.code == sport, Season.year == year)
    )
    if exists is not None:
        return year
    latest = db.scalar(
        select(Season.year).join(Sport, Season.sport_id == Sport.id).where(Sport.code == sport).order_by(Season.year.desc()).limit(1)
    )
    return latest if latest is not None else year


def current_player_ids(db: Session, *, sport: str = "AFL", season_year: int | None = None) -> set[int]:
    """The reusable 'current players' population — call this once per
    request and filter/check membership against the returned set, rather
    than re-implementing this rule on each page (requirement: one shared
    helper, not duplicated filtering logic).

    A player is current if ANY of:
      - they have a PlayerMatchStat (actually played) in the current season;
      - they appear in an ExpectedLineup row for a current-season match
        (current team roster / lineup conversation, confirmed or not);
      - they have a live PlayerDisposalProjection or PlayerGoalProjection
        for a current-season match;
      - Player.is_active is explicitly True — set only for manually-added
        2026 debutants with zero historical data yet (see
        player_identity.create_new_player), so a genuine new player is
        never hidden merely for lacking history.
    """
    year = season_year if season_year is not None else current_season_year(db, sport=sport)

    def _season_scoped(model):
        return (
            select(model.player_id)
            .join(Match, model.match_id == Match.id)
            .join(Season, Match.season_id == Season.id)
            .join(Sport, Season.sport_id == Sport.id)
            .where(Sport.code == sport, Season.year == year)
        )

    explicitly_active = (
        select(Player.id).join(Sport, Player.sport_id == Sport.id).where(Sport.code == sport, Player.is_active.is_(True))
    )

    ids: set[int] = set()
    for stmt in (
        _season_scoped(PlayerMatchStat),
        _season_scoped(ExpectedLineup),
        _season_scoped(PlayerDisposalProjection),
        _season_scoped(PlayerGoalProjection),
        explicitly_active,
    ):
        ids.update(db.scalars(stmt).all())
    return ids


def is_current_player(player_id: int, current_ids: set[int]) -> bool:
    return player_id in current_ids
