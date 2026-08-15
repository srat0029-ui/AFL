from datetime import datetime, timezone

import pytest

from app.modelling.features import (
    MIN_GAMES_STATS,
    STATS_FEATURE_NAMES,
    MatchFeatureInput,
    build_match_features,
)


def _match(
    match_id, year, month, day, home, away, home_score, away_score,
    home_cl=40, away_cl=40, home_i50=50, away_i50=50, home_cp=150, away_cp=150,
    home_tk=60, away_tk=60, home_mi50=5, away_mi50=5,
) -> MatchFeatureInput:
    home_goals, home_behinds = home_score // 6, home_score % 6
    away_goals, away_behinds = away_score // 6, away_score % 6
    return MatchFeatureInput(
        match_id=match_id, season_year=year,
        scheduled_start=datetime(year, month, day, tzinfo=timezone.utc),
        home_team_id=home, away_team_id=away, home_score=home_score, away_score=away_score,
        home_goals=home_goals, home_behinds=home_behinds, away_goals=away_goals, away_behinds=away_behinds,
        home_clearances=home_cl, away_clearances=away_cl,
        home_inside_50s=home_i50, away_inside_50s=away_i50,
        home_contested_possessions=home_cp, away_contested_possessions=away_cp,
        home_tackles=home_tk, away_tackles=away_tk,
        home_marks_inside_50=home_mi50, away_marks_inside_50=away_mi50,
    )


def test_first_match_has_no_derived_history_features():
    rows = build_match_features([_match(1, 2024, 3, 1, 1, 2, 90, 80)])
    row = rows[0]
    for name in STATS_FEATURE_NAMES:
        if name == "league_home_win_rate":
            continue  # has a value from the very first match (Beta prior)
        assert row.features[name] is None
    assert row.has_full_history is False


def test_league_home_win_rate_present_from_first_match():
    rows = build_match_features([_match(1, 2024, 3, 1, 1, 2, 90, 80)])
    assert rows[0].features["league_home_win_rate"] == 0.5  # Beta(1,1) prior


def test_advanced_stats_populate_once_both_sides_reach_minimum_history():
    # team 1 and team 2 play each other repeatedly (alternating home/away)
    # so both sides accumulate stats history at the same rate — the diff
    # feature needs BOTH sides to individually clear MIN_GAMES_STATS.
    matches = [
        _match(i, 2024, 3, 1 + i, 1 if i % 2 == 0 else 2, 2 if i % 2 == 0 else 1, 90, 80, home_cl=45, away_cl=40)
        for i in range(MIN_GAMES_STATS + 2)
    ]
    rows = build_match_features(matches)
    assert rows[MIN_GAMES_STATS - 1].features["clearance_differential_diff"] is None  # neither side has MIN_GAMES_STATS prior games yet
    assert rows[MIN_GAMES_STATS].features["clearance_differential_diff"] is not None  # both sides now have MIN_GAMES_STATS prior games


def test_rolling_form_reflects_only_strictly_prior_results():
    """Both teams accumulate 3 games of head-to-head history; the 4th
    match's feature snapshot must reflect exactly those 3 results, and must
    not be influenced by the 4th match's own (not-yet-known) outcome."""
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 100, 50),  # team1 (home) beats team2
        _match(2, 2024, 3, 8, 2, 1, 100, 50),  # team2 (home) beats team1
        _match(3, 2024, 3, 15, 1, 2, 100, 50),  # team1 (home) beats team2
        _match(4, 2024, 3, 22, 1, 2, 999, 1),  # team1 (home) crushes team2 — outcome must not leak backward or into its own features
    ]
    rows = build_match_features(matches)
    fourth = next(r for r in rows if r.match_id == 4)

    # team1's record entering match 4: [W, L, W] -> 2/3; team2's: [L, W, L] -> 1/3
    assert fourth.features["form_diff_5"] == pytest.approx(2 / 3 - 1 / 3)

    # re-run with match 4's own score changed drastically — its OWN feature
    # row must be identical, since features are computed before the result
    # is known, and match 4 was already the last match chronologically
    # (nothing later exists to leak from) — this instead checks robustness
    # of the snapshot-before-update ordering directly.
    alternate = matches[:3] + [_match(4, 2024, 3, 22, 1, 2, 1, 999)]
    alt_rows = build_match_features(alternate)
    alt_fourth = next(r for r in alt_rows if r.match_id == 4)
    assert alt_fourth.features["form_diff_5"] == fourth.features["form_diff_5"]


