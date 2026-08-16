"""Tests for AFLTablesPlayerStatsProvider using a fake transport and a
compact synthetic HTML fixture matching AFL Tables' real "game by game" grid
structure — verified against a real fetched 2024 Adelaide page (via
web.archive.org, since direct access was temporarily blocked at
verification time) before this provider was written. See the module
docstring in app/providers/afl/afltables_players.py.
"""

from app.providers.afl.afltables_players import AFLTablesPlayerStatsProvider
from app.providers.afl.round_labels import RoundKind

# Two players, three rounds (R2/R3/R4):
#   Alpha, Amy    - played full R2, subbed off R3, did not feature R4
#   Beta, Bob     - did not feature R2, subbed on R3, played full R4
# Marks table's round headers deliberately don't match the others (R2/R3
# only, no R4) - regression coverage for the "skip a misaligned table rather
# than misattribute a value" defensive behaviour.
SAMPLE_GBG_HTML = """
<html><body>
<table style="font: 12px Verdana;"><tr><td>Abbreviations key</td></tr></table>
<table class="sortable" border="2" width="100%"><thead><tr><th colspan="25">Disposals</th></tr>
<tr><th align="left">Player</th><th width="3%">R2</th><th width="3%">R3</th><th width="3%">R4</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">15</td><td align="center">8</td><td>&nbsp;</td><td align="center">23</td></tr>
<tr><td><a href="players/B/Bob_Beta.html">Beta, Bob</a></td><td>&nbsp;</td><td align="center">6</td><td align="center">20</td><td align="center">26</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="25">Kicks</th></tr>
<tr><th align="left">Player</th><th width="3%">R2</th><th width="3%">R3</th><th width="3%">R4</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">9</td><td align="center">5</td><td>&nbsp;</td><td align="center">14</td></tr>
<tr><td><a href="players/B/Bob_Beta.html">Beta, Bob</a></td><td>&nbsp;</td><td align="center">4</td><td align="center">12</td><td align="center">16</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="25">Handballs</th></tr>
<tr><th align="left">Player</th><th width="3%">R2</th><th width="3%">R3</th><th width="3%">R4</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">6</td><td align="center">3</td><td>&nbsp;</td><td align="center">9</td></tr>
<tr><td><a href="players/B/Bob_Beta.html">Beta, Bob</a></td><td>&nbsp;</td><td align="center">2</td><td align="center">8</td><td align="center">10</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="25">Goals</th></tr>
<tr><th align="left">Player</th><th width="3%">R2</th><th width="3%">R3</th><th width="3%">R4</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">1</td><td>&nbsp;</td><td>&nbsp;</td><td align="center">1</td></tr>
<tr><td><a href="players/B/Bob_Beta.html">Beta, Bob</a></td><td>&nbsp;</td><td>&nbsp;</td><td align="center">2</td><td align="center">2</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="25">Marks</th></tr>
<tr><th align="left">Player</th><th width="3%">R2</th><th width="3%">R3</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">3</td><td align="center">2</td><td align="center">5</td></tr>
<tr><td><a href="players/B/Bob_Beta.html">Beta, Bob</a></td><td>&nbsp;</td><td align="center">1</td><td align="center">1</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="25">Subs</th></tr>
<tr><th align="left">Player</th><th width="3%">R2</th><th width="3%">R3</th><th width="3%">R4</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">-</td><td align="center">Off</td><td>&nbsp;</td><td align="center">1/0</td></tr>
<tr><td><a href="players/B/Bob_Beta.html">Beta, Bob</a></td><td>&nbsp;</td><td align="center">On</td><td align="center">-</td><td align="center">1/0</td></tr>
</tbody></table>
</body></html>
"""


