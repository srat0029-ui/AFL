"""Tests for opportunity family grouping + diversification (Sections 2-6
of the Weekly Opportunity Discovery stage): the fix for "all five top
opportunities are alternate disposal lines for the same player"."""

from app.player_modelling.opportunity_families import (
    compute_correlation_labels,
    diversify,
    family_key,
    group_into_families,
    price_advantage_pct,
    representative_score,
)


def _player_opp(player_id, player_name, match_id, market_type, threshold, price, score, n_bookmakers=1, bookmakers=None):
    return {
        "opportunity_type": "player",
        "match_id": match_id,
        "player_id": player_id,
        "player_name": player_name,
        "market_type": market_type,
        "threshold": threshold,
        "best_price": price,
        "n_bookmakers": n_bookmakers,
        "bookmakers": bookmakers or [{"bookmaker_name": "SportsBet", "price_decimal": price}],
        "opportunity_score": score,
        "selection": None,
        "line_value": None,
    }


def _team_opp(match_id, market_type, selection, price, score):
    return {
        "opportunity_type": "team",
        "match_id": match_id,
        "player_id": None,
        "market_type": market_type,
        "selection": selection,
        "line_value": None,
        "best_price": price,
        "n_bookmakers": 1,
        "bookmakers": [{"bookmaker_name": "TAB", "price_decimal": price}],
        "opportunity_score": score,
    }


def test_alternate_disposal_thresholds_share_one_family_key():
    a = _player_opp(1, "Darcy Gardiner", 100, "player_disposals", 12.5, 2.36, 20)
    b = _player_opp(1, "Darcy Gardiner", 100, "player_disposals", 13.5, 3.07, 25)
    assert family_key(a) == family_key(b)


def test_different_players_or_matches_are_different_families():
    a = _player_opp(1, "Darcy Gardiner", 100, "player_disposals", 12.5, 2.36, 20)
    b = _player_opp(2, "Jack Crisp", 100, "player_disposals", 12.5, 2.36, 20)
    c = _player_opp(1, "Darcy Gardiner", 200, "player_disposals", 12.5, 2.36, 20)
    assert family_key(a) != family_key(b)
    assert family_key(a) != family_key(c)


def test_disposals_and_goals_for_same_player_are_different_families():
    a = _player_opp(1, "X", 100, "player_disposals", 20.5, 1.9, 20)
    b = _player_opp(1, "X", 100, "player_goals", 1.5, 1.9, 20)
    assert family_key(a) != family_key(b)


def test_group_into_families_picks_highest_score_as_representative():
    lines = [
        _player_opp(1, "Darcy Gardiner", 100, "player_disposals", 12.5, 2.36, 23.8),
        _player_opp(1, "Darcy Gardiner", 100, "player_disposals", 13.5, 3.07, 27.6),
        _player_opp(1, "Darcy Gardiner", 100, "player_disposals", 14.5, 4.0, 28.2),
    ]
    families = group_into_families(lines, {100: "St Kilda v Gold Coast"})
    assert len(families) == 1
    fam = families[0]
    assert fam.representative["threshold"] == 14.5
    assert len(fam.alternates) == 2
    assert fam.label == "Darcy Gardiner / St Kilda v Gold Coast / disposals"


def test_extreme_short_price_is_penalised_in_representative_selection():
    # A near-certainty at $1.05 has a big raw score but should lose to a
    # sensibly-priced line even if its raw score is nominally lower.
    near_certain = _player_opp(1, "X", 100, "player_disposals", 5.5, 1.05, 40.0)
    normal = _player_opp(1, "X", 100, "player_disposals", 20.5, 2.0, 30.0)
    assert representative_score(near_certain) < representative_score(normal)


def test_extreme_long_price_is_penalised_in_representative_selection():
    longshot = _player_opp(1, "X", 100, "player_disposals", 40.5, 60.0, 40.0)
    normal = _player_opp(1, "X", 100, "player_disposals", 20.5, 2.0, 30.0)
    assert representative_score(longshot) < representative_score(normal)


