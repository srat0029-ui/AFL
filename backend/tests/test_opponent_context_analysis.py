"""Tests for app/player_modelling/opponent_context_analysis.py - the
selected-opponent vs other-opponents split, the honestly-gated adjusted
effect, sample-size confidence tiering, and point-in-time integrity of
the trailing-form baseline it reuses from player_context_analysis.py.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.opponent_context_analysis import (
    build_opponent_context_analysis,
)
from app.player_modelling.player_context_analysis import (
    MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT,
    PlayerContextConfidenceTier,
)

BASE = datetime(2024, 3, 1, tzinfo=timezone.utc)


def _seed_club(db, suffix=""):
    sport = Sport(code=f"AFL{suffix}", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2024)
    db.add(season)
    db.flush()
    home = Team(sport_id=sport.id, name="North Melbourne", short_name="NM")
    opp_a = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    opp_b = Team(sport_id=sport.id, name="Essendon", short_name="ESS")
    db.add_all([home, opp_a, opp_b])
    db.flush()
    player = Player(sport_id=sport.id, display_name="Harry Sheezel", source="afltables", source_player_id=f"players/S/Sheezel{suffix}.html")
    db.add(player)
    db.commit()
    return sport, season, home, opp_a, opp_b, player


def _add_match(db, sport, season, round_number, home, away, start):
    round_ = Round(season_id=season.id, round_number=round_number)
    db.add(round_)
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=start, status=MatchStatus.COMPLETED,
    )
    db.add(match)
    db.commit()
    return match


def _add_stat(db, *, player, match, team, opponent_team, disposals, goals=1, tog=85):
    row = PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=team.id,
        opponent_team_id=opponent_team.id if opponent_team is not None else None,
        source="afltables", recorded_at=match.scheduled_start, disposals=disposals, goals=goals, time_on_ground_pct=tog,
    )
    db.add(row)
    db.commit()
    return row


def _build_scenario(db, *, disposals_against, disposals_other, suffix=""):
    """Seeds `player` at one club, always playing HOME against opp_a for
    the "against" games and away at opp_b's venue for the "other" games -
    the exact venue/home-away split doesn't matter for the raw split,
    only for is_home evidence checks below.
    """
    sport, season, home, opp_a, opp_b, player = _seed_club(db, suffix=suffix)
    round_number = 1
    start = BASE
    for d in disposals_against:
        match = _add_match(db, sport, season, round_number, home, opp_a, start)
        _add_stat(db, player=player, match=match, team=home, opponent_team=opp_a, disposals=d)
        round_number += 1
        start += timedelta(days=7)
    for d in disposals_other:
        match = _add_match(db, sport, season, round_number, home, opp_b, start)
        _add_stat(db, player=player, match=match, team=home, opponent_team=opp_b, disposals=d)
        round_number += 1
        start += timedelta(days=7)
    return player, opp_a, opp_b


def test_raw_split_means_and_medians(db_session):
    player, opp_a, opp_b = _build_scenario(db_session, disposals_against=[20, 22, 24, 26, 28, 30], disposals_other=[10, 12, 14, 16, 18, 20])
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)

    assert analysis.against_opponent.games == 6
    assert analysis.against_other_opponents.games == 6
    assert analysis.against_opponent.mean == sum([20, 22, 24, 26, 28, 30]) / 6
    assert analysis.against_other_opponents.mean == sum([10, 12, 14, 16, 18, 20]) / 6
    # against_opponent minus against_other_opponents - positive here since
    # the "against" games are the higher-scoring group.
    assert analysis.raw_difference == analysis.against_opponent.mean - analysis.against_other_opponents.mean
    assert analysis.raw_difference > 0


def test_milestone_rates_computed_per_group(db_session):
    player, opp_a, opp_b = _build_scenario(db_session, disposals_against=[10, 15, 20, 25], disposals_other=[25, 30, 30, 35])
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id, thresholds=[15, 25])

    assert analysis.against_opponent.milestone_rates[15] == 3 / 4
    assert analysis.against_opponent.milestone_rates[25] == 1 / 4
    assert analysis.against_other_opponents.milestone_rates[15] == 1.0
    assert analysis.against_other_opponents.milestone_rates[25] == 1.0


def test_time_on_ground_averaged_only_over_non_null(db_session):
    sport, season, home, opp_a, opp_b, player = _seed_club(db_session)
    m1 = _add_match(db_session, sport, season, 1, home, opp_b, BASE)
    _add_stat(db_session, player=player, match=m1, team=home, opponent_team=opp_b, disposals=20, tog=90)
    m2 = _add_match(db_session, sport, season, 2, home, opp_b, BASE + timedelta(days=7))
    row2 = PlayerMatchStat(
        player_id=player.id, match_id=m2.id, team_id=home.id, opponent_team_id=opp_b.id,
        source="afltables", recorded_at=m2.scheduled_start, disposals=22, goals=1, time_on_ground_pct=None,
    )
    db_session.add(row2)
    db_session.commit()

    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)
    assert analysis.against_other_opponents.time_on_ground_sample_size == 1
    assert analysis.against_other_opponents.average_time_on_ground_pct == 90


def test_stat_query_param_selects_goals(db_session):
    player, opp_a, opp_b = _build_scenario(db_session, disposals_against=[20, 22], disposals_other=[24, 26])
    # goals default to 1 for every row in _add_stat
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id, stat="goals", thresholds=[1, 2])
    assert analysis.stat == "goals"
    assert analysis.against_opponent.mean == 1.0
    assert analysis.against_opponent.milestone_rates[1] == 1.0
    assert analysis.against_opponent.milestone_rates[2] == 0.0


def test_no_recorded_stats_returns_insufficient_history_not_an_error(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Rookie", source="afltables", source_player_id="players/R/Rookie.html")
    opponent = Team(sport_id=sport.id, name="Geelong", short_name="GEE")
    db_session.add_all([player, opponent])
    db_session.commit()

    analysis = build_opponent_context_analysis(db_session, player.id, opponent.id)

    assert analysis.against_opponent.games == 0
    assert analysis.against_other_opponents.games == 0
    assert analysis.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY
    assert analysis.adjusted_effect.available is False
    assert "No recorded match statistics" in analysis.adjusted_effect.explanation


def test_opponent_never_faced_reports_zero_against_games_and_a_warning(db_session):
    """Player has games, but never actually played the requested opponent
    - a real, meaningful "no" answer, not an error."""
    sport, season, home, opp_a, opp_b, player = _seed_club(db_session)
    m1 = _add_match(db_session, sport, season, 1, home, opp_b, BASE)
    _add_stat(db_session, player=player, match=m1, team=home, opponent_team=opp_b, disposals=20)

    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)

    assert analysis.against_opponent.games == 0
    assert analysis.against_other_opponents.games == 1
    assert any("No recorded games found against this opponent" in w for w in analysis.confidence.warnings)


def test_analysis_restricted_to_players_most_recent_club(db_session):
    """A player who changed clubs should only be compared within their
    CURRENT club's games, per PlayerMatchStat.team_id being the source of
    truth (not players.current_team_id)."""
    sport, season, home, opp_a, opp_b, player = _seed_club(db_session)
    old_club = Team(sport_id=sport.id, name="Western Bulldogs", short_name="WB")
    db_session.add(old_club)
    db_session.commit()

    # Earlier stint at old_club, against opp_a there too.
    m_old = _add_match(db_session, sport, season, 1, old_club, opp_a, BASE)
    _add_stat(db_session, player=player, match=m_old, team=old_club, opponent_team=opp_a, disposals=5)

    # Later, traded to `home`, also faces opp_a.
    m_new = _add_match(db_session, sport, season, 2, home, opp_a, BASE + timedelta(days=200))
    _add_stat(db_session, player=player, match=m_new, team=home, opponent_team=opp_a, disposals=25)

    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)

    assert analysis.team_id == home.id
    assert analysis.against_opponent.games == 1  # only the `home`-club game counts
    assert analysis.against_opponent.mean == 25


def test_unknown_player_raises_lookup_error(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    opponent = Team(sport_id=sport.id, name="Geelong", short_name="GEE")
    db_session.add(opponent)
    db_session.commit()

    with pytest.raises(LookupError):
        build_opponent_context_analysis(db_session, 999999, opponent.id)


def test_unknown_opponent_team_raises_lookup_error(db_session):
    player, opp_a, _opp_b = _build_scenario(db_session, disposals_against=[20], disposals_other=[25])

    with pytest.raises(LookupError):
        build_opponent_context_analysis(db_session, player.id, 999999)


def test_games_with_unknown_opponent_excluded_from_both_groups(db_session):
    """A row with opponent_team_id=None must never be silently counted as
    'against other opponents' - that would misrepresent data we don't
    actually have."""
    sport, season, home, opp_a, opp_b, player = _seed_club(db_session)
    m1 = _add_match(db_session, sport, season, 1, home, opp_a, BASE)
    _add_stat(db_session, player=player, match=m1, team=home, opponent_team=opp_a, disposals=20)
    m2 = _add_match(db_session, sport, season, 2, home, opp_b, BASE + timedelta(days=7))
    _add_stat(db_session, player=player, match=m2, team=home, opponent_team=None, disposals=99)

    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)

    assert analysis.against_opponent.games == 1
    assert analysis.against_other_opponents.games == 0
    assert all(e.match_id != m2.id for e in analysis.evidence)


def test_role_analysis_always_reported_unavailable(db_session):
    player, opp_a, opp_b = _build_scenario(db_session, disposals_against=[20], disposals_other=[25])
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)
    assert analysis.role_analysis_available is False
    assert "No structured position/role data" in analysis.role_analysis_explanation


def test_confounders_report_what_was_and_was_not_considered(db_session):
    player, opp_a, opp_b = _build_scenario(db_session, disposals_against=[20], disposals_other=[25])
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)
    assert analysis.confounders["recent_form"].considered is True
    assert analysis.confounders["venue"].considered is False
    assert analysis.confounders["role"].considered is False
    assert analysis.confounders["lineup_and_era_context"].considered is False
    assert analysis.confounders["lineup_and_era_context"].reason


def test_scope_explanation_states_most_recent_club_restriction(db_session):
    player, opp_a, opp_b = _build_scenario(db_session, disposals_against=[20], disposals_other=[25])
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)
    assert "most recent recorded club" in analysis.scope_explanation


def test_evidence_rows_are_auditable_newest_first_and_mark_selected_opponent(db_session):
    player, opp_a, opp_b = _build_scenario(db_session, disposals_against=[20, 22], disposals_other=[24])
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)

    assert len(analysis.evidence) == 3
    starts = [e.scheduled_start for e in analysis.evidence]
    assert starts == sorted(starts, reverse=True)
    for row in analysis.evidence:
        assert row.opponent_name in {"Carlton", "Essendon"}
        assert row.team_name == "North Melbourne"
        assert row.season_year == 2024
        assert row.is_home is True  # `home` is always home_team_id in this fixture

    against_row = next(e for e in analysis.evidence if e.opponent_name == "Carlton")
    assert against_row.is_selected_opponent is True
    other_row = next(e for e in analysis.evidence if e.opponent_name == "Essendon")
    assert other_row.is_selected_opponent is False


def test_home_away_is_derived_correctly_when_player_team_is_away(db_session):
    sport, season, home, opp_a, opp_b, player = _seed_club(db_session)
    # player's club is the AWAY team in this match
    match = _add_match(db_session, sport, season, 1, opp_b, home, BASE)
    _add_stat(db_session, player=player, match=match, team=home, opponent_team=opp_b, disposals=20)

    analysis = build_opponent_context_analysis(db_session, player.id, opp_b.id)
    assert analysis.evidence[0].is_home is False


def test_confidence_tier_escalates_with_sample_size(db_session):
    small_player, small_opp_a, _b = _build_scenario(db_session, disposals_against=[20, 21], disposals_other=[25])
    small = build_opponent_context_analysis(db_session, small_player.id, small_opp_a.id)
    assert small.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY

    bigger_against = [20 + i for i in range(20)]
    bigger_other = [25 + i for i in range(20)]
    bigger_player, bigger_opp_a, _b2 = _build_scenario(db_session, disposals_against=bigger_against, disposals_other=bigger_other, suffix="2")
    bigger = build_opponent_context_analysis(db_session, bigger_player.id, bigger_opp_a.id)
    assert bigger.confidence.tier is PlayerContextConfidenceTier.HIGHER


def test_adjusted_effect_unavailable_below_minimum_sample_per_group(db_session):
    assert MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT == 5
    player, opp_a, opp_b = _build_scenario(
        db_session,
        disposals_against=[20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31],
        disposals_other=[35, 36],
    )
    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)

    assert analysis.adjusted_effect.available is False
    assert "Need at least" in analysis.adjusted_effect.explanation


def test_adjusted_effect_available_and_isolates_recent_form(db_session):
    """A genuine form spike unrelated to the opponent, that happens to
    start a few games before facing "other opponents" for the rest of the
    stretch, should NOT show up as a large adjusted effect even though the
    raw split is confounded by it."""
    sport, season, home, opp_a, opp_b, player = _seed_club(db_session)
    round_number = 1
    start = BASE

    def _play(disposals, opponent):
        nonlocal round_number, start
        match = _add_match(db_session, sport, season, round_number, home, opponent, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=opponent, disposals=disposals)
        round_number += 1
        start += timedelta(days=7)

    for _ in range(10):  # stable baseline period, against opp_a
        _play(20, opp_a)
    for _ in range(5):  # form spike begins, still against opp_a
        _play(30, opp_a)
    for _ in range(6):  # now against opp_b, form spike simply continues
        _play(30, opp_b)

    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)

    assert analysis.against_opponent.games == 15
    assert analysis.against_other_opponents.games == 6
    # Raw split is confounded: "other" games are entirely at the spiked
    # level, while "against" games average in the earlier, lower period too.
    assert analysis.raw_difference < -5
    assert analysis.adjusted_effect.available is True
    assert abs(analysis.adjusted_effect.value) < abs(analysis.raw_difference) / 2


def test_adjusted_effect_baselines_include_opponent_unknown_games(db_session):
    """The trailing-form baseline must be computed over the player's FULL
    club history (including any opponent-unknown rows), not just the rows
    with a known opponent - otherwise recent form context would have gaps
    purely because of missing opponent labels."""
    sport, season, home, opp_a, opp_b, player = _seed_club(db_session)
    round_number = 1
    start = BASE

    def _play(disposals, opponent):
        nonlocal round_number, start
        match = _add_match(db_session, sport, season, round_number, home, opponent or opp_b, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=opponent, disposals=disposals)
        round_number += 1
        start += timedelta(days=7)

    for _ in range(10):
        _play(20, opp_a)
    for _ in range(3):  # opponent-unknown rows interleaved - contribute to form history only
        _play(20, None)
    for _ in range(6):
        _play(20, opp_b)

    analysis = build_opponent_context_analysis(db_session, player.id, opp_a.id)
    # All values are a flat 20, so with no real change the adjusted effect
    # should be available (enough baseline-eligible known-opponent games)
    # and close to zero, not None/unavailable due to a baseline gap.
    assert analysis.against_opponent.games == 10
    assert analysis.against_other_opponents.games == 6
    assert analysis.adjusted_effect.available is True
    assert abs(analysis.adjusted_effect.value) < 0.5
