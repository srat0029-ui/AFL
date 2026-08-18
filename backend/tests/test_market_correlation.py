"""Tests for cross-market correlation families (Market Integrity stage,
Section 6) — the fix for the brief's own "Collingwood H2H + Collingwood
+24.5 counted as two opinions" example."""

from app.player_modelling.market_correlation import (
    CORRELATION_SAME_TEAM_PLAYERS,
    CORRELATION_TEAM_AND_TOTAL,
    CORRELATION_TEAM_DIRECTIONAL,
    compute_market_correlations,
    market_correlation_labels,
    strong_correlation_group_key,
)


def _team_opp(match_id, market_type, selection, team_id, label=None):
    return {
        "opportunity_type": "team",
        "match_id": match_id,
        "market_type": market_type,
        "selection": selection,
        "team_id": team_id,
        "label": label or f"{selection} {market_type}",
        "player_id": None,
    }


def _player_opp(match_id, player_id, team_id, label):
    return {
        "opportunity_type": "player",
        "match_id": match_id,
        "market_type": "player_disposals",
        "player_id": player_id,
        "team_id": team_id,
        "selection": None,
        "label": label,
    }


def test_h2h_and_line_same_team_are_strongly_correlated():
    h2h = _team_opp(1, "h2h", "Collingwood", team_id=10, label="Collingwood to win")
    line = _team_opp(1, "line", "Collingwood", team_id=10, label="Collingwood +24.5")
    correlations = compute_market_correlations([h2h, line])
    labels_h2h = market_correlation_labels(h2h, correlations)
    labels_line = market_correlation_labels(line, correlations)
    assert any("strongly related" in l for l in labels_h2h)
    assert any("Collingwood +24.5" in l for l in labels_h2h)
    assert any("Collingwood to win" in l for l in labels_line)


def test_different_teams_h2h_not_correlated():
    a = _team_opp(1, "h2h", "Collingwood", team_id=10)
    b = _team_opp(1, "h2h", "Carlton", team_id=11)
    correlations = compute_market_correlations([a, b])
    assert market_correlation_labels(a, correlations) == []
    assert market_correlation_labels(b, correlations) == []


def test_h2h_and_total_are_moderately_correlated_not_strong():
    h2h = _team_opp(1, "h2h", "Collingwood", team_id=10)
    total = _team_opp(1, "total", "over", team_id=None, label="over 181.5 total points")
    correlations = compute_market_correlations([h2h, total])
    labels = market_correlation_labels(h2h, correlations)
    assert any("moderately related" in l for l in labels)
    assert not any("strongly related" in l for l in labels)


def test_different_matches_never_correlated():
    a = _team_opp(1, "h2h", "Collingwood", team_id=10)
    b = _team_opp(2, "line", "Collingwood", team_id=10)
    correlations = compute_market_correlations([a, b])
    assert market_correlation_labels(a, correlations) == []


def test_same_team_players_moderately_correlated():
    p1 = _player_opp(1, player_id=100, team_id=10, label="Player A 20+ Disposals")
    p2 = _player_opp(1, player_id=101, team_id=10, label="Player B 15+ Disposals")
    correlations = compute_market_correlations([p1, p2])
    labels = market_correlation_labels(p1, correlations)
    assert any("Same team" in l for l in labels)


def test_different_team_players_not_correlated():
    p1 = _player_opp(1, player_id=100, team_id=10, label="Player A")
    p2 = _player_opp(1, player_id=101, team_id=11, label="Player B")
    correlations = compute_market_correlations([p1, p2])
    assert market_correlation_labels(p1, correlations) == []


def test_alternate_lines_of_same_player_not_treated_as_cross_market_pair():
    p1 = _player_opp(1, player_id=100, team_id=10, label="Player A 20+")
    p2 = _player_opp(1, player_id=100, team_id=10, label="Player A 25+")
    correlations = compute_market_correlations([p1, p2])
    # Same player -> handled as one alternate-line family elsewhere, not a
    # cross-market correlation pair here.
    assert market_correlation_labels(p1, correlations) == []


def test_strong_correlation_group_key_groups_h2h_and_line_same_team():
    h2h = _team_opp(1, "h2h", "Collingwood", team_id=10)
    line = _team_opp(1, "line", "Collingwood", team_id=10)
    assert strong_correlation_group_key(h2h) == strong_correlation_group_key(line)


def test_strong_correlation_group_key_none_for_total_and_player():
    total = _team_opp(1, "total", "over", team_id=None)
    player = _player_opp(1, player_id=100, team_id=10, label="x")
    assert strong_correlation_group_key(total) is None
    assert strong_correlation_group_key(player) is None


def test_strong_correlation_group_key_differs_across_teams():
    a = _team_opp(1, "h2h", "Collingwood", team_id=10)
    b = _team_opp(1, "h2h", "Carlton", team_id=11)
    assert strong_correlation_group_key(a) != strong_correlation_group_key(b)