# Regression fixture for a real bug caught during the first live 2016-2025
# backfill run: every season before ~2019 uses this shape - a DIFFERENT
# colspan value than 2024's (varies with how many round columns that
# team-season happens to have — NOT a fixed page-format constant, which the
# original hardcoded `colspan="25"` regex wrongly assumed) — and no "Subs"
# table at all (verified genuinely absent, not malformed, on a real fetched
# 2017 Adelaide page). Every 2016/2017 team-season failed entirely under the
# old code before this fix.
SAMPLE_GBG_HTML_NO_SUBS_OLD_COLSPAN = """
<html><body>
<table class="sortable" border="2" width="100%"><thead><tr><th colspan="27">Disposals</th></tr>
<tr><th align="left">Player</th><th width="3%">R1</th><th width="3%">R2</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">19</td><td align="center">15</td><td align="center">34</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="27">Goals</th></tr>
<tr><th align="left">Player</th><th width="3%">R1</th><th width="3%">R2</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">3</td><td>&nbsp;</td><td align="center">3</td></tr>
</tbody></table>
</body></html>
"""


def _make_provider() -> AFLTablesPlayerStatsProvider:
    return AFLTablesPlayerStatsProvider(
        transport=lambda url: (200, "text/html", SAMPLE_GBG_HTML), request_delay_seconds=0
    )


def _make_provider_no_subs_old_colspan() -> AFLTablesPlayerStatsProvider:
    return AFLTablesPlayerStatsProvider(
        transport=lambda url: (200, "text/html", SAMPLE_GBG_HTML_NO_SUBS_OLD_COLSPAN), request_delay_seconds=0
    )


def test_older_season_format_without_subs_table_still_parses():
    lines = _make_provider_no_subs_old_colspan().get_team_season_player_stats("AFL", 2017, "Adelaide")
    rows = _by_player_round(lines)

    assert ("Alpha, Amy", 1) in rows
    assert ("Alpha, Amy", 2) in rows
    assert rows[("Alpha, Amy", 1)].stats["disposals"] == 19
    assert rows[("Alpha, Amy", 1)].stats["goals"] == 3
    assert rows[("Alpha, Amy", 2)].stats["goals"] == 0  # blank cell on a played round is a genuine zero


def test_older_season_format_defaults_subs_flags_to_false_not_fabricated():
    lines = _make_provider_no_subs_old_colspan().get_team_season_player_stats("AFL", 2017, "Adelaide")
    assert all(l.subbed_on is False and l.subbed_off is False for l in lines)


# Regression fixture for a second real bug caught in the same live backfill
# run: a team-season with enough round columns (a long finals run adds
# QF/SF/PF/GF columns) gets narrower per-column width — "2%" instead of the
# "3%" seen on every page sampled before this run — so a round-header regex
# hardcoding "3%" silently matched zero rounds and produced zero rows for
# that team-season, with no error raised at all (the most dangerous kind of
# parser bug: silently wrong, not loudly broken).
SAMPLE_GBG_HTML_NARROW_WIDTH = """
<html><body>
<table class="sortable" border="2" width="100%"><thead><tr><th colspan="29">Disposals</th></tr>
<tr><th align="left">Player</th><th width="2%">R1</th><th width="2%">R2</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">22</td><td align="center">17</td><td align="center">39</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="29">Subs</th></tr>
<tr><th align="left">Player</th><th width="2%">R1</th><th width="2%">R2</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">-</td><td align="center">-</td><td align="center">0/0</td></tr>
</tbody></table>
</body></html>
"""


def test_narrow_column_width_from_a_long_finals_run_still_parses():
    provider = AFLTablesPlayerStatsProvider(
        transport=lambda url: (200, "text/html", SAMPLE_GBG_HTML_NARROW_WIDTH), request_delay_seconds=0
    )
    lines = provider.get_team_season_player_stats("AFL", 2025, "Adelaide")
    rows = _by_player_round(lines)

    assert ("Alpha, Amy", 1) in rows
    assert ("Alpha, Amy", 2) in rows
    assert rows[("Alpha, Amy", 1)].stats["disposals"] == 22


def _by_player_round(lines):
    return {(l.player_name, l.round_label.round_number): l for l in lines}


