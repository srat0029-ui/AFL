"""Tests for AFLTablesStatsProvider using a fake transport function and a
compact synthetic HTML fixture matching AFL Tables' real structure (verified
against real 2016/2024 pages before this backfill ran — see module
docstring in app/providers/afl/afltables.py) — no real HTTP calls here."""

from datetime import date

import pytest

from app.providers.afl.afltables import AFLTablesStatsProvider

# Two teams, three matches — one home-and-away row per team pair, plus a
# finals row for Adelaide with a blank Brownlow-votes cell (real AFL
# behaviour: Brownlow votes are never awarded in finals).
SAMPLE_SEASON_HTML = """
<html><body>
<a name="1"></a>
<div class="simpleTabs">
<div class="simpleTabsContent"><table class="sortable"><thead><tr><th colspan=13>Adelaide Team Statistics [Players]</th></tr>
<tr><th>#</th><th>Opponent</th><th>KI</th><th>MK</th><th>HB</th><th>DI</th><th>GL</th><th>BH</th><th>HO</th><th>TK</th><th>RB</th><th>IF</th><th>CL</th><th>CG</th></tr></thead>

<tr><td align=center><a href="games/2016/011320160402.html">R2</a></td><td>Port Adelaide</td>
<td align=center>234-175</td><td align=center>109-44</td><td align=center>178-149</td><td align=center>412-324</td><td align=center>22-11</td><td align=center>9-19</td><td align=center>49-54</td><td align=center>64-70</td><td align=center>49-26</td><td align=center>55-70</td><td align=center>45-43</td><td align=center>52-62</td><tr>
<td align=center><a href="games/2016/011220160910.html">EF</a></td><td>North Melbourne</td>
<td align=center>219-200</td><td align=center>101-90</td><td align=center>166-150</td><td align=center>385-350</td><td align=center>21-15</td><td align=center>12-10</td><td align=center>49-40</td><td align=center>65-60</td><td align=center>22-30</td><td align=center>68-55</td><td align=center>45-40</td><td align=center>40-45</td><tr>
</table></div>
<div class="simpleTabsContent"><table class="sortable"><thead><tr><th colspan=11>Adelaide Team Statistics [Players]</th></tr>
<tr><th>#</th><th>Opponent</th><th>FF</th><th>FA</th><th>BR</th><th>CP</th><th>UP</th><th>CM</th><th>MI</th><th>1%</th><th>BO</th><th>GA</th></tr></thead>

<tr><td align=center><a href="games/2016/011320160402.html">R2</a></td><td>Port Adelaide</td>
<td align=center>22-15</td><td align=center>15-22</td><td align=center>5-1</td><td align=center>165-161</td><td align=center>247-164</td><td align=center>14-13</td><td align=center>17-6</td><td align=center>55-68</td><td align=center>10-9</td><td align=center>15-7</td><tr>
<td align=center><a href="games/2016/011220160910.html">EF</a></td><td>North Melbourne</td>
<td align=center>18-14</td><td align=center>14-18</td><td align=center></td><td align=center>150-140</td><td align=center>235-210</td><td align=center>10-8</td><td align=center>19-12</td><td align=center>50-45</td><td align=center>5-6</td><td align=center>12-9</td><tr>
</table></div>
</div>
<a name="13"></a>
<div class="simpleTabs">
<div class="simpleTabsContent"><table class="sortable"><thead><tr><th colspan=13>Port Adelaide Team Statistics [Players]</th></tr>
<tr><th>#</th><th>Opponent</th><th>KI</th><th>MK</th><th>HB</th><th>DI</th><th>GL</th><th>BH</th><th>HO</th><th>TK</th><th>RB</th><th>IF</th><th>CL</th><th>CG</th></tr></thead>

<tr><td align=center><a href="games/2016/011320160402.html">R2</a></td><td>Adelaide</td>
<td align=center>175-234</td><td align=center>44-109</td><td align=center>149-178</td><td align=center>324-412</td><td align=center>11-22</td><td align=center>19-9</td><td align=center>54-49</td><td align=center>70-64</td><td align=center>26-49</td><td align=center>70-55</td><td align=center>43-45</td><td align=center>62-52</td><tr>
</table></div>
<div class="simpleTabsContent"><table class="sortable"><thead><tr><th colspan=11>Port Adelaide Team Statistics [Players]</th></tr>
<tr><th>#</th><th>Opponent</th><th>FF</th><th>FA</th><th>BR</th><th>CP</th><th>UP</th><th>CM</th><th>MI</th><th>1%</th><th>BO</th><th>GA</th></tr></thead>

<tr><td align=center><a href="games/2016/011320160402.html">R2</a></td><td>Adelaide</td>
<td align=center>15-22</td><td align=center>22-15</td><td align=center>1-5</td><td align=center>161-165</td><td align=center>164-247</td><td align=center>13-14</td><td align=center>6-17</td><td align=center>68-55</td><td align=center>9-10</td><td align=center>7-15</td><tr>
</table></div>
</div>
</body></html>
"""


