"""Builds leakage-safe "as of right now" feature rows for upcoming players —
the live-projection counterpart to disposal_features.py/goal_features.py,
which only ever build features for COMPLETED matches (the target match's
own actual stats are real and available in the historical dataset).

An upcoming match has no PlayerMatchStat/TeamMatchStat rows at all, so
there is nothing to leak — but the SAME walk-forward feature builders are
reused rather than re-implemented, by handing each one exactly one extra,
synthetic "next row" per expected player: a row with the correct
identifiers/kickoff-time (so it sorts chronologically last, after every
real completed match) but placeholder/None stat values that are never
treated as a real outcome and are always discarded after the feature dict
is read off.

Only the SINGLE nearest upcoming round is ever processed in one pass
(see load_next_upcoming_round). This is a deliberate correctness
constraint, not just a product-framing choice: a player cannot play twice
in one round, so at most one synthetic row per player ever exists in a
given build() call. If two different future rounds were built in the same
pass, an earlier round's placeholder row (disposals=0, since
PlayerGameRow.disposals is a plain non-optional int, not int | None) would
get folded into that player's rolling history by the builder's own
update-after-build step, corrupting the later round's features with a fake
zero — the loader-level "one round only" scoping is what prevents this,
not any special-case logic in the builders themselves.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, ExpectedLineupStatus, Match, MatchStatus, PlayerMatchStat, Sport
from app.player_modelling.disposal_data import PlayerGameRow, TeamGameRow, load_player_game_rows, load_team_game_rows
from app.player_modelling.disposal_features import DisposalFeatureBuilder, DisposalFeatureRow
from app.player_modelling.goal_data import PlayerGoalGameRow, TeamGoalGameRow, load_player_goal_game_rows, load_team_goal_game_rows
from app.player_modelling.goal_features import GoalFeatureBuilder, GoalFeatureRow


@dataclass(frozen=True)
class UpcomingMatchTeams:
    match_id: int
    home_team_id: int
    away_team_id: int
    venue_id: int | None
    scheduled_start: datetime
    season_year: int
    round_number: int
    is_final: bool


@dataclass(frozen=True)
class ExpectedPlayer:
    player_id: int
    match_id: int
    team_id: int
    opponent_team_id: int
    is_home: bool
    status: str  # ExpectedLineupStatus value


def load_next_upcoming_round(db: Session, sport_code: str = "AFL") -> list[UpcomingMatchTeams]:
    """The single nearest upcoming round's matches only - see module
    docstring for why more than one future round is never mixed into a
    single feature-build pass."""
    earliest = db.execute(
        select(Match)
        .join(Sport, Sport.id == Match.sport_id)
        .where(Sport.code == sport_code, Match.status == MatchStatus.SCHEDULED)
        .order_by(Match.scheduled_start)
        .limit(1)
    ).scalar_one_or_none()
    if earliest is None:
        return []

    matches = (
        db.execute(
            select(Match)
            .where(Match.round_id == earliest.round_id, Match.status == MatchStatus.SCHEDULED)
            .order_by(Match.scheduled_start)
        )
        .scalars()
        .all()
    )

    return [
        UpcomingMatchTeams(
            match_id=m.id,
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
            venue_id=m.venue_id,
            scheduled_start=m.scheduled_start,
            season_year=m.season.year,
            round_number=m.round.round_number,
            is_final=m.round.name is not None,
        )
        for m in matches
    ]


def load_expected_players(db: Session, match_ids: list[int]) -> list[ExpectedPlayer]:
    """Only EXPECTED_IN / UNCERTAIN players are returned - EXPECTED_OUT
    players (and any player with no lineup record at all) get no
    projection, per Section 1's "make it obvious when a projection depends
    on an unconfirmed lineup" and "do not invent likely players"."""
    if not match_ids:
        return []
    rows = db.execute(
        select(ExpectedLineup, Match)
        .join(Match, Match.id == ExpectedLineup.match_id)
        .where(
            ExpectedLineup.match_id.in_(match_ids),
            ExpectedLineup.status.in_((ExpectedLineupStatus.EXPECTED_IN.value, ExpectedLineupStatus.UNCERTAIN.value)),
        )
    ).all()
    result = []
    for lineup, match in rows:
        is_home = lineup.team_id == match.home_team_id
        opponent_team_id = match.away_team_id if is_home else match.home_team_id
        result.append(
            ExpectedPlayer(
                player_id=lineup.player_id,
                match_id=lineup.match_id,
                team_id=lineup.team_id,
                opponent_team_id=opponent_team_id,
                is_home=is_home,
                status=lineup.status,
            )
        )
    return result


