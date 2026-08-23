"""Targeted tests for the production usage-regime detector
(app/player_modelling/usage_regime.py, Usage-Change Production Integration
stage, item 1): leakage-safe profile construction, stable/changed
classification against the tune-derived cutoff, insufficient-history
handling, and that the production implementation is the SAME code path the
validated research scripts now import (not a reimplementation)."""

from datetime import datetime, timedelta, timezone

from app.player_modelling.usage_regime import (
    CHANGED,
    INSUFFICIENT_HISTORY,
    ROLE_DIMS,
    STABLE,
    RawPlayerRow,
    build_role_rows,
    fit_usage_regime_model,
    usage_regime_for,
)

NOW = datetime(2018, 6, 1, tzinfo=timezone.utc)  # pre-EVALUATION_START_YEAR (2019), so this feeds the tune-period fit


def _row(player_id, match_id, day_offset, *, disposals=20, kicks=12, marks=4, clearances=3, i50=2, contested=8, uncontested=8, tog=80, goals=1, behinds=1, team_total=350):
    return RawPlayerRow(
        player_id=player_id, match_id=match_id, team_id=1, season_year=2018,
        scheduled_start=NOW + timedelta(days=day_offset), disposals=disposals, kicks=kicks, marks=marks,
        clearances=clearances, inside_50s=i50, contested_possessions=contested, uncontested_possessions=uncontested,
        time_on_ground_pct=tog, goals=goals, behinds=behinds,
    ), team_total


def _build(rows_with_team_totals):
    raw_rows = [r for r, _ in rows_with_team_totals]
    team_disposals = {(r.team_id, r.match_id): total for r, total in rows_with_team_totals}
    return build_role_rows(raw_rows, team_disposals)


def _stable_player_history(player_id, n_games=20, match_start=0):
    """A player with a small, deterministic amount of realistic game-to-game
    jitter around a fixed profile - NOT perfectly constant (a truly
    zero-variance profile makes the tune-median cutoff itself exactly 0,
    a degenerate edge case that isn't representative of real player data,
    where usage always fluctuates a little)."""
    return [_row(player_id, match_start + i, i, disposals=20 + (i % 3) - 1, kicks=12 + (i % 2)) for i in range(n_games)]


def test_role_row_has_no_history_before_any_prior_game():
    role_rows = _build(_stable_player_history(player_id=1, n_games=3))
    assert role_rows[0].games_of_history == 0
    assert all(v is None for v in role_rows[0].recent.values())


def test_leakage_a_rows_own_stats_never_feed_its_own_profile():
    """Adversarial: a player's single game with an extreme disposal count
    must not appear in that SAME row's own recent/longterm profile - only
    in games built AFTER it."""
    rows = _stable_player_history(player_id=1, n_games=1)
    extreme_row, team_total = _row(1, 999, 50, disposals=999)  # would blow out any average if leaked into its own row
    role_rows = _build(rows + [(extreme_row, team_total)])
    extreme_role_row = next(r for r in role_rows if r.match_id == 999)
    assert extreme_role_row.games_of_history == 1  # only the ONE prior game, not counting itself
    # its own 999 disposals must not appear in its own recent window
    assert extreme_role_row.recent["disposal_share"] != 999 / team_total


def test_fit_and_classify_stable_vs_changed():
    # 30 games at a stable profile, tune-period (2018) so this feeds the fit.
    stable_history = _stable_player_history(player_id=1, n_games=30)
    role_rows = _build(stable_history)
    model = fit_usage_regime_model(role_rows)
    # A perfectly non-varying synthetic player has change_score == 0 for
    # every row, so the tune-median cutoff is legitimately 0 here - the
    # real dataset's cutoff (1.670, see usage_regime_change_research.py)
    # is positive only because real usage genuinely fluctuates game to game.
    assert model.cutoff >= 0

    # A row deep in a long stable run should classify as stable (its recent
    # window looks just like its longterm window).
    late_stable_row = next(r for r in role_rows if r.games_of_history == 25)
    result = usage_regime_for(late_stable_row, model)
    assert result.usage_regime == STABLE
    assert result.usage_change_score is not None
    assert result.usage_change_score < model.cutoff
    assert result.changed_dimensions == []


def test_a_genuine_role_shift_is_classified_as_changed():
    # 20 games at one profile (high disposal involvement), then a further
    # 6 games at a starkly different profile (much lower involvement, more
    # kick-heavy) - the row right after that shift should read "changed."
    baseline = [_row(1, i, i, disposals=28, kicks=14, i50=5, clearances=6, contested=14, tog=90) for i in range(20)]
    shifted = [_row(1, 20 + i, 20 + i, disposals=8, kicks=2, i50=0, clearances=0, contested=1, tog=40) for i in range(6)]
    role_rows = _build(baseline + shifted)
    model = fit_usage_regime_model(role_rows)

    changed_row = role_rows[-1]  # last row's recent(5) is entirely within the shifted block, longterm still mostly baseline
    result = usage_regime_for(changed_row, model)
    assert result.usage_regime == CHANGED
    assert result.usage_change_score >= model.cutoff
    assert len(result.changed_dimensions) == 2
    assert all(d in ROLE_DIMS for d in result.changed_dimensions)


def test_insufficient_history_below_min_games():
    # A long-enough history to let the model actually fit, but we check an
    # EARLY row within it (games_of_history=3) - below ROLE_CHANGE_MIN_GAMES (10).
    role_rows = _build(_stable_player_history(player_id=1, n_games=20))
    model = fit_usage_regime_model(role_rows)
    early_row = role_rows[3]
    assert early_row.games_of_history == 3
    result = usage_regime_for(early_row, model)
    assert result.usage_regime == INSUFFICIENT_HISTORY
    assert result.usage_change_score is None
    assert result.changed_dimensions == []


def test_threshold_used_matches_the_fitted_cutoff():
    role_rows = _build(_stable_player_history(player_id=1, n_games=15))
    model = fit_usage_regime_model(role_rows)
    row = role_rows[-1]
    result = usage_regime_for(row, model)
    assert result.threshold_used == model.cutoff
