"""Point-in-time correctness tests for disposal_features.py — the leakage
discipline this whole disposal-prediction stage depends on. Uses
synthetic PlayerGameRow/TeamGameRow objects directly (no DB needed) so
these tests are fast and exercise the feature builder in isolation.
"""

from datetime import datetime, timedelta, timezone

from app.player_modelling.disposal_data import PlayerGameRow, TeamGameRow
from app.player_modelling.disposal_features import DisposalFeatureBuilder

BASE_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _row(
    player_id=1,
    match_id=1,
    team_id=10,
    opponent_team_id=20,
    season_year=2020,
    round_number=1,
    is_home=True,
    venue_id=1,
    days_offset=0,
    disposals=15,
    tog=80,
    kicks=8,
    handballs=7,
    subbed_on=False,
    subbed_off=False,
) -> PlayerGameRow:
    return PlayerGameRow(
        player_id=player_id,
        match_id=match_id,
        team_id=team_id,
        opponent_team_id=opponent_team_id,
        season_year=season_year,
        round_number=round_number,
        is_final=False,
        is_home=is_home,
        venue_id=venue_id,
        scheduled_start=BASE_TIME + timedelta(days=days_offset),
        disposals=disposals,
        kicks=kicks,
        handballs=handballs,
        marks=2,
        tackles=3,
        clearances=1,
        inside_50s=2,
        contested_possessions=5,
        uncontested_possessions=6,
        time_on_ground_pct=tog,
        subbed_on=subbed_on,
        subbed_off=subbed_off,
    )


def test_first_game_has_no_history_and_none_rolling_features():
    rows = [_row(match_id=1, days_offset=0, disposals=20)]
    features = DisposalFeatureBuilder().build(rows)
    assert features[0].games_of_history == 0
    assert features[0].features["disposals_last3_avg"] is None
    assert features[0].features["disposals_career_avg"] is None


def test_features_use_only_strictly_prior_games_not_the_target_game_itself():
    """The core leakage guarantee: a row's own disposals/stats must never
    appear in its own features."""
    rows = [
        _row(match_id=1, days_offset=0, disposals=10),
        _row(match_id=2, days_offset=7, disposals=20),
        _row(match_id=3, days_offset=14, disposals=100),  # extreme value - would obviously distort game 2's features if leaked
    ]
    features = DisposalFeatureBuilder().build(rows)
    game2 = features[1]
    # game 2's last3 average must reflect ONLY game 1 (10), not itself (20) or game 3 (100)
    assert game2.features["disposals_last3_avg"] == 10
    assert game2.games_of_history == 1


def test_appending_a_future_game_does_not_change_earlier_predictions():
    """Building features with N games, then again with N+1 games (a later
    season appended), must produce byte-identical features for the first N
    rows - the concrete form of "appending a future season must not alter
    previous predictions" from the stage brief."""
    rows_a = [
        _row(match_id=1, days_offset=0, disposals=12),
        _row(match_id=2, days_offset=7, disposals=18),
    ]
    rows_b = rows_a + [_row(match_id=3, days_offset=14, disposals=99)]

    features_a = DisposalFeatureBuilder().build(rows_a)
    features_b = DisposalFeatureBuilder().build(rows_b)

    assert features_a[0].features == features_b[0].features
    assert features_a[1].features == features_b[1].features


def test_tog_features_never_use_the_target_matchs_own_tog():
    """Adversarial: give the TARGET (3rd) game an extreme TOG (100) and
    verify the computed TOG features for that row reflect only its own
    PRIOR games (60, 70), not 100."""
    rows = [
        _row(match_id=1, days_offset=0, tog=60, disposals=10),
        _row(match_id=2, days_offset=7, tog=70, disposals=12),
        _row(match_id=3, days_offset=14, tog=100, disposals=30),
    ]
    features = DisposalFeatureBuilder().build(rows)
    game3 = features[2]
    assert game3.features["tog_last3_avg"] == 65  # avg(60, 70), never touches 100
    assert game3.features["tog_last5_avg"] == 65


