"""Time windows for teammate context (current season / last 2 seasons /
current club career): filtering, club changes, insufficient-sample handling
(never a silent fallback), discovery/detail consistency, season breakdown and
point-in-time safety of the trailing baseline."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team
from app.player_modelling.context_windows import (
    DEFAULT_WINDOW,
    ClubScope,
    ContextWindow,
    select_window,
    window_phrase,
)
from app.player_modelling.player_context_analysis import (
    DEFAULT_TRAILING_FORM_WINDOW,
    MIN_TRAILING_GAMES_FOR_BASELINE,
    PlayerContextConfidenceTier,
    build_player_context_analysis,
    player_context_analysis_as_dict,
)
from app.player_modelling.teammate_discovery import build_teammate_discovery

CS, L2, CAREER = ContextWindow.CURRENT_SEASON, ContextWindow.LAST_2_SEASONS, ContextWindow.CURRENT_CLUB_CAREER


class World:
    """One sport, a set of seasons, clubs and players, with helpers to add games."""

    def __init__(self, db, suffix=""):
        self.db = db
        self.sport = Sport(code=f"AFL{suffix}", name="AFL")
        db.add(self.sport)
        db.flush()
        self.seasons: dict[int, Season] = {}
        self.round_no: dict[int, int] = {}
        self.club_a = Team(sport_id=self.sport.id, name="Collingwood", short_name="COL")
        self.club_b = Team(sport_id=self.sport.id, name="Carlton", short_name="CAR")
        self.opp = Team(sport_id=self.sport.id, name="Essendon", short_name="ESS")
        db.add_all([self.club_a, self.club_b, self.opp])
        db.flush()
        self.player = self.new_player("Nick Daicos", suffix)
        self.teammate = self.new_player("Josh Daicos", suffix)
        self.clock = datetime(2023, 3, 1, tzinfo=timezone.utc)
        db.commit()

    def new_player(self, name, suffix=""):
        p = Player(sport_id=self.sport.id, display_name=name, source="afltables", source_player_id=f"players/{name}{id(self)}{suffix}")
        self.db.add(p)
        self.db.flush()
        return p

    def season(self, year):
        if year not in self.seasons:
            s = Season(sport_id=self.sport.id, year=year)
            self.db.add(s)
            self.db.flush()
            self.seasons[year] = s
        return self.seasons[year]

    def game(self, year, *, player_disposals, teammate_in, club=None, teammate_club=None, extra=()):
        """One match: the player at `club` (default club_a); the teammate has a
        row for the same team when `teammate_in`. `extra` = [(Player, disposals)]."""
        club = club or self.club_a
        season = self.season(year)
        self.round_no[year] = self.round_no.get(year, 0) + 1
        rnd = Round(season_id=season.id, round_number=self.round_no[year])
        self.db.add(rnd)
        self.db.flush()
        # strictly increasing dates, jumping to the right calendar year
        self.clock = max(self.clock + timedelta(days=7), datetime(year, 3, 1, tzinfo=timezone.utc) + timedelta(days=7 * self.round_no[year]))
        match = Match(
            sport_id=self.sport.id, season_id=season.id, round_id=rnd.id, home_team_id=club.id, away_team_id=self.opp.id,
            scheduled_start=self.clock, status=MatchStatus.COMPLETED,
        )
        self.db.add(match)
        self.db.flush()

        def row(p, d, team):
            self.db.add(PlayerMatchStat(
                player_id=p.id, match_id=match.id, team_id=team.id, opponent_team_id=self.opp.id, source="afltables",
                recorded_at=self.clock, disposals=d, goals=1, time_on_ground_pct=85,
            ))

        row(self.player, player_disposals, club)
        if teammate_in:
            row(self.teammate, 15, teammate_club or club)
        for p, d in extra:
            row(p, d, club)
        self.db.commit()
        return match


def build_career(db, suffix=""):
    """2023: 6 games all apart (20); 2024: 6 together (25); 2025: 4 together
    (24) + 4 apart (28); 2026: 6 together (26) + 2 apart (30)."""
    w = World(db, suffix)
    for _ in range(6):
        w.game(2023, player_disposals=20, teammate_in=False)
    for _ in range(6):
        w.game(2024, player_disposals=25, teammate_in=True)
    for _ in range(4):
        w.game(2025, player_disposals=24, teammate_in=True)
    for _ in range(4):
        w.game(2025, player_disposals=28, teammate_in=False)
    for _ in range(6):
        w.game(2026, player_disposals=26, teammate_in=True)
    for _ in range(2):
        w.game(2026, player_disposals=30, teammate_in=False)
    return w


def analyse(w, window):
    return build_player_context_analysis(w.db, w.player.id, w.teammate.id, window=window)


# --- filtering ---------------------------------------------------------------

def test_default_window_is_current_season():
    assert DEFAULT_WINDOW is CS


def test_current_season_filtering(db_session):
    w = build_career(db_session)
    a = analyse(w, CS)
    assert a.window.window is CS and a.window.included_seasons == [2026] and a.window.games_considered == 8
    assert (a.with_teammate.games, a.without_teammate.games) == (6, 2)
    assert a.with_teammate.mean == 26 and a.without_teammate.mean == 30 and a.raw_difference == 4
    assert {e.season_year for e in a.evidence} == {2026} and len(a.evidence) == 8
    assert a.window.scope_label == "2026 season"
    assert a.window.earliest_date < a.window.latest_date
    assert a.window.earliest_date.year == 2026


def test_last_two_seasons_filtering(db_session):
    w = build_career(db_session)
    a = analyse(w, L2)
    assert a.window.included_seasons == [2025, 2026] and a.window.games_considered == 16
    assert (a.with_teammate.games, a.without_teammate.games) == (10, 6)
    assert {e.season_year for e in a.evidence} == {2025, 2026}
    assert a.window.scope_label == "Last 2 seasons · 2025–2026"


def test_current_club_career_filtering(db_session):
    w = build_career(db_session)
    a = analyse(w, CAREER)
    assert a.window.included_seasons == [2023, 2024, 2025, 2026] and a.window.games_considered == 28
    # the 6 games in 2023 predate the teammate's first recorded appearance: excluded, not "apart"
    assert (a.with_teammate.games, a.without_teammate.games) == (16, 6)
    assert a.teammate_tenure.games_excluded_outside_tenure == 6
    assert a.window.scope_label == "Current club career · 2023–2026"


def test_windows_are_nested_and_change_only_the_sample(db_session):
    w = build_career(db_session)
    sizes = [analyse(w, x).window.games_considered for x in (CS, L2, CAREER)]
    assert sizes == sorted(sizes) and len(set(sizes)) == 3
    assert {analyse(w, x).player_id for x in (CS, L2, CAREER)} == {w.player.id}


# --- club changes -------------------------------------------------------------

def test_player_who_changed_clubs_uses_only_the_most_recent_club_in_every_window(db_session):
    w = World(db_session, "chg")
    for _ in range(5):
        w.game(2024, player_disposals=12, teammate_in=True, club=w.club_b, teammate_club=w.club_b)   # old club
    for _ in range(4):
        w.game(2025, player_disposals=22, teammate_in=True)                                             # new club
    for _ in range(3):
        w.game(2025, player_disposals=26, teammate_in=False)
    for _ in range(3):
        w.game(2026, player_disposals=24, teammate_in=True)
    a_career, a_l2, a_cs = analyse(w, CAREER), analyse(w, L2), analyse(w, CS)
    assert a_career.team_name == "Collingwood" == a_l2.team_name == a_cs.team_name
    assert a_career.window.games_considered == 10          # the 5 Carlton games never appear
    assert a_l2.window.games_considered == 10              # 2024 excluded by window AND club
    assert a_cs.window.games_considered == 3
    assert all(e.team_name == "Collingwood" for e in a_career.evidence)


def test_mid_season_trade_current_season_counts_only_new_club_games(db_session):
    w = World(db_session, "trade")
    for _ in range(3):
        w.game(2026, player_disposals=10, teammate_in=True, club=w.club_b, teammate_club=w.club_b)  # before the trade
    for _ in range(4):
        w.game(2026, player_disposals=25, teammate_in=True)                                          # after the trade
    a = analyse(w, CS)
    assert a.team_name == "Collingwood" and a.window.games_considered == 4
    assert a.with_teammate.mean == 25


# --- teammate joined partway --------------------------------------------------

def test_teammate_who_joined_partway_is_windowed_consistently(db_session):
    """Windows cut the player's games first; tenure eligibility then removes the
    games before the teammate arrived from every statistic in every window."""
    w = World(db_session, "join")
    for _ in range(8):
        w.game(2025, player_disposals=20, teammate_in=False)
    for _ in range(3):
        w.game(2026, player_disposals=24, teammate_in=False)  # teammate not yet arrived
    for _ in range(9):
        w.game(2026, player_disposals=27, teammate_in=True)
    for _ in range(2):
        w.game(2026, player_disposals=25, teammate_in=False)  # genuine absence after arriving
    cs, l2, career = analyse(w, CS), analyse(w, L2), analyse(w, CAREER)
    for a in (cs, l2, career):
        assert (a.with_teammate.games, a.without_teammate.games) == (9, 2)
    assert (cs.teammate_tenure.games_excluded_outside_tenure, l2.teammate_tenure.games_excluded_outside_tenure) == (3, 11)
    assert [s.season_year for s in l2.season_breakdown] == [2026, 2025]
    old, new = l2.season_breakdown[1], l2.season_breakdown[0]
    assert (old.with_teammate.games, old.without_teammate.games, old.excluded_games) == (0, 0, 8)
    assert (new.with_teammate.games, new.without_teammate.games, new.excluded_games) == (9, 2, 3)


# --- missing season metadata --------------------------------------------------

def _fake_scope(years):
    rows, matches, by_match = [], {}, {}
    for i, year in enumerate(years, start=1):
        rows.append(SimpleNamespace(id=i, match_id=i, team_id=1))
        matches[i] = SimpleNamespace(scheduled_start=datetime(2026, 1, i, tzinfo=timezone.utc))
        by_match[i] = year
    known = [y for y in years if y is not None]
    return ClubScope(team_id=1, club_rows=rows, matches_by_id=matches, season_year_by_match=by_match, anchor_season_year=max(known) if known else None)


def test_games_without_season_metadata_are_excluded_from_season_windows_and_counted():
    scope = _fake_scope([None, None, 2025, 2026, 2026])
    cs, l2, career = select_window(scope, CS), select_window(scope, L2), select_window(scope, CAREER)
    assert cs.games_considered == 2 and cs.games_excluded_missing_season == 2
    assert l2.games_considered == 3 and l2.games_excluded_missing_season == 2
    assert career.games_considered == 5 and career.games_excluded_missing_season == 0  # career keeps them


def test_no_season_metadata_at_all_gives_empty_season_windows_not_a_crash():
    scope = _fake_scope([None, None])
    cs = select_window(scope, CS)
    assert cs.games_considered == 0 and cs.anchor_season_year is None and cs.rows == []
    assert select_window(scope, CAREER).games_considered == 2
    assert "this season" in window_phrase(cs)


# --- insufficient current season / no silent fallback --------------------------

def test_insufficient_current_season_is_reported_not_broadened(db_session):
    w = build_career(db_session)  # 2026: 6 together / 2 apart
    a = analyse(w, CS)
    assert a.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY
    assert a.sufficiency.sufficient is False
    assert "Only 2 games without Josh Daicos this season (2026)" in a.sufficiency.message
    assert "Current season comparison is insufficient" in a.sufficiency.message
    # the user is told which broader windows ARE usable - but nothing was applied
    assert [x.value for x in a.sufficiency.suggested_windows] == ["last_2_seasons", "current_club_career"]
    assert a.window.window is CS and a.window.games_considered == 8
    assert {e.season_year for e in a.evidence} == {2026}
    assert (a.with_teammate.games, a.without_teammate.games) == (6, 2)


def test_window_summaries_show_all_windows_so_the_user_can_choose(db_session):
    w = build_career(db_session)
    by_key = {s["key"]: s for s in player_context_analysis_as_dict(analyse(w, CS))["window_summaries"]}
    assert by_key["current_season"]["sufficient"] is False and by_key["current_season"]["games_without"] == 2
    assert by_key["last_2_seasons"]["sufficient"] is True and (by_key["last_2_seasons"]["games_with"], by_key["last_2_seasons"]["games_without"]) == (10, 6)
    assert by_key["current_club_career"]["games_with"] == 16


def test_sufficient_current_season_has_no_notice_and_confidence_tracks_sample_size(db_session):
    w = World(db_session, "suff")
    for _ in range(9):
        w.game(2026, player_disposals=25, teammate_in=True)
    for _ in range(9):
        w.game(2026, player_disposals=28, teammate_in=False)
    a = analyse(w, CS)
    assert a.sufficiency.sufficient and a.sufficiency.message is None and a.sufficiency.suggested_windows == []
    assert a.confidence.tier is PlayerContextConfidenceTier.MODERATE  # 9 in the smaller group, not "higher" because of a large gap


def test_insufficient_everywhere_says_no_broader_window_helps(db_session):
    w = World(db_session, "thin")
    for _ in range(5):
        w.game(2026, player_disposals=25, teammate_in=True)
    w.game(2026, player_disposals=28, teammate_in=False)
    a = analyse(w, CS)
    assert not a.sufficiency.sufficient and a.sufficiency.suggested_windows == []
    assert "No broader window has enough games either" in a.sufficiency.message


def test_no_games_in_current_season_window_message(db_session):
    w = World(db_session, "none")
    w.game(2025, player_disposals=25, teammate_in=True)
    a = analyse(w, CS)  # anchor is the player's own latest season (2025) - the label says so
    assert a.window.scope_label == "2025 season" and a.window.anchor_season_year == 2025


# --- season breakdown ----------------------------------------------------------

def test_season_breakdown_only_when_multiple_seasons_and_is_purely_descriptive(db_session):
    w = build_career(db_session)
    assert analyse(w, CS).season_breakdown == []
    l2 = analyse(w, L2).season_breakdown
    assert [s.season_year for s in l2] == [2026, 2025]
    s26, s25 = l2
    assert (s26.with_teammate.games, s26.with_teammate.mean, s26.without_teammate.games, s26.without_teammate.mean) == (6, 26, 2, 30)
    assert (s25.with_teammate.games, s25.with_teammate.mean, s25.without_teammate.games, s25.without_teammate.mean) == (4, 24, 4, 28)
    career = analyse(w, CAREER).season_breakdown
    assert [s.season_year for s in career] == [2026, 2025, 2024, 2023]
    d = player_context_analysis_as_dict(analyse(w, CAREER))
    assert "consistency" not in " ".join(d.keys()) and all("score" not in k for k in d["season_breakdown"][0])


# --- discovery / detail consistency -------------------------------------------

@pytest.mark.parametrize("window", [CS, L2, CAREER])
def test_discovery_matches_detail_for_the_same_window(db_session, window):
    w = build_career(db_session)
    third = w.new_player("Third Wheel")
    # a third teammate who plays only some 2026 games
    for i, m in enumerate(db_session.query(Match).filter(Match.season_id == w.season(2026).id).all()):
        if i % 2 == 0:
            db_session.add(PlayerMatchStat(player_id=third.id, match_id=m.id, team_id=w.club_a.id, opponent_team_id=w.opp.id, source="afltables",
                                           recorded_at=m.scheduled_start, disposals=10, goals=0, time_on_ground_pct=70))
    db_session.commit()
    discovery = build_teammate_discovery(db_session, w.player.id, window=window)
    assert discovery.window.window is window
    assert discovery.candidates
    for cand in discovery.candidates:
        detail = build_player_context_analysis(db_session, w.player.id, cand.teammate_id, window=window)
        assert detail.window.scope_label == discovery.window.scope_label
        assert (detail.with_teammate, detail.without_teammate, detail.raw_difference) == (cand.with_teammate, cand.without_teammate, cand.raw_difference)
        assert detail.adjusted_effect == cand.adjusted_effect
        assert detail.confidence == cand.confidence


def test_discovery_candidate_pool_and_options_respect_the_window(db_session):
    w = build_career(db_session)
    old_mate = w.new_player("Old Mate")
    for m in db_session.query(Match).filter(Match.season_id == w.season(2023).id).all():
        db_session.add(PlayerMatchStat(player_id=old_mate.id, match_id=m.id, team_id=w.club_a.id, opponent_team_id=w.opp.id, source="afltables",
                                       recorded_at=m.scheduled_start, disposals=10, goals=0, time_on_ground_pct=70))
    db_session.commit()
    cs_ids = {c.teammate_id for c in build_teammate_discovery(db_session, w.player.id, window=CS).candidates}
    career_ids = {c.teammate_id for c in build_teammate_discovery(db_session, w.player.id, window=CAREER).candidates}
    assert old_mate.id not in cs_ids and old_mate.id in career_ids
    opts = {o["key"]: o for o in build_teammate_discovery(db_session, w.player.id, window=CS).window_options}
    assert set(opts) == {"current_season", "last_2_seasons", "current_club_career"}
    assert opts["current_season"]["n_sufficient_candidates"] == 0 and opts["last_2_seasons"]["n_sufficient_candidates"] >= 1


def test_discovery_still_ranks_by_evidence_never_by_effect_size(db_session):
    w = build_career(db_session)
    big_gap = w.new_player("Huge Gap")  # 2 shared games, enormous apparent effect
    ms = db_session.query(Match).filter(Match.season_id == w.season(2026).id).all()[:2]
    for m in ms:
        db_session.add(PlayerMatchStat(player_id=big_gap.id, match_id=m.id, team_id=w.club_a.id, opponent_team_id=w.opp.id, source="afltables",
                                       recorded_at=m.scheduled_start, disposals=10, goals=0, time_on_ground_pct=70))
    db_session.commit()
    order = [c.teammate_name for c in build_teammate_discovery(db_session, w.player.id, window=L2).candidates]
    assert order.index("Josh Daicos") < order.index("Huge Gap")


# --- point-in-time safety -----------------------------------------------------

def test_baselines_use_pre_window_history_and_no_future_information(db_session):
    """A current-season game's baseline is the mean of the player's previous
    trailing-window games - INCLUDING earlier seasons (strictly earlier games
    only) - so narrowing the window neither loses history nor leaks."""
    w = build_career(db_session)
    a = analyse(w, CS)
    assert a.adjusted_effect.games_with_baseline_teammate_in + a.adjusted_effect.games_with_baseline_teammate_out == 8  # every 2026 game has a baseline
    scope_rows = (
        db_session.query(PlayerMatchStat).join(Match, PlayerMatchStat.match_id == Match.id)
        .filter(PlayerMatchStat.player_id == w.player.id).order_by(Match.scheduled_start).all()
    )
    values = [r.disposals for r in scope_rows]
    tm = {r.match_id for r in db_session.query(PlayerMatchStat).filter(PlayerMatchStat.player_id == w.teammate.id)}
    ins, outs = [], []
    for i, r in enumerate(scope_rows):
        if i < len(values) - 8:
            continue  # only 2026 games
        prior = values[:i]
        assert len(prior) >= MIN_TRAILING_GAMES_FOR_BASELINE
        base = sum(prior[-DEFAULT_TRAILING_FORM_WINDOW:]) / len(prior[-DEFAULT_TRAILING_FORM_WINDOW:])
        (ins if r.match_id in tm else outs).append(r.disposals - base)
    # too few "out" games in the current season for an adjusted estimate: it is withheld, not invented
    assert (len(ins), len(outs)) == (6, 2)
    assert a.adjusted_effect.available is False


def test_changing_a_later_game_never_changes_an_earlier_windows_result(db_session):
    """Adjusted effect for the last-2-seasons window must not depend on games
    after those seasons' games it uses: bumping the newest game's stat only moves
    that game's own residual, never an earlier game's baseline."""
    w = build_career(db_session)
    before = analyse(w, L2).adjusted_effect
    newest = (
        db_session.query(PlayerMatchStat).join(Match, PlayerMatchStat.match_id == Match.id)
        .filter(PlayerMatchStat.player_id == w.player.id).order_by(Match.scheduled_start.desc()).first()
    )
    newest.disposals = newest.disposals + 20
    db_session.commit()
    after = analyse(w, L2).adjusted_effect
    # exactly one residual (the newest game, an "out" game) moved; the number of games and the "in" side are untouched
    assert (after.games_with_baseline_teammate_in, after.games_with_baseline_teammate_out) == (before.games_with_baseline_teammate_in, before.games_with_baseline_teammate_out)
    assert after.value != before.value
    n_out = before.games_with_baseline_teammate_out
    assert after.value - before.value == pytest.approx(20 / n_out)


# --- API ----------------------------------------------------------------------

def test_api_defaults_to_current_season_and_returns_window_metadata(client, db_session):
    w = build_career(db_session)
    body = client.get(f"/api/afl/players/{w.player.id}/context/{w.teammate.id}").json()
    assert body["window"]["key"] == "current_season" and body["window"]["included_seasons"] == [2026]
    assert body["window"]["games_considered"] == 8 and body["window"]["earliest_date"] and body["window"]["latest_date"]
    assert body["sufficiency"]["sufficient"] is False and body["sufficiency"]["suggested_windows"] == ["last_2_seasons", "current_club_career"]
    career = client.get(f"/api/afl/players/{w.player.id}/context/{w.teammate.id}?window=current_club_career").json()
    assert career["window"]["label"] == "Current club career" and career["window"]["games_considered"] == 28
    assert len(career["season_breakdown"]) == 4


def test_api_rejects_unknown_window_values(client, db_session):
    w = build_career(db_session)
    assert client.get(f"/api/afl/players/{w.player.id}/context/{w.teammate.id}?window=whenever").status_code == 422
    assert client.get(f"/api/afl/players/{w.player.id}/context-candidates?window=whenever").status_code == 422


def test_api_discovery_takes_the_same_window_parameter(client, db_session):
    w = build_career(db_session)
    body = client.get(f"/api/afl/players/{w.player.id}/context-candidates?window=last_2_seasons").json()
    assert body["window"]["key"] == "last_2_seasons"
    cand = next(c for c in body["candidates"] if c["teammate_id"] == w.teammate.id)
    assert (cand["with_teammate"]["games"], cand["without_teammate"]["games"]) == (10, 6)


# --- comparison eligibility (tenure) ---------------------------------------------
#
# Classification per game, using only recorded match history:
#   with_teammate     - same-club row in the match
#   eligible_without  - no row, and the teammate's most recent recorded appearance
#                       before the game was for THIS club (could be injury/rest/omission)
#   excluded_outside_tenure - no row, and the teammate had NO earlier appearance, or their
#                       most recent earlier appearance was for ANOTHER club

def status_by_season(w, window=CAREER):
    a = analyse(w, window)
    out: dict = {}
    for e in a.evidence:
        out.setdefault(e.season_year, []).append(e.comparison_status)
    return a, out


def test_teammate_who_joined_after_the_player_had_several_seasons_there(db_session):
    w = World(db_session, "late")
    for year in (2023, 2024, 2025):
        for _ in range(8):
            w.game(year, player_disposals=20, teammate_in=False)          # 24 games before the teammate ever played
    for _ in range(10):
        w.game(2026, player_disposals=25, teammate_in=True)
    for _ in range(4):
        w.game(2026, player_disposals=27, teammate_in=False)              # genuine "apart" after joining
    a = analyse(w, CAREER)
    assert a.window.games_considered == 38
    assert (a.with_teammate.games, a.without_teammate.games) == (10, 4)   # was 10 together / 28 apart
    t = a.teammate_tenure
    assert (t.total_games_in_window, t.comparison_eligible_games, t.games_with_teammate, t.eligible_games_without_teammate, t.games_excluded_outside_tenure) == (38, 14, 10, 4, 24)
    assert "38 games fall inside" in t.note and "24 occurred before Josh Daicos was recorded at Collingwood" in t.note
    assert a.without_teammate.mean == 27                                  # the 24 pre-arrival games (20 each) are nowhere in it
    assert a.raw_difference == 2


def test_no_note_when_nothing_is_excluded(db_session):
    w = World(db_session, "clean")
    for _ in range(9):
        w.game(2026, player_disposals=25, teammate_in=True)
    for _ in range(4):
        w.game(2026, player_disposals=27, teammate_in=False)
    t = analyse(w, CS).teammate_tenure
    assert t.games_excluded_outside_tenure == 0 and t.note is None


def test_teammate_who_moves_from_another_club_mid_season(db_session):
    w = World(db_session, "mid")
    for _ in range(3):
        w.game(2026, player_disposals=20, teammate_in=True, teammate_club=w.club_b)   # teammate still playing for Carlton
    w.game(2026, player_disposals=20, teammate_in=False)                                # still Carlton's - excluded
    for _ in range(9):
        w.game(2026, player_disposals=25, teammate_in=True)                             # joined
    for _ in range(3):
        w.game(2026, player_disposals=28, teammate_in=False)                            # genuine absences after joining
    a, seasons = status_by_season(w, CS)
    assert (a.with_teammate.games, a.without_teammate.games) == (9, 3)
    assert a.teammate_tenure.games_excluded_outside_tenure == 4                         # 3 games with a Carlton row + the 1 after
    assert a.without_teammate.mean == 28


def test_teammate_who_leaves_for_another_club(db_session):
    w = World(db_session, "leave")
    for _ in range(10):
        w.game(2024, player_disposals=25, teammate_in=True)
    for _ in range(5):
        w.game(2025, player_disposals=18, teammate_in=True, teammate_club=w.club_b)    # now at Carlton
    for _ in range(6):
        w.game(2025, player_disposals=18, teammate_in=False)                            # after that: not a teammate any more
    a = analyse(w, CAREER)
    assert a.with_teammate.games == 10 and a.without_teammate.games == 0
    assert a.teammate_tenure.games_excluded_outside_tenure == 5 + 6                     # the 5 games he played for Carlton have no club row here either


def test_teammate_who_leaves_and_later_returns_to_the_original_club(db_session):
    """Teammate history: A A  B B  A.  Games in the Club B interval are not
    "without" games; comparison eligibility resumes after the return."""
    w = World(db_session, "return")
    for _ in range(2):
        w.game(2024, player_disposals=25, teammate_in=True)                            # A, A
    w.game(2024, player_disposals=24, teammate_in=False)                                # after last A game: no evidence he left -> eligible
    w.game(2025, player_disposals=18, teammate_in=True, teammate_club=w.club_b)        # B
    w.game(2025, player_disposals=18, teammate_in=False)                                # while at B -> excluded
    w.game(2025, player_disposals=18, teammate_in=True, teammate_club=w.club_b)        # B
    w.game(2025, player_disposals=18, teammate_in=False)                                # still B -> excluded
    w.game(2026, player_disposals=27, teammate_in=True)                                 # back at A
    w.game(2026, player_disposals=29, teammate_in=False)                                # eligible again (injury/rest)
    a = analyse(w, CAREER)
    by_game = {(e.season_year, e.round_number): e.comparison_status for e in a.evidence}
    assert by_game[(2024, 3)] == "eligible_without"
    assert by_game[(2025, 1)] == by_game[(2025, 2)] == by_game[(2025, 3)] == by_game[(2025, 4)] == "excluded_outside_tenure"
    assert by_game[(2026, 1)] == "with_teammate" and by_game[(2026, 2)] == "eligible_without"
    assert (a.with_teammate.games, a.without_teammate.games, a.teammate_tenure.games_excluded_outside_tenure) == (3, 2, 4)
    assert a.without_teammate.mean == (24 + 29) / 2


def test_genuine_rest_or_omission_after_a_same_club_appearance_still_counts_as_without(db_session):
    w = World(db_session, "rest")
    for _ in range(6):
        w.game(2025, player_disposals=25, teammate_in=True)
    for _ in range(5):
        w.game(2025, player_disposals=28, teammate_in=False)   # weeks after his last game FOR THIS CLUB, no evidence he left
    a = analyse(w, CS)
    assert a.without_teammate.games == 5 and a.teammate_tenure.games_excluded_outside_tenure == 0


def test_games_before_the_teammates_first_appearance_are_excluded_even_when_he_never_played_elsewhere(db_session):
    w = World(db_session, "first")
    for _ in range(7):
        w.game(2025, player_disposals=20, teammate_in=False)
    for _ in range(6):
        w.game(2025, player_disposals=25, teammate_in=True)
    a, seasons = status_by_season(w, CS)
    assert seasons[2025].count("excluded_outside_tenure") == 7
    assert a.without_teammate.games == 0 and a.teammate_tenure.eligible_games_without_teammate == 0


def test_a_player_with_no_recorded_history_has_no_eligible_without_games(db_session):
    w = World(db_session, "never")
    for _ in range(4):
        w.game(2026, player_disposals=20, teammate_in=False)
    a = analyse(w, CS)
    assert a.with_teammate.games == 0 and a.without_teammate.games == 0 and a.teammate_tenure.games_excluded_outside_tenure == 4


def test_excluded_games_are_in_no_statistic_and_never_look_like_teammate_out_evidence(db_session):
    w = World(db_session, "audit")
    for _ in range(10):
        w.game(2025, player_disposals=10, teammate_in=False)   # excluded (huge difference if they leaked in)
    for _ in range(8):
        w.game(2026, player_disposals=30, teammate_in=True)
    for _ in range(8):
        w.game(2026, player_disposals=30, teammate_in=False)
    a = analyse(w, CAREER)
    assert a.without_teammate.mean == 30 and a.raw_difference == 0
    assert a.without_teammate.median == 30 and a.without_teammate.milestone_rates[25] == 1
    excluded = [e for e in a.evidence if e.comparison_status == "excluded_outside_tenure"]
    assert len(excluded) == 10 and all(e.teammate_played is False for e in excluded)   # visible as audit rows, tagged distinctly
    sb = {s.season_year: s for s in a.season_breakdown}
    assert sb[2025].excluded_games == 10 and sb[2025].without_teammate.games == 0 and sb[2025].without_teammate.mean is None
    assert sb[2026].without_teammate.games == 8 and sb[2026].excluded_games == 0


def test_confidence_and_sufficiency_use_only_eligible_comparison_samples(db_session):
    w = World(db_session, "conf")
    for _ in range(40):
        w.game(2024, player_disposals=20, teammate_in=False)   # 40 pre-arrival "apart" games: previously made the smaller group huge
    for _ in range(12):
        w.game(2025, player_disposals=25, teammate_in=True)
    for _ in range(2):
        w.game(2025, player_disposals=27, teammate_in=False)   # only 2 eligible apart
    a = analyse(w, CAREER)
    assert (a.with_teammate.games, a.without_teammate.games) == (12, 2)
    assert a.confidence.tier is PlayerContextConfidenceTier.INSUFFICIENT_HISTORY
    assert not a.sufficiency.sufficient and "Only 2 games without Josh Daicos" in a.sufficiency.message
    assert a.adjusted_effect.available is False
    summaries = {s.window.value: s for s in a.window_summaries}
    assert (summaries["current_club_career"].games_with, summaries["current_club_career"].games_without, summaries["current_club_career"].games_excluded) == (12, 2, 40)
    assert summaries["current_club_career"].sufficient is False


def test_window_summaries_and_suggestions_use_eligible_samples(db_session):
    w = build_career(db_session, "wsum")            # 2023: 6 pre-arrival games
    by_key = {s.window.value: s for s in analyse(w, CS).window_summaries}
    assert (by_key["current_club_career"].games_with, by_key["current_club_career"].games_without, by_key["current_club_career"].games_excluded) == (16, 6, 6)
    assert (by_key["last_2_seasons"].games_with, by_key["last_2_seasons"].games_without, by_key["last_2_seasons"].games_excluded) == (10, 6, 0)


def test_baselines_use_excluded_games_as_prior_form_and_stay_leakage_safe(db_session):
    """Excluded games are still the player's own genuine prior form, so they feed
    the trailing baseline; only comparison-eligible games enter the residual
    comparison; and no baseline ever uses a later game."""
    w = World(db_session, "base")
    for _ in range(10):
        w.game(2025, player_disposals=20, teammate_in=False)       # excluded from the split, valid history
    pattern = [(26, True), (24, False), (27, True), (23, False)] * 3
    for d, tin in pattern:
        w.game(2026, player_disposals=d, teammate_in=tin)          # first game has the teammate -> 2026 all comparable
    a = analyse(w, CS)
    assert a.teammate_tenure.games_excluded_outside_tenure == 0    # (the 2025 games are outside the CS window)
    assert a.adjusted_effect.games_with_baseline_teammate_in + a.adjusted_effect.games_with_baseline_teammate_out == 12   # baselines exist from game 1: 2025 history used

    career = analyse(w, CAREER)
    assert career.teammate_tenure.games_excluded_outside_tenure == 10
    # career adjusted effect counts only the 12 eligible games, yet their baselines come from the excluded 2025 games too
    assert career.adjusted_effect.games_with_baseline_teammate_in + career.adjusted_effect.games_with_baseline_teammate_out == 12
    rows = (
        db_session.query(PlayerMatchStat).join(Match, PlayerMatchStat.match_id == Match.id)
        .filter(PlayerMatchStat.player_id == w.player.id).order_by(Match.scheduled_start).all()
    )
    values = [r.disposals for r in rows]
    tm = {r.match_id for r in db_session.query(PlayerMatchStat).filter(PlayerMatchStat.player_id == w.teammate.id)}
    ins, outs = [], []
    for i, r in enumerate(rows):
        if i < 10:
            continue
        prior = values[:i]
        base = sum(prior[-DEFAULT_TRAILING_FORM_WINDOW:]) / len(prior[-DEFAULT_TRAILING_FORM_WINDOW:])
        (ins if r.match_id in tm else outs).append(r.disposals - base)
    expected = sum(outs) / len(outs) - sum(ins) / len(ins)
    assert career.adjusted_effect.available and career.adjusted_effect.value == pytest.approx(expected)

    # Leakage: raising the NEWEST game only moves that game's own residual (an "out" game) - no earlier baseline changes.
    before = career.adjusted_effect.value
    newest = rows[-1]
    newest.disposals += 20
    db_session.commit()
    after = analyse(w, CAREER).adjusted_effect.value
    assert after - before == pytest.approx(20 / len(outs))
    # Excluded history DOES feed baselines: changing an excluded 2025 game moves the residuals of later games.
    rows[9].disposals += 30
    db_session.commit()
    assert analyse(w, CAREER).adjusted_effect.value != after


def test_discovery_and_detail_use_identical_eligibility(db_session):
    w = build_career(db_session, "ident")
    late = w.new_player("Late Arrival")
    for i, m in enumerate(db_session.query(Match).filter(Match.season_id == w.season(2026).id).all()):
        db_session.add(PlayerMatchStat(player_id=late.id, match_id=m.id, team_id=w.club_a.id, opponent_team_id=w.opp.id, source="afltables",
                                       recorded_at=m.scheduled_start, disposals=10, goals=0, time_on_ground_pct=70))
    db_session.commit()
    for window in (CS, L2, CAREER):
        discovery = build_teammate_discovery(db_session, w.player.id, window=window)
        by_id = {c.teammate_id: c for c in discovery.candidates}
        assert late.id in by_id
        for tid, cand in by_id.items():
            detail = build_player_context_analysis(db_session, w.player.id, tid, window=window)
            assert cand.games_excluded_outside_tenure == detail.teammate_tenure.games_excluded_outside_tenure
            assert (cand.with_teammate.games, cand.without_teammate.games) == (detail.with_teammate.games, detail.without_teammate.games)
            assert cand.without_teammate.games == detail.teammate_tenure.eligible_games_without_teammate
    career_late = by_id[late.id]
    assert career_late.without_teammate.games == 0 and career_late.games_excluded_outside_tenure == 20   # 2023-2025 = 20 games before he arrived


def test_a_newer_teammate_does_not_get_a_huge_without_sample_from_the_players_long_career(db_session):
    w = World(db_session, "newer")
    for year in (2022, 2023, 2024, 2025):
        for _ in range(20):
            w.game(year, player_disposals=22, teammate_in=False)
    for _ in range(12):
        w.game(2026, player_disposals=25, teammate_in=True)
    for _ in range(5):
        w.game(2026, player_disposals=26, teammate_in=False)
    disc = build_teammate_discovery(db_session, w.player.id, window=CAREER)
    cand = next(c for c in disc.candidates if c.teammate_id == w.teammate.id)
    assert (cand.with_teammate.games, cand.without_teammate.games, cand.games_excluded_outside_tenure) == (12, 5, 80)
    opts = {o["key"]: o for o in disc.window_options}
    assert opts["current_club_career"]["n_sufficient_candidates"] == 1     # 12 / 5 clears the minimum; 12 / 85 would too - but on honest counts


def test_discovery_window_options_count_only_eligible_apart_games(db_session):
    w = World(db_session, "opts")
    for _ in range(30):
        w.game(2024, player_disposals=20, teammate_in=False)   # pre-arrival
    for _ in range(9):
        w.game(2025, player_disposals=25, teammate_in=True)
    w.game(2025, player_disposals=27, teammate_in=False)       # only 1 eligible apart
    opts = {o["key"]: o for o in build_teammate_discovery(db_session, w.player.id, window=CAREER).window_options}
    assert opts["current_club_career"]["n_sufficient_candidates"] == 0   # 9 / 1 is insufficient; would have been 9 / 31 before


def test_api_exposes_comparison_metadata_and_evidence_status(client, db_session):
    w = build_career(db_session, "api")
    body = client.get(f"/api/afl/players/{w.player.id}/context/{w.teammate.id}?window=current_club_career").json()
    t = body["teammate_tenure"]
    assert (t["total_games_in_window"], t["comparison_eligible_games"], t["games_with_teammate"], t["eligible_games_without_teammate"], t["games_excluded_outside_tenure"]) == (28, 22, 16, 6, 6)
    assert t["note"] and "6 occurred before Josh Daicos was recorded at Collingwood" in t["note"]
    assert {e["comparison_status"] for e in body["evidence"]} == {"with_teammate", "eligible_without", "excluded_outside_tenure"}
    assert body["season_breakdown"][-1]["excluded_games"] == 6            # 2023
    cs = client.get(f"/api/afl/players/{w.player.id}/context/{w.teammate.id}").json()
    assert cs["teammate_tenure"]["games_excluded_outside_tenure"] == 0 and cs["teammate_tenure"]["note"] is None
    disc = client.get(f"/api/afl/players/{w.player.id}/context-candidates?window=current_club_career").json()
    cand = next(c for c in disc["candidates"] if c["teammate_id"] == w.teammate.id)
    assert cand["games_excluded_outside_tenure"] == 6 and cand["without_teammate"]["games"] == 6