def test_diversify_caps_at_two_headlines_per_player():
    families = []
    for i, market in enumerate(["player_disposals", "player_goals"]):
        families.append(group_into_families([_player_opp(1, "X", 100 + i, market, 20.5, 2.0, 50 - i)], {})[0])
    # a third family for the same player should be dropped
    families.append(group_into_families([_player_opp(1, "X", 102, "player_disposals", 15.5, 2.0, 10)], {})[0])
    # add extra families for OTHER players so the list isn't just player 1
    families.append(group_into_families([_player_opp(2, "Y", 103, "player_disposals", 20.5, 2.0, 5)], {})[0])

    selected = diversify(families, limit=10)
    player_1_count = sum(1 for f in selected if f.representative.get("player_id") == 1)
    assert player_1_count <= 2


def test_diversify_soft_caps_per_match_when_alternatives_exist():
    # 4 different players all in the same match, plus one strong option
    # from a different match - the same-match cap should defer some of
    # the 4 in favour of the other match's option once the cap is hit.
    families = []
    for player_id in range(1, 5):
        families += group_into_families(
            [_player_opp(player_id, f"Player{player_id}", 100, "player_disposals", 20.5, 2.0, 30 - player_id)], {}
        )
    families += group_into_families([_player_opp(5, "OtherMatch", 200, "player_disposals", 20.5, 2.0, 15)], {})

    selected = diversify(families, limit=4)
    match_100_count = sum(1 for f in selected if f.representative["match_id"] == 100)
    match_200_count = sum(1 for f in selected if f.representative["match_id"] == 200)
    assert match_100_count <= 3
    assert match_200_count >= 1


def test_diversify_backfills_from_same_match_when_no_alternatives_exist():
    # If ALL quality options come from one match, the soft cap should not
    # artificially shrink the list below what's genuinely available.
    families = []
    for player_id in range(1, 6):
        families += group_into_families(
            [_player_opp(player_id, f"Player{player_id}", 100, "player_disposals", 20.5, 2.0, 30 - player_id)], {}
        )
    selected = diversify(families, limit=5)
    assert len(selected) == 5


def test_correlation_labels_flag_same_player_and_same_match():
    fam_a = group_into_families([_player_opp(1, "X", 100, "player_disposals", 20.5, 2.0, 30)], {})[0]
    fam_b = group_into_families([_player_opp(1, "X", 101, "player_goals", 1.5, 2.0, 25)], {})[0]
    fam_c = group_into_families([_player_opp(2, "Y", 100, "player_disposals", 15.5, 2.0, 20)], {})[0]

    labels = compute_correlation_labels([fam_a, fam_b, fam_c])
    assert "Same player as another opportunity" in labels[fam_a.key]
    assert "Same player as another opportunity" in labels[fam_b.key]
    assert "Same match as another opportunity" in labels[fam_a.key]
    assert "Same match as another opportunity" in labels[fam_c.key]
    assert "Same player as another opportunity" not in labels[fam_c.key]


def test_correlation_labels_flag_remaining_alternate_lines():
    lines = [
        _player_opp(1, "X", 100, "player_disposals", 12.5, 2.0, 20),
        _player_opp(1, "X", 100, "player_disposals", 13.5, 2.0, 25),
    ]
    fam = group_into_families(lines, {})[0]
    labels = compute_correlation_labels([fam])
    assert any("alternate line" in label for label in labels[fam.key])


def test_price_advantage_pct_computed_vs_next_best():
    o = _player_opp(
        1, "X", 100, "player_disposals", 20.5, 2.15, 30,
        n_bookmakers=3,
        bookmakers=[
            {"bookmaker_name": "TAB", "price_decimal": 2.15},
            {"bookmaker_name": "SportsBet", "price_decimal": 1.95},
            {"bookmaker_name": "Betr", "price_decimal": 1.90},
        ],
    )
    pct = price_advantage_pct(o)
    assert pct == ((2.15 - 1.95) / 1.95) * 100.0


def test_price_advantage_pct_none_for_single_bookmaker():
    o = _player_opp(1, "X", 100, "player_disposals", 20.5, 2.0, 30)
    assert price_advantage_pct(o) is None


def test_team_markets_group_by_match_and_market_type():
    a = _team_opp(100, "total", "over", 1.9, 20)
    b = _team_opp(100, "total", "under", 1.9, 20)
    assert family_key(a) == family_key(b)
    c = _team_opp(100, "h2h", "Collingwood", 1.9, 20)
    assert family_key(a) != family_key(c)