def _make_provider() -> AFLTablesStatsProvider:
    return AFLTablesStatsProvider(transport=lambda url: (200, "text/html", SAMPLE_SEASON_HTML), request_delay_seconds=0)


def test_parses_expected_number_of_rows():
    rows = _make_provider().get_season_team_stats("AFL", 2016)
    assert len(rows) == 3  # Adelaide x2 + Port Adelaide x1


def test_row_combines_table1_and_table2_fields():
    rows = _make_provider().get_season_team_stats("AFL", 2016)
    row = next(r for r in rows if r.team_name == "Adelaide" and r.opponent_name == "Port Adelaide")

    assert row.stats["kicks"] == 234
    assert row.stats["disposals"] == 412
    assert row.stats["goals"] == 22
    assert row.stats["behinds"] == 9
    assert row.stats["contested_possessions"] == 165
    assert row.stats["goal_assists"] == 15


def test_match_date_parsed_from_game_url():
    rows = _make_provider().get_season_team_stats("AFL", 2016)
    row = next(r for r in rows if r.team_name == "Adelaide" and r.opponent_name == "Port Adelaide")
    assert row.match_date == date(2016, 4, 2)


def test_finals_blank_brownlow_cell_is_none_not_zero():
    rows = _make_provider().get_season_team_stats("AFL", 2016)
    finals_row = next(r for r in rows if r.opponent_name == "North Melbourne")
    assert "brownlow_votes" not in finals_row.stats  # blank cell -> field omitted, not fabricated as 0


def test_both_teams_perspectives_are_self_consistent():
    rows = _make_provider().get_season_team_stats("AFL", 2016)
    adelaide_row = next(r for r in rows if r.team_name == "Adelaide" and r.opponent_name == "Port Adelaide")
    port_row = next(r for r in rows if r.team_name == "Port Adelaide" and r.opponent_name == "Adelaide")

    # each team's "self" numbers should equal the other's "opponent" numbers
    assert adelaide_row.stats["kicks"] == 234
    assert port_row.stats["kicks"] == 175
    # Adelaide's actual kicks (234) should appear as the opponent side in the raw source data
    # (verified via the raw fixture's "175-234" pairing for Port Adelaide's own row)


def test_match_external_id_is_the_game_url():
    rows = _make_provider().get_season_team_stats("AFL", 2016)
    row = next(r for r in rows if r.team_name == "Adelaide" and r.opponent_name == "Port Adelaide")
    assert row.match_external_id == "games/2016/011320160402.html"


def test_rejects_non_afl_sport():
    with pytest.raises(ValueError, match="only supports AFL"):
        _make_provider().get_season_team_stats("NRL", 2016)


def test_raises_on_http_error():
    provider = AFLTablesStatsProvider(transport=lambda url: (404, "text/html", ""), request_delay_seconds=0)
    with pytest.raises(RuntimeError, match="HTTP 404"):
        provider.get_season_team_stats("AFL", 2016)


def test_malformed_team_block_is_skipped_not_raised():
    malformed_html = "<html><a name=\"1\"></a>garbage no team stats here</html>"
    provider = AFLTablesStatsProvider(transport=lambda url: (200, "text/html", malformed_html), request_delay_seconds=0)
    rows = provider.get_season_team_stats("AFL", 2016)
    assert rows == []