def test_appending_a_future_match_does_not_change_earlier_feature_rows():
    """Adversarial leakage test: build features for a fixed set of matches,
    then rebuild with one additional match appended strictly after all of
    them — every earlier row's features must be byte-identical."""
    base_matches = [
        _match(1, 2024, 3, 1, 1, 2, 100, 50, home_cl=45, away_cl=38),
        _match(2, 2024, 3, 8, 3, 4, 80, 90, home_cl=40, away_cl=44),
        _match(3, 2024, 3, 15, 1, 3, 70, 65, home_cl=42, away_cl=41),
        _match(4, 2024, 3, 22, 2, 4, 60, 88, home_cl=39, away_cl=46),
    ]
    future_match = _match(5, 2024, 4, 1, 1, 4, 200, 10, home_cl=60, away_cl=20)

    baseline_rows = build_match_features(base_matches)
    extended_rows = build_match_features(base_matches + [future_match])

    baseline_by_id = {r.match_id: r for r in baseline_rows}
    extended_by_id = {r.match_id: r for r in extended_rows}

    for match_id in (m.match_id for m in base_matches):
        assert extended_by_id[match_id] == baseline_by_id[match_id], (
            f"features for match {match_id} changed after appending a future match — leakage"
        )


def test_build_match_features_is_deterministic_across_reruns():
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 100, 50),
        _match(2, 2024, 3, 8, 3, 4, 80, 90),
        _match(3, 2024, 3, 15, 1, 3, 70, 65),
    ]
    assert build_match_features(matches) == build_match_features(matches)


def test_build_match_features_deterministic_regardless_of_input_order():
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 100, 50),
        _match(2, 2024, 3, 8, 3, 4, 80, 90),
        _match(3, 2024, 3, 15, 1, 3, 70, 65),
    ]
    forward = build_match_features(matches)
    reversed_input = build_match_features(list(reversed(matches)))
    assert forward == reversed_input


def test_elo_probability_attached_when_provided():
    matches = [_match(1, 2024, 3, 1, 1, 2, 90, 80)]
    rows = build_match_features(matches, elo_prob_by_match={1: 0.62})
    assert rows[0].features["elo_home_win_probability"] == 0.62


def test_missing_elo_probability_recorded_as_none_not_dropped():
    matches = [_match(1, 2024, 3, 1, 1, 2, 90, 80)]
    rows = build_match_features(matches, elo_prob_by_match={})  # match 1 not in the dict
    assert rows[0].features["elo_home_win_probability"] is None
    assert rows[0].has_full_history is False


def test_missing_advanced_stats_for_one_match_does_not_crash():
    matches = [
        _match(1, 2024, 3, 1, 1, 2, 100, 50, home_cl=45, away_cl=40),
        MatchFeatureInput(
            match_id=2, season_year=2024, scheduled_start=datetime(2024, 3, 8, tzinfo=timezone.utc),
            home_team_id=1, away_team_id=2, home_score=80, away_score=70,
            home_goals=13, home_behinds=2, away_goals=11, away_behinds=4,
            # no advanced stats for this match — simulates missing TeamMatchStat coverage
        ),
    ]
    rows = build_match_features(matches)
    assert len(rows) == 2  # both matches still produce a row
    assert rows[1].features["clearance_differential_diff"] is None
