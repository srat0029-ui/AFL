from app.providers.afl.round_labels import ROUND_NAME_BY_FINALS_KIND, RoundKind, parse_round_label


def test_parses_numbered_home_and_away_round():
    label = parse_round_label("5")
    assert label.kind is RoundKind.HOME_AND_AWAY
    assert label.round_number == 5
    assert label.is_final is False


def test_parses_r_prefixed_round_as_published_on_the_grid():
    """AFL Tables' own game-by-game grid publishes "R5", not "5" - the
    real, verbatim source format, not a convenience alias."""
    label = parse_round_label("R5")
    assert label.kind is RoundKind.HOME_AND_AWAY
    assert label.round_number == 5
    assert label.raw == "R5"


def test_parses_ef_as_finals_week_1():
    label = parse_round_label("EF")
    assert label.kind is RoundKind.FINALS_WEEK_1
    assert label.round_number is None
    assert label.is_final is True


def test_parses_qf_as_the_same_kind_as_ef():
    """Squiggle groups both under one round - see module docstring."""
    assert parse_round_label("QF").kind is parse_round_label("EF").kind


def test_parses_sf_pf_gf():
    assert parse_round_label("SF").kind is RoundKind.SEMI_FINALS
    assert parse_round_label("PF").kind is RoundKind.PRELIMINARY_FINAL
    assert parse_round_label("GF").kind is RoundKind.GRAND_FINAL


def test_strips_whitespace():
    label = parse_round_label("  12  ")
    assert label.round_number == 12


def test_unrecognised_label_returns_none_not_a_guess():
    assert parse_round_label("XX") is None
    assert parse_round_label("") is None
    assert parse_round_label("Round 5") is None


def test_every_finals_kind_has_a_round_name_mapping():
    for kind in (RoundKind.FINALS_WEEK_1, RoundKind.SEMI_FINALS, RoundKind.PRELIMINARY_FINAL, RoundKind.GRAND_FINAL):
        assert kind in ROUND_NAME_BY_FINALS_KIND
    assert RoundKind.HOME_AND_AWAY not in ROUND_NAME_BY_FINALS_KIND
