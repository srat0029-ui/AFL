"""Shared, DB-decoupled DTOs used by more than one model's walk-forward replay.

Both Elo (Stage 1.2) and the Poisson scoring model (Stage 1.3) replay the
same completed matches chronologically, so they share one loader and one
input shape rather than each defining a slightly different match view.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MatchResult:
    """A completed match, as needed for walk-forward model replay.

    Goals/behinds are optional because they weren't captured for a match
    (or aren't available for a hypothetical non-AFL sport later) — Elo
    doesn't need them, only the Poisson scoring model does.
    """

    match_id: int
    season_year: int
    scheduled_start: datetime
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    home_goals: int | None = None
    home_behinds: int | None = None
    away_goals: int | None = None
    away_behinds: int | None = None
    # Defaults to 0 (unknown) rather than being required, so existing
    # callers/tests that build MatchResult without it keep working — only
    # the Poisson season-transition early-round diagnostics need this.
    round_number: int = 0