def test_only_rounds_a_player_actually_featured_in_are_returned():
    lines = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    rows = _by_player_round(lines)

    assert ("Alpha, Amy", 2) in rows
    assert ("Alpha, Amy", 3) in rows
    assert ("Alpha, Amy", 4) not in rows  # blank Subs cell - did not feature

    assert ("Beta, Bob", 2) not in rows  # blank Subs cell
    assert ("Beta, Bob", 3) in rows
    assert ("Beta, Bob", 4) in rows


def test_subbed_on_and_off_flags_parsed_correctly():
    lines = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    rows = _by_player_round(lines)

    r2 = rows[("Alpha, Amy", 2)]
    assert r2.subbed_on is False and r2.subbed_off is False  # "-" = played, no sub event

    r3_off = rows[("Alpha, Amy", 3)]
    assert r3_off.subbed_off is True and r3_off.subbed_on is False

    r3_on = rows[("Beta, Bob", 3)]
    assert r3_on.subbed_on is True and r3_on.subbed_off is False


def test_stats_merged_correctly_across_tables():
    lines = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    rows = _by_player_round(lines)

    r2 = rows[("Alpha, Amy", 2)]
    assert r2.stats["disposals"] == 15
    assert r2.stats["kicks"] == 9
    assert r2.stats["handballs"] == 6
    assert r2.stats["goals"] == 1


def test_blank_cell_for_a_round_the_player_played_is_a_genuine_zero_not_missing():
    """Amy Alpha's Goals cell for round 3 is blank, but she's confirmed to
    have featured that round (Subs cell = "Off", not blank) - AFL Tables'
    own convention is that a blank stat cell for a played round means zero,
    not unknown."""
    lines = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    rows = _by_player_round(lines)

    r3 = rows[("Alpha, Amy", 3)]
    assert r3.stats["goals"] == 0


def test_misaligned_table_is_skipped_not_misattributed():
    """The Marks table only has R2/R3 columns (no R4), unlike every other
    table - its round_numbers won't match the Subs table's, so it must be
    silently excluded from stats entirely rather than risk shifting values
    onto the wrong round."""
    lines = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    rows = _by_player_round(lines)

    for line in rows.values():
        assert "marks" not in line.stats


def test_missing_stat_table_leaves_field_absent_not_guessed():
    """Tackles has no table at all in this fixture - confirm it's simply
    absent from every row's stats dict."""
    lines = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    assert all("tackles" not in l.stats for l in lines)


def test_player_source_id_and_metadata_populated():
    lines = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    rows = _by_player_round(lines)
    r2 = rows[("Alpha, Amy", 2)]

    assert r2.player_source_id == "players/A/Amy_Alpha.html"
    assert r2.sport_code == "AFL"
    assert r2.season_year == 2024
    assert r2.team_name == "Adelaide"
    assert r2.jumper_number is None  # not published on this page format


def test_unknown_sport_code_rejected():
    import pytest

    with pytest.raises(ValueError):
        _make_provider().get_team_season_player_stats("NRL", 2024, "Adelaide")


def test_unknown_team_name_rejected():
    import pytest

    with pytest.raises(ValueError):
        _make_provider().get_team_season_player_stats("AFL", 2024, "Not A Real Team")


def test_http_error_raises():
    import pytest

    provider = AFLTablesPlayerStatsProvider(transport=lambda url: (404, "text/html", ""), request_delay_seconds=0)
    with pytest.raises(RuntimeError):
        provider.get_team_season_player_stats("AFL", 2024, "Adelaide")


def test_known_team_names_covers_all_18_clubs():
    assert len(AFLTablesPlayerStatsProvider.known_team_names()) == 18


