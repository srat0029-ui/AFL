"""Tests for app/player_modelling/player_context_analysis.py - the
with/without-teammate split, the honestly-gated adjusted effect, sample-size
confidence tiering, and point-in-time integrity of the trailing-form
baseline.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.player_context_analysis import (
    MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT,
    PlayerContextConfidenceTier,
    build_player_context_analysis,
    compute_trailing_baselines,
)

BASE = datetime(2024, 3, 1, tzinfo=timezone.utc)


def _seed_club(db, suffix=""):
    # Sport.code is globally unique, so a test that needs two independent
    # scenarios in one db_session (e.g. comparing confidence tiers) must
    # vary it - suffix defaults to "" for the common single-scenario case.
    sport = Sport(code=f"AFL{suffix}", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2024)
    db.add(season)
    db.flush()
    home = Team(sport_id=sport.id, name="North Melbourne", short_name="NM")
    away_a = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away_b = Team(sport_id=sport.id, name="Essendon", short_name="ESS")
    db.add_all([home, away_a, away_b])
    db.flush()
    player = Player(sport_id=sport.id, display_name="Harry Sheezel", source="afltables", source_player_id=f"players/S/Sheezel{suffix}.html")
    teammate = Player(sport_id=sport.id, display_name="Luke Davies-Uniacke", source="afltables", source_player_id=f"players/D/Davies-Uniacke{suffix}.html")
    db.add_all([player, teammate])
    db.commit()
    return sport, season, home, [away_a, away_b], player, teammate


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


def _add_stat(db, *, player, match, team, opponent_team, disposals, goals=1, tog=85, source="afltables"):
    row = PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=team.id, opponent_team_id=opponent_team.id,
        source=source, recorded_at=match.scheduled_start, disposals=disposals, goals=goals, time_on_ground_pct=tog,
    )
    db.add(row)
    db.commit()
    return row


def _build_scenario(db, *, disposals_with, disposals_without, suffix=""):
    """Seeds `player` at one club across len(disposals_with) + len(disposals_without)
    games, alternating whether `teammate` also has a row for that match, in
    chronological order (all "with" games first, then all "without" - the
    exact interleaving doesn't matter for the raw split, only for the
    trailing-baseline tests below, which build their own precise sequence).
    """
    sport, season, home, aways, player, teammate = _seed_club(db, suffix=suffix)
    round_number = 1
    start = BASE
    for d in disposals_with:
        match = _add_match(db, sport, season, round_number, home, aways[0], start)
        _add_stat(db, player=player, match=match, team=home, opponent_team=aways[0], disposals=d)
        _add_stat(db, player=teammate, match=match, team=home, opponent_team=aways[0], disposals=15)
        round_number += 1
        start += timedelta(days=7)
    for d in disposals_without:
        match = _add_match(db, sport, season, round_number, home, aways[1], start)
        _add_stat(db, player=player, match=match, team=home, opponent_team=aways[1], disposals=d)
        round_number += 1
        start += timedelta(days=7)
    return player, teammate


def test_raw_split_means_and_medians(db_session):
    player, teammate = _build_scenario(db_session, disposals_with=[20, 22, 24, 26, 28, 30], disposals_without=[30, 32, 34, 36, 38, 40])
    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert analysis.with_teammate.games == 6
    assert analysis.without_teammate.games == 6
    assert analysis.with_teammate.mean == sum([20, 22, 24, 26, 28, 30]) / 6
    assert analysis.without_teammate.mean == sum([30, 32, 34, 36, 38, 40]) / 6
    assert analysis.raw_difference == analysis.without_teammate.mean - analysis.with_teammate.mean
    assert analysis.raw_difference > 0


def test_milestone_rates_computed_per_group(db_session):
    player, teammate = _build_scenario(db_session, disposals_with=[10, 15, 20, 25], disposals_without=[25, 30, 30, 35])
    analysis = build_player_context_analysis(db_session, player.id, teammate.id, thresholds=[15, 25])

    assert analysis.with_teammate.milestone_rates[15] == 3 / 4
    assert analysis.with_teammate.milestone_rates[25] == 1 / 4
    assert analysis.without_teammate.milestone_rates[15] == 1.0
    assert analysis.without_teammate.milestone_rates[25] == 1.0


def test_time_on_ground_averaged_only_over_non_null(db_session):
    sport, season, home, aways, player, teammate = _seed_club(db_session)
    m1 = _add_match(db_session, sport, season, 1, home, aways[0], BASE)
    _add_stat(db_session, player=player, match=m1, team=home, opponent_team=aways[0], disposals=20, tog=90)
    m2 = _add_match(db_session, sport, season, 2, home, aways[0], BASE + timedelta(days=7))
    row2 = PlayerMatchStat(
        player_id=player.id, match_id=m2.id, team_id=home.id, opponent_team_id=aways[0].id,
        source="afltables", recorded_at=m2.scheduled_start, disposals=22, goals=1, time_on_ground_pct=None,
    )
    db_session.add(row2)
    db_session.commit()

    analysis = build_player_context_analysis(db_session, player.id, teammate.id)
    assert analysis.without_teammate.time_on_ground_sample_size == 1
    assert analysis.without_teammate.average_time_on_ground_pct == 90


def test_stat_query_param_selects_goals(db_session):
    player, teammate = _build_scenario(db_session, disposals_with=[20, 22], disposals_without=[24, 26])
    # goals were set to 1 for every row by _add_stat's default
    analysis = build_player_context_analysis(db_session, player.id, teammate.id, stat="goals", thresholds=[1, 2])
    assert analysis.stat == "goals"
    assert analysis.with_teammate.mean == 1.0
    assert analysis.with_teammate.milestone_rates[1] == 1.0
    assert analysis.with_teammate.milestone_rates[2] == 0.0


def test_no_recorded_stats_returns_insufficient_history_not_an_error(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Rookie", source="afltables", source_player_id="players/R/Rookie.html")
    teammate = Player(sport_id=sport.id, display_name="Vet", source="afltables", source_player_id="players/V/Vet.html")
    db_session.add_all([player, teammate])
    db_session.commit()

    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert analysis.with_teammate.games == 0
    assert analysis.without_teammate.games == 0
    assert analysis.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY
    assert analysis.adjusted_effect.available is False
    assert "No recorded match statistics" in analysis.adjusted_effect.explanation


def test_never_teammates_reports_zero_with_games_and_a_warning(db_session):
    """Player has games, but the requested teammate never has a row for
    any of them - a real, meaningful "no" answer, not an error."""
    sport, season, home, aways, player, teammate = _seed_club(db_session)
    m1 = _add_match(db_session, sport, season, 1, home, aways[0], BASE)
    _add_stat(db_session, player=player, match=m1, team=home, opponent_team=aways[0], disposals=20)

    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert analysis.with_teammate.games == 0
    assert analysis.without_teammate.games == 1
    assert any("teammate played alongside" in w for w in analysis.confidence.warnings)


def test_teammate_on_different_team_in_same_match_does_not_count_as_in(db_session):
    """A player traded away and now playing for the OPPONENT in a match
    must never be counted as a teammate for that match."""
    sport, season, home, aways, player, teammate = _seed_club(db_session)
    m1 = _add_match(db_session, sport, season, 1, home, aways[0], BASE)
    _add_stat(db_session, player=player, match=m1, team=home, opponent_team=aways[0], disposals=20)
    # teammate played for the OPPONENT in this exact match
    _add_stat(db_session, player=teammate, match=m1, team=aways[0], opponent_team=home, disposals=15)

    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert analysis.with_teammate.games == 0
    assert analysis.without_teammate.games == 1


def test_analysis_restricted_to_players_most_recent_club(db_session):
    """A player who changed clubs should only be compared within their
    CURRENT club's games, per PlayerMatchStat.team_id being the source of
    truth (not players.current_team_id)."""
    sport, season, home, aways, player, teammate = _seed_club(db_session)
    old_club = Team(sport_id=sport.id, name="Western Bulldogs", short_name="WB")
    db_session.add(old_club)
    db_session.commit()

    # Earlier stint at old_club (no teammate involvement at all there).
    m_old = _add_match(db_session, sport, season, 1, old_club, aways[0], BASE)
    _add_stat(db_session, player=player, match=m_old, team=old_club, opponent_team=aways[0], disposals=5)

    # Later, traded to `home`, teammate now present.
    m_new = _add_match(db_session, sport, season, 2, home, aways[0], BASE + timedelta(days=200))
    _add_stat(db_session, player=player, match=m_new, team=home, opponent_team=aways[0], disposals=25)
    _add_stat(db_session, player=teammate, match=m_new, team=home, opponent_team=aways[0], disposals=15)

    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert analysis.team_id == home.id
    assert analysis.with_teammate.games == 1
    assert analysis.without_teammate.games == 0  # the old-club game is excluded entirely


def test_unknown_player_raises_lookup_error(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    teammate = Player(sport_id=sport.id, display_name="Vet", source="afltables", source_player_id="players/V/Vet.html")
    db_session.add(teammate)
    db_session.commit()

    import pytest

    with pytest.raises(LookupError):
        build_player_context_analysis(db_session, 999999, teammate.id)


class TestTrailingBaselinePointInTimeIntegrity:
    """Direct unit tests of compute_trailing_baselines - no DB needed,
    since it operates purely on ordered row-like objects with .id and a
    stat attribute."""

    def _rows(self, disposals: list[int]):
        return [SimpleNamespace(id=i, disposals=v) for i, v in enumerate(disposals)]

    def test_first_games_have_no_baseline_until_min_games_reached(self):
        rows = self._rows([10, 20, 30, 40, 50])
        baselines = compute_trailing_baselines(rows, "disposals", window=10, min_games=3)
        assert baselines[0] is None
        assert baselines[1] is None
        assert baselines[2] is None  # only 2 prior games so far
        assert baselines[3] is not None  # 3 prior games (10, 20, 30) now available

    def test_baseline_never_includes_the_current_or_a_future_game(self):
        rows = self._rows([10, 20, 30, 40, 50])
        baselines = compute_trailing_baselines(rows, "disposals", window=10, min_games=3)
        # Row 3's baseline must be exactly mean(10, 20, 30) - never 40 (itself) or 50 (future).
        assert baselines[3] == (10 + 20 + 30) / 3
        # Row 4's baseline must be exactly mean(10, 20, 30, 40) - never 50 (future).
        assert baselines[4] == (10 + 20 + 30 + 40) / 4

    def test_window_truncates_to_the_most_recent_n_games_only(self):
        rows = self._rows([10, 20, 30, 40, 50, 999])
        baselines = compute_trailing_baselines(rows, "disposals", window=3, min_games=3)
        # Row 5's baseline uses only the immediately preceding 3 games (30, 40, 50), not 10/20 too.
        assert baselines[5] == (30 + 40 + 50) / 3

    def test_null_stat_values_are_skipped_when_building_history(self):
        rows = [
            SimpleNamespace(id=0, disposals=10),
            SimpleNamespace(id=1, disposals=None),
            SimpleNamespace(id=2, disposals=20),
            SimpleNamespace(id=3, disposals=30),
            SimpleNamespace(id=4, disposals=40),
        ]
        baselines = compute_trailing_baselines(rows, "disposals", window=10, min_games=3)
        # Row 4 should see exactly the three non-null prior values (10, 20, 30), skipping row 1's None.
        assert baselines[4] == (10 + 20 + 30) / 3


def test_adjusted_effect_unavailable_below_minimum_sample_per_group(db_session):
    # Only 2 "without" games - below MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT (5).
    assert MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT == 5
    player, teammate = _build_scenario(
        db_session,
        disposals_with=[20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31],
        disposals_without=[35, 36],
    )
    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert analysis.adjusted_effect.available is False
    assert "Need at least" in analysis.adjusted_effect.explanation


def test_adjusted_effect_available_and_isolates_recent_form(db_session):
    """Construct a player who has a genuine FORM SPIKE (unrelated to the
    teammate) that happens to start a few games before the teammate goes
    out, and continues unchanged once the teammate is out. The raw split
    is confounded by this (it compares the "out" games, all at the
    elevated level, against an "in" average dragged down by the earlier,
    lower pre-spike games). The form-adjusted residual should correctly
    show the "out" games are NOT meaningfully elevated relative to the
    player's already-elevated recent form, so it should be much smaller
    in magnitude than the raw difference.
    """
    sport, season, home, aways, player, teammate = _seed_club(db_session)
    round_number = 1
    start = BASE

    def _play(disposals, opponent, teammate_in):
        nonlocal round_number, start
        match = _add_match(db_session, sport, season, round_number, home, opponent, start)
        _add_stat(db_session, player=player, match=match, team=home, opponent_team=opponent, disposals=disposals)
        if teammate_in:
            _add_stat(db_session, player=teammate, match=match, team=home, opponent_team=opponent, disposals=15)
        round_number += 1
        start += timedelta(days=7)

    for _ in range(10):  # stable baseline period, teammate in
        _play(20, aways[0], teammate_in=True)
    for _ in range(5):  # form spike begins, teammate still in
        _play(30, aways[0], teammate_in=True)
    for _ in range(6):  # teammate now out, form spike simply continues unchanged
        _play(30, aways[1], teammate_in=False)

    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert analysis.with_teammate.games == 15
    assert analysis.without_teammate.games == 6
    # Raw split is confounded: "out" games are entirely at the spiked
    # level, while "in" games average in the earlier, lower period too.
    assert analysis.raw_difference > 5
    assert analysis.adjusted_effect.available is True
    # The adjusted estimate should be much smaller - the "out" games are a
    # continuation of already-elevated form, not a real extra bump.
    assert abs(analysis.adjusted_effect.value) < abs(analysis.raw_difference) / 2


def test_role_analysis_always_reported_unavailable(db_session):
    player, teammate = _build_scenario(db_session, disposals_with=[20], disposals_without=[25])
    analysis = build_player_context_analysis(db_session, player.id, teammate.id)
    assert analysis.role_analysis_available is False
    assert "No structured position/role data" in analysis.role_analysis_explanation


def test_confounders_report_what_was_and_was_not_considered(db_session):
    player, teammate = _build_scenario(db_session, disposals_with=[20], disposals_without=[25])
    analysis = build_player_context_analysis(db_session, player.id, teammate.id)
    assert analysis.confounders["recent_form"].considered is True
    assert analysis.confounders["opponent_strength"].considered is False
    assert analysis.confounders["opponent_strength"].reason
    assert analysis.confounders["venue"].considered is False
    assert analysis.confounders["role"].considered is False


def test_evidence_rows_are_auditable_and_newest_first(db_session):
    player, teammate = _build_scenario(db_session, disposals_with=[20, 22], disposals_without=[24])
    analysis = build_player_context_analysis(db_session, player.id, teammate.id)

    assert len(analysis.evidence) == 3
    starts = [e.scheduled_start for e in analysis.evidence]
    assert starts == sorted(starts, reverse=True)
    for row in analysis.evidence:
        assert row.opponent_name is not None
        assert row.team_name == "North Melbourne"
        assert row.season_year == 2024
    without_row = next(e for e in analysis.evidence if e.opponent_name == "Essendon")
    assert without_row.teammate_played is False
    with_row = next(e for e in analysis.evidence if e.opponent_name == "Carlton")
    assert with_row.teammate_played is True


def test_confidence_tier_escalates_with_sample_size(db_session):
    small_player, small_teammate = _build_scenario(db_session, disposals_with=[20, 21], disposals_without=[25])
    small = build_player_context_analysis(db_session, small_player.id, small_teammate.id)
    assert small.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY

    bigger_with = [20 + i for i in range(20)]
    bigger_without = [25 + i for i in range(20)]
    bigger_player, bigger_teammate = _build_scenario(db_session, disposals_with=bigger_with, disposals_without=bigger_without, suffix="2")
    bigger = build_player_context_analysis(db_session, bigger_player.id, bigger_teammate.id)
    assert bigger.confidence.tier is PlayerContextConfidenceTier.HIGHER