def build_upcoming_disposal_features(
    db: Session,
    upcoming_matches: list[UpcomingMatchTeams],
    expected_players: list[ExpectedPlayer],
    team_context: dict[int, dict[int, dict]],
) -> dict[tuple[int, int], DisposalFeatureRow]:
    """Returns {(player_id, match_id): DisposalFeatureRow}, one per
    expected player, built by the real DisposalFeatureBuilder over real
    history plus one synthetic row per player (see module docstring)."""
    player_rows = list(load_player_game_rows(db))
    team_rows = list(load_team_game_rows(db))
    match_by_id = {m.match_id: m for m in upcoming_matches}

    for m in upcoming_matches:
        team_rows.append(
            TeamGameRow(
                team_id=m.home_team_id, opponent_team_id=m.away_team_id, match_id=m.match_id,
                season_year=m.season_year, scheduled_start=m.scheduled_start, disposals=None,
            )
        )
        team_rows.append(
            TeamGameRow(
                team_id=m.away_team_id, opponent_team_id=m.home_team_id, match_id=m.match_id,
                season_year=m.season_year, scheduled_start=m.scheduled_start, disposals=None,
            )
        )

    synthetic_keys: set[tuple[int, int]] = set()
    for ep in expected_players:
        m = match_by_id[ep.match_id]
        player_rows.append(
            PlayerGameRow(
                player_id=ep.player_id, match_id=ep.match_id, team_id=ep.team_id, opponent_team_id=ep.opponent_team_id,
                season_year=m.season_year, round_number=m.round_number, is_final=m.is_final, is_home=ep.is_home,
                venue_id=m.venue_id, scheduled_start=m.scheduled_start, disposals=0, kicks=None, handballs=None,
                marks=None, tackles=None, clearances=None, inside_50s=None, contested_possessions=None,
                uncontested_possessions=None, time_on_ground_pct=None, subbed_on=False, subbed_off=False,
            )
        )
        synthetic_keys.add((ep.player_id, ep.match_id))

    builder = DisposalFeatureBuilder(team_context=team_context)
    all_rows = builder.build(player_rows, team_rows)
    return {(r.player_id, r.match_id): r for r in all_rows if (r.player_id, r.match_id) in synthetic_keys}


def build_upcoming_goal_features(
    db: Session,
    upcoming_matches: list[UpcomingMatchTeams],
    expected_players: list[ExpectedPlayer],
    team_context: dict[int, dict[int, dict]],
) -> dict[tuple[int, int], GoalFeatureRow]:
    player_rows = list(load_player_goal_game_rows(db))
    team_rows = list(load_team_goal_game_rows(db))
    match_by_id = {m.match_id: m for m in upcoming_matches}

    for m in upcoming_matches:
        team_rows.append(
            TeamGoalGameRow(
                team_id=m.home_team_id, opponent_team_id=m.away_team_id, match_id=m.match_id,
                season_year=m.season_year, scheduled_start=m.scheduled_start, goals=None, behinds=None, inside_50s=None,
            )
        )
        team_rows.append(
            TeamGoalGameRow(
                team_id=m.away_team_id, opponent_team_id=m.home_team_id, match_id=m.match_id,
                season_year=m.season_year, scheduled_start=m.scheduled_start, goals=None, behinds=None, inside_50s=None,
            )
        )

    synthetic_keys: set[tuple[int, int]] = set()
    for ep in expected_players:
        m = match_by_id[ep.match_id]
        player_rows.append(
            PlayerGoalGameRow(
                player_id=ep.player_id, match_id=ep.match_id, team_id=ep.team_id, opponent_team_id=ep.opponent_team_id,
                season_year=m.season_year, round_number=m.round_number, is_final=m.is_final, is_home=ep.is_home,
                venue_id=m.venue_id, scheduled_start=m.scheduled_start, goals=0, behinds=None, disposals=None,
                kicks=None, marks=None, handballs=None, tackles=None, contested_possessions=None,
                uncontested_possessions=None, inside_50s=None, marks_inside_50=None, goal_assists=None,
                time_on_ground_pct=None, subbed_on=False, subbed_off=False,
            )
        )
        synthetic_keys.add((ep.player_id, ep.match_id))

    builder = GoalFeatureBuilder(team_context=team_context)
    all_rows = builder.build(player_rows, team_rows)
    return {(r.player_id, r.match_id): r for r in all_rows if (r.player_id, r.match_id) in synthetic_keys}


def load_all_lineup_player_ids(db: Session, match_ids: list[int]) -> set[int]:
    """Every player with ANY current ExpectedLineup record for these
    matches, regardless of status - used only to figure out who does NOT
    yet have a recorded status at all (see count_missing_lineup_candidates);
    an EXPECTED_OUT player has a real, recorded status and should not be
    treated as "missing information" the way a player with no record is."""
    if not match_ids:
        return set()
    return set(db.scalars(select(ExpectedLineup.player_id).where(ExpectedLineup.match_id.in_(match_ids))).all())


def count_missing_lineup_candidates(
    db: Session, upcoming_matches: list[UpcomingMatchTeams], players_with_lineup: set[int]
) -> dict[int, int]:
    """A diagnostic count only — Section 23's "players blocked by missing
    lineup information" — NEVER used to generate a projection. For each
    upcoming team, counts players who featured in that team's most recent
    COMPLETED match but have no current ExpectedLineup row for the upcoming
    one; a rough "likely squad, not yet confirmed" signal for the CLI report,
    not an inference that they ARE playing (see module docstring: this
    system never assumes a historical player is playing just because they
    played last time)."""
    result: dict[int, int] = {}
    team_ids = {t for m in upcoming_matches for t in (m.home_team_id, m.away_team_id)}
    for team_id in team_ids:
        last_match_id = db.execute(
            select(PlayerMatchStat.match_id)
            .join(Match, Match.id == PlayerMatchStat.match_id)
            .where(PlayerMatchStat.team_id == team_id, Match.status == MatchStatus.COMPLETED)
            .order_by(Match.scheduled_start.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last_match_id is None:
            result[team_id] = 0
            continue
        recent_player_ids = set(
            db.scalars(
                select(PlayerMatchStat.player_id).where(
                    PlayerMatchStat.team_id == team_id, PlayerMatchStat.match_id == last_match_id
                )
            ).all()
        )
        result[team_id] = len(recent_player_ids - players_with_lineup)
    return result
