"""Explicit time windows for teammate context analysis.

The with/without-teammate comparison used to span EVERY match a player had
recorded at their most recent club - potentially six seasons of old roles, old
team structures and old systems mixed into one headline number. This module
makes the time horizon an explicit, visible choice:

  * current_season       - the player's most recent season at their most
                           recent club (the default research view)
  * last_2_seasons       - that season and the calendar season before it
  * current_club_career  - every recorded match at the most recent club
                           (the previous behaviour)

Rules that keep this honest:
  - The club is still the club of the player's MOST RECENT MATCH ROW
    (PlayerMatchStat.team_id), never players.current_team_id.
  - "Current season" is anchored to the player's own most recent season at
    that club - a deterministic, data-derived anchor (no wall-clock), always
    named by year in the returned metadata so the reader sees exactly which
    season was used. A player who has not yet played this calendar year
    therefore shows their last season, labelled as such.
  - A window is NEVER silently broadened. If a window's sample is too small,
    the caller is told so and is shown what the other windows would contain
    (see WindowSummary in player_context_analysis) so the user can choose.
  - Matches whose season cannot be determined are excluded from the season-
    scoped windows (we cannot place them in time) and are counted in
    `games_excluded_missing_season`; the club-career window keeps them.
  - The point-in-time trailing baseline is computed over the player's whole
    club history up to each game (strictly earlier games only), NOT just the
    window - so narrowing the window never removes the pre-window history that
    a game's baseline legitimately depends on, and never introduces future
    information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.match import Match
from app.models.player_match_stat import PlayerMatchStat
from app.models.season import Season


class ContextWindow(str, Enum):
    CURRENT_SEASON = "current_season"
    LAST_2_SEASONS = "last_2_seasons"
    CURRENT_CLUB_CAREER = "current_club_career"


DEFAULT_WINDOW = ContextWindow.CURRENT_SEASON

# Narrow -> broad. A window is only ever "broader" than another by this order.
WINDOW_ORDER: tuple[ContextWindow, ...] = (
    ContextWindow.CURRENT_SEASON,
    ContextWindow.LAST_2_SEASONS,
    ContextWindow.CURRENT_CLUB_CAREER,
)

WINDOW_LABELS: dict[ContextWindow, str] = {
    ContextWindow.CURRENT_SEASON: "Current season",
    ContextWindow.LAST_2_SEASONS: "Last 2 seasons",
    ContextWindow.CURRENT_CLUB_CAREER: "Current club career",
}


@dataclass(frozen=True)
class ClubScope:
    """Everything needed to cut windows out of one player's most-recent-club
    history."""

    team_id: int
    club_rows: list[PlayerMatchStat]  # chronological
    matches_by_id: dict[int, Match]
    season_year_by_match: dict[int, int | None]
    anchor_season_year: int | None


@dataclass(frozen=True)
class WindowSelection:
    window: ContextWindow
    label: str
    scope_label: str  # e.g. "2026 season", "Last 2 seasons · 2025–2026", "Current club career · 2022–2026"
    rows: list[PlayerMatchStat] = field(default_factory=list)  # chronological, only games inside the window
    anchor_season_year: int | None = None
    included_seasons: list[int] = field(default_factory=list)
    earliest_date: datetime | None = None
    latest_date: datetime | None = None
    games_considered: int = 0
    games_excluded_missing_season: int = 0


def load_club_scope(db: Session, player_id: int) -> ClubScope | None:
    """The player's most-recent-club history, or None when the player has no
    recorded matches at all."""
    player_rows: list[PlayerMatchStat] = list(
        db.scalars(
            select(PlayerMatchStat)
            .join(Match, PlayerMatchStat.match_id == Match.id)
            .where(PlayerMatchStat.player_id == player_id)
            .order_by(Match.scheduled_start.asc(), PlayerMatchStat.match_id.asc())
        ).all()
    )
    if not player_rows:
        return None

    # Same safety as before: the club of the most recent match row - see
    # PlayerMatchStat's own docstring for why players.current_team_id is never used.
    team_id = player_rows[-1].team_id
    club_rows = [r for r in player_rows if r.team_id == team_id]
    match_ids = [r.match_id for r in club_rows]
    matches_by_id = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all()}
    season_ids = {m.season_id for m in matches_by_id.values() if m.season_id is not None}
    year_by_season_id = (
        {s.id: s.year for s in db.scalars(select(Season).where(Season.id.in_(season_ids))).all()} if season_ids else {}
    )
    season_year_by_match: dict[int, int | None] = {
        match_id: year_by_season_id.get(match.season_id) for match_id, match in matches_by_id.items()
    }
    ordered = sorted(club_rows, key=lambda r: (matches_by_id[r.match_id].scheduled_start, r.match_id))
    anchor = next((season_year_by_match[r.match_id] for r in reversed(ordered) if season_year_by_match.get(r.match_id) is not None), None)
    return ClubScope(
        team_id=team_id, club_rows=ordered, matches_by_id=matches_by_id,
        season_year_by_match=season_year_by_match, anchor_season_year=anchor,
    )


def _span(years: list[int]) -> str:
    return f"{years[0]}" if years[0] == years[-1] else f"{years[0]}–{years[-1]}"


def empty_selection(window: ContextWindow) -> WindowSelection:
    return WindowSelection(window=window, label=WINDOW_LABELS[window], scope_label=WINDOW_LABELS[window])


def select_window(scope: ClubScope, window: ContextWindow) -> WindowSelection:
    anchor = scope.anchor_season_year
    year_of = scope.season_year_by_match
    if window is ContextWindow.CURRENT_CLUB_CAREER:
        rows = list(scope.club_rows)
        excluded = 0
    else:
        if anchor is None:
            keep: set[int] = set()
        elif window is ContextWindow.CURRENT_SEASON:
            keep = {anchor}
        else:
            keep = {anchor, anchor - 1}
        rows = [r for r in scope.club_rows if year_of.get(r.match_id) in keep]
        excluded = sum(1 for r in scope.club_rows if year_of.get(r.match_id) is None)

    included = sorted({y for r in rows if (y := year_of.get(r.match_id)) is not None})
    dates = [scope.matches_by_id[r.match_id].scheduled_start for r in rows]

    if window is ContextWindow.CURRENT_SEASON:
        scope_label = f"{anchor} season" if anchor is not None else "Current season (season unknown)"
    elif window is ContextWindow.LAST_2_SEASONS:
        scope_label = f"Last 2 seasons · {anchor - 1}–{anchor}" if anchor is not None else "Last 2 seasons (season unknown)"
    else:
        scope_label = f"Current club career · {_span(included)}" if included else "Current club career"

    return WindowSelection(
        window=window, label=WINDOW_LABELS[window], scope_label=scope_label, rows=rows, anchor_season_year=anchor,
        included_seasons=included, earliest_date=min(dates) if dates else None, latest_date=max(dates) if dates else None,
        games_considered=len(rows), games_excluded_missing_season=excluded,
    )


def broader_windows(window: ContextWindow) -> list[ContextWindow]:
    return list(WINDOW_ORDER[WINDOW_ORDER.index(window) + 1:])


def window_phrase(selection: WindowSelection) -> str:
    """Short "when" phrase for messages: "this season (2026)", "in the last 2
    seasons (2025–2026)", "in this club career (2022–2026)"."""
    if selection.window is ContextWindow.CURRENT_SEASON:
        return f"this season ({selection.anchor_season_year})" if selection.anchor_season_year is not None else "this season"
    if selection.window is ContextWindow.LAST_2_SEASONS:
        span = f" ({selection.anchor_season_year - 1}–{selection.anchor_season_year})" if selection.anchor_season_year is not None else ""
        return f"in the last 2 seasons{span}"
    span = f" ({_span(selection.included_seasons)})" if selection.included_seasons else ""
    return f"in this club career{span}"