def test_parsing_is_deterministic_across_reruns():
    """A historical backfill must produce identical rows every time it
    parses the same source HTML — no reliance on dict/set iteration order
    or any other non-deterministic input."""
    first = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")
    second = _make_provider().get_team_season_player_stats("AFL", 2024, "Adelaide")

    def _key(line):
        return (line.player_source_id, line.round_label.raw)

    first_sorted = sorted(first, key=_key)
    second_sorted = sorted(second, key=_key)
    assert len(first_sorted) == len(second_sorted)
    for a, b in zip(first_sorted, second_sorted):
        assert a.player_name == b.player_name
        assert a.player_source_id == b.player_source_id
        assert a.round_label == b.round_label
        assert a.subbed_on == b.subbed_on
        assert a.subbed_off == b.subbed_off
        assert a.stats == b.stats


# Regression fixture for finals support — verified against a real fetched
# 2024 Brisbane Lions page (a Grand Finalist that season): finals columns
# are bare 2-letter codes (EF/SF/PF/GF here; QF is structurally identical,
# just a different code), in the SAME header row as the numbered
# home-and-away columns, no "R" prefix.
SAMPLE_GBG_HTML_WITH_FINALS = """
<html><body>
<table class="sortable" border="2" width="100%"><thead><tr><th colspan="29">Disposals</th></tr>
<tr><th align="left">Player</th><th width="2%">R24</th><th width="2%">EF</th><th width="2%">SF</th><th width="2%">PF</th><th width="2%">GF</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">18</td><td align="center">22</td><td align="center">19</td><td align="center">25</td><td align="center">35</td><td align="center">119</td></tr>
</tbody></table>

<table class="sortable" border="2" width="100%"><thead><tr><th colspan="29">Subs</th></tr>
<tr><th align="left">Player</th><th width="2%">R24</th><th width="2%">EF</th><th width="2%">SF</th><th width="2%">PF</th><th width="2%">GF</th><th>Tot</th></tr></thead>
<tbody>
<tr><td><a href="players/A/Amy_Alpha.html">Alpha, Amy</a></td><td align="center">-</td><td align="center">-</td><td align="center">-</td><td align="center">-</td><td align="center">-</td><td align="center">0/0</td></tr>
</tbody></table>
</body></html>
"""


def test_finals_columns_are_parsed_with_correct_round_kinds():
    provider = AFLTablesPlayerStatsProvider(
        transport=lambda url: (200, "text/html", SAMPLE_GBG_HTML_WITH_FINALS), request_delay_seconds=0
    )
    lines = provider.get_team_season_player_stats("AFL", 2024, "Adelaide")
    by_raw = {l.round_label.raw: l for l in lines}

    assert by_raw["R24"].round_label.kind is RoundKind.HOME_AND_AWAY
    assert by_raw["EF"].round_label.kind is RoundKind.FINALS_WEEK_1
    assert by_raw["SF"].round_label.kind is RoundKind.SEMI_FINALS
    assert by_raw["PF"].round_label.kind is RoundKind.PRELIMINARY_FINAL
    assert by_raw["GF"].round_label.kind is RoundKind.GRAND_FINAL


def test_finals_columns_carry_the_right_stats():
    provider = AFLTablesPlayerStatsProvider(
        transport=lambda url: (200, "text/html", SAMPLE_GBG_HTML_WITH_FINALS), request_delay_seconds=0
    )
    lines = provider.get_team_season_player_stats("AFL", 2024, "Adelaide")
    by_raw = {l.round_label.raw: l for l in lines}

    assert by_raw["GF"].stats["disposals"] == 35
    assert by_raw["EF"].stats["disposals"] == 22


def test_qf_is_parsed_as_the_same_kind_as_ef():
    html = SAMPLE_GBG_HTML_WITH_FINALS.replace(">EF<", ">QF<")
    provider = AFLTablesPlayerStatsProvider(transport=lambda url: (200, "text/html", html), request_delay_seconds=0)
    lines = provider.get_team_season_player_stats("AFL", 2024, "Adelaide")
    by_raw = {l.round_label.raw: l for l in lines}

    assert by_raw["QF"].round_label.kind is RoundKind.FINALS_WEEK_1