def test_disposal_history_carries_across_a_mid_career_trade():
    """A player's rolling disposal history must NOT reset just because
    their team_id changes (a trade) - only current-match team context
    (team_recent_disposals_avg etc.) should reflect the new team."""
    rows = [
        _row(match_id=1, team_id=10, opponent_team_id=20, days_offset=0, disposals=15),
        _row(match_id=2, team_id=10, opponent_team_id=30, days_offset=7, disposals=17),
        _row(match_id=3, team_id=99, opponent_team_id=20, days_offset=14, disposals=19),  # traded to team 99
    ]
    features = DisposalFeatureBuilder().build(rows)
    game3 = features[2]
    assert game3.games_of_history == 2  # both prior games count, regardless of team
    assert game3.features["disposals_last3_avg"] == 16  # avg(15, 17)
    assert game3.team_id == 99  # this row's own team is still the CURRENT (new) team


def test_season_to_date_average_resets_each_season():
    rows = [
        _row(match_id=1, season_year=2020, days_offset=0, disposals=10),
        _row(match_id=2, season_year=2020, days_offset=7, disposals=20),
        _row(match_id=3, season_year=2021, days_offset=365, disposals=30),
    ]
    features = DisposalFeatureBuilder().build(rows)
    game3 = features[2]  # first game of a new season
    assert game3.features["disposals_season_avg"] is None  # no PRIOR games in 2021 yet
    assert game3.features["disposals_career_avg"] == 15  # career avg still spans both seasons


def test_season_scale_factor_only_affects_history_never_the_rows_own_target():
    """Section 19's 2020-adjustment mechanism: scaling 2020's contribution
    to rolling HISTORY must never change what a 2020 row is itself scored
    against (its `disposals` field, the actual target)."""
    rows = [
        _row(match_id=1, season_year=2020, days_offset=0, disposals=10),
        _row(match_id=2, season_year=2021, days_offset=365, disposals=20),
    ]
    unadjusted = DisposalFeatureBuilder().build(rows)
    scaled = DisposalFeatureBuilder(season_scale_factors={2020: 1.5}).build(rows)

    # the 2020 row's own actual disposals are identical in both cases
    assert unadjusted[0].disposals == scaled[0].disposals == 10

    # but the 2021 row's HISTORY of the 2020 game is scaled up
    assert unadjusted[1].features["disposals_last3_avg"] == 10
    assert scaled[1].features["disposals_last3_avg"] == 15  # 10 * 1.5


def test_venue_environment_uses_shrinkage_toward_league_average_with_little_history():
    rows = [_row(match_id=1, venue_id=5, days_offset=0, disposals=10)]
    features = DisposalFeatureBuilder().build(rows)
    # zero venue history observed before this row -> should equal the league fallback exactly
    from app.player_modelling.disposal_features import LEAGUE_AVG_DISPOSALS_FALLBACK

    assert features[0].features["venue_disposals_env"] == LEAGUE_AVG_DISPOSALS_FALLBACK


def test_team_and_opponent_context_use_prior_team_matches_only():
    team_rows = [
        TeamGameRow(team_id=10, opponent_team_id=20, match_id=1, season_year=2020, scheduled_start=BASE_TIME, disposals=150),
        TeamGameRow(team_id=20, opponent_team_id=10, match_id=1, season_year=2020, scheduled_start=BASE_TIME, disposals=140),
        TeamGameRow(
            team_id=10, opponent_team_id=30, match_id=2, season_year=2020, scheduled_start=BASE_TIME + timedelta(days=7), disposals=160
        ),
        TeamGameRow(
            team_id=30, opponent_team_id=10, match_id=2, season_year=2020, scheduled_start=BASE_TIME + timedelta(days=7), disposals=130
        ),
    ]
    player_rows = [
        _row(player_id=1, match_id=2, team_id=10, opponent_team_id=30, days_offset=7, disposals=15),
    ]
    features = DisposalFeatureBuilder().build(player_rows, team_rows)
    row = features[0]
    # team 10's own recent disposal avg entering match 2 = its match-1 total (150), not match 2's own (160)
    assert row.features["team_recent_disposals_avg"] == 150
    # opponent (team 30) has no prior matches at all before match 2 -> None, not guessed
    assert row.features["opponent_disposals_conceded_avg"] is None


def test_subbed_players_still_produce_a_valid_row_with_no_special_casing_of_target_disposals():
    rows = [_row(match_id=1, disposals=5, tog=25, subbed_on=True, days_offset=0)]
    features = DisposalFeatureBuilder().build(rows)
    assert features[0].disposals == 5  # actual outcome unchanged by subbed_on flag
