"""NBA live evidence: provider parsing (trimmed copies of REAL ESPN
responses captured on 2026-10-02), append-only recording, as-of queries and
the live cycle. All HTTP is mocked - never a real network call."""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.core.prospective import FrozenRecordError
from app.models.nba import (
    NbaEvidencePoll,
    NbaGame,
    NbaGameLineupObservation,
    NbaGameScheduleObservation,
    NbaPlayer,
    NbaPlayerAvailabilityReport,
    NbaTeam,
    NbaTeamObservation,
)
from app.nba.asof import (
    availability_known_at,
    game_lineup_known_at,
    game_schedule_known_at,
    team_availability_known_at,
    team_observation_known_at,
)
from app.nba.evidence import EvidenceRejected, canonical_status, record_availability, record_game_lineup, record_team_depth_chart, record_team_roster
from app.nba.ingestion import ingest_games, ingest_teams
from app.providers.nba.espn import EspnNbaError
from app.providers.nba.espn_evidence import EspnNbaEvidenceProvider
from app.providers.nba.evidence_types import NbaDepthChart, NbaGameLineup, NbaInjuryFeed, NbaInjuryItem, NbaLineupEntry, NbaRosterPlayer, NbaTeamRoster
from app.providers.nba.types import NbaGameRecord, NbaTeamRecord

FIXTURES = Path(__file__).parent / "fixtures" / "nba"
SRC = "espn"
T0 = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)  # 3:00 PM
TIPOFF = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- provider parsing (real fixtures) --------------------------------------


def _provider(site: dict | None = None, core: dict | None = None) -> EspnNbaEvidenceProvider:
    site_routes = {
        "/injuries": "espn_injuries_20261002.json",
        "/teams/28/roster": "espn_team_roster_28.json",
    }
    core_routes = {
        "/seasons/2027/teams/28/depthcharts": "espn_core_depthchart_28.json",
        "/events/401902644/competitions/401902644": "espn_core_competition_pregame_401902644.json",
        "/events/401902644/competitions/401902644/competitors/28/roster": "espn_core_game_roster_pregame_401902644_28.json",
        "/events/401810433/competitions/401810433": "espn_core_competition_final_401810433.json",
        "/events/401810433/competitions/401810433/competitors/29/roster": "espn_core_game_roster_final_401810433_29.json",
    }

    def handler(routes: dict, overrides: dict | None, marker: str):
        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path.split(marker, 1)[1]
            if overrides and path in overrides:
                override = overrides[path]
                return httpx.Response(override) if isinstance(override, int) else httpx.Response(200, json=override)
            return httpx.Response(200, json=_fixture(routes[path])) if path in routes else httpx.Response(404)

        return handle

    site_client = httpx.Client(base_url="https://site.api.espn.com/apis/site/v2/sports/basketball/nba", transport=httpx.MockTransport(handler(site_routes, site, "/basketball/nba")))
    core_client = httpx.Client(base_url="https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba", transport=httpx.MockTransport(handler(core_routes, core, "/leagues/nba")))
    return EspnNbaEvidenceProvider(site_client, core_client, request_interval_seconds=0)


def test_injury_feed_is_parsed_with_raw_statuses_and_details():
    feed = _provider().get_injuries()
    assert (feed.source, len(feed.items), feed.teams_listed, feed.unresolvable_items) == ("espn", 17, 5, 0)
    assert {item.source_status for item in feed.items} == {"Out", "Day-To-Day"}
    veesaar = next(i for i in feed.items if i.player_name == "Henri Veesaar")
    assert veesaar.source_player_id == "5105571"  # taken from the athlete's link: the feed has no id field
    assert (veesaar.source_status, veesaar.source_status_type, veesaar.source_report_id) == ("Out", "INJURY_STATUS_OUT", "-57577")
    assert (veesaar.injury_type, veesaar.injury_location, veesaar.injury_side, veesaar.fantasy_status) == ("Knee", "Leg", "Right", "OFS")
    assert veesaar.expected_return_date == date(2027, 7, 1)
    assert veesaar.source_published_at == datetime(2026, 9, 21, 19, 50, tzinfo=timezone.utc)
    assert veesaar.raw["athlete"]["id_from_link"] == "5105571" and "links" not in veesaar.raw["athlete"]


def test_injury_feed_has_its_own_timestamp_distinct_from_each_entrys_date():
    feed = _provider().get_injuries()
    assert feed.source_timestamp == datetime(2026, 10, 2, 13, 23, 38, tzinfo=timezone.utc)
    assert all(item.source_published_at < feed.source_timestamp for item in feed.items)
    assert feed.fetched_at.tzinfo is not None


def test_injury_feed_keeps_both_team_ids_when_they_disagree():
    feed = _provider().get_injuries()
    watson = next(i for i in feed.items if i.player_name == "Peyton Watson")
    assert (watson.source_team_id, watson.source_athlete_team_id) == ("5", "7")
    assert sum(1 for i in feed.items if i.source_team_id != i.source_athlete_team_id) == 3


def test_an_injury_entry_with_no_player_id_is_unresolvable_not_matched_by_name():
    body = _fixture("espn_injuries_20261002.json")
    body["injuries"][0]["injuries"][0]["athlete"]["links"] = []
    feed = _provider(site={"/injuries": body}).get_injuries()
    assert feed.unresolvable_items == 1 and len(feed.items) == 16
    assert "Henri Veesaar" not in {i.player_name for i in feed.items}


def test_injury_feed_that_does_not_report_success_is_an_error():
    with pytest.raises(EspnNbaError, match="did not report success"):
        _provider(site={"/injuries": {"status": "error", "injuries": []}}).get_injuries()
    with pytest.raises(EspnNbaError, match="HTTP 503"):
        _provider(site={"/injuries": 503}).get_injuries()


def test_the_payload_hash_ignores_the_feeds_own_timestamp():
    body = _fixture("espn_injuries_20261002.json")
    later = {**body, "timestamp": "2026-10-02T14:00:00Z"}
    assert _provider().get_injuries().payload_sha256 == _provider(site={"/injuries": later}).get_injuries().payload_sha256


def test_team_roster_is_parsed():
    roster = _provider().get_team_roster("28")
    assert len(roster.players) == 20 and roster.source_timestamp == datetime(2026, 10, 2, 13, 24, 10, tzinfo=timezone.utc)
    anderson = next(p for p in roster.players if p.source_player_id == "2993874")
    assert (anderson.name, anderson.position, anderson.jersey, anderson.status) == ("Kyle Anderson", "F", "12", "Active")


def test_depth_chart_is_parsed_in_rank_order():
    chart = _provider().get_team_depth_chart("28", 2026)  # 2026-27 is ESPN's season 2027
    assert set(chart.positions) == {"pg", "sg", "sf", "pf", "c"}
    assert chart.positions["pg"][:3] == ["4395724", "4432241", "4684272"]
    assert chart.positions["sf"][0] == "6450"


def test_pregame_lineup_lists_players_but_has_no_starter_information():
    """Real response about 33 hours before tip-off."""
    lineup = _provider().get_game_lineup("401902644", "28")
    assert len(lineup.entries) == 21 and lineup.lineup_available is False
    assert lineup.has_starter_field is False
    assert all(e.starter is None and e.active is None and e.did_not_play is None and e.reason is None for e in lineup.entries)


def test_a_finished_games_lineup_does_carry_starters():
    lineup = _provider().get_game_lineup("401810433", "29")
    assert lineup.has_starter_field is True
    assert sum(1 for e in lineup.entries if e.starter) == 5
    assert any(e.did_not_play for e in lineup.entries)


# --- recording availability ------------------------------------------------


def _teams(db):
    ingest_teams(db, [NbaTeamRecord(source=SRC, source_team_id=str(i), name=f"Team {i}", abbreviation=f"T{i}") for i in (1, 2, 3)])


def _item(player_id="p1", status="Day-To-Day", *, name="Player One", team="1", injury="Knee", published=None, comment="note") -> NbaInjuryItem:
    raw = {"id": f"r-{player_id}", "status": status, "date": str(published), "details": {"type": injury}, "shortComment": comment, "athlete": {"displayName": name}}
    return NbaInjuryItem(
        source_player_id=player_id, player_name=name, position="G", source_team_id=team, source_athlete_team_id=team, source_report_id=f"r-{player_id}",
        source_status=status, source_status_type=None, source_published_at=published, injury_type=injury, injury_location=None, injury_side=None, injury_detail=None,
        fantasy_status=None, expected_return_date=None, short_comment=comment, long_comment=None, raw=raw,
    )


def _feed(items, at: datetime) -> NbaInjuryFeed:
    return NbaInjuryFeed(source=SRC, fetched_at=at, source_timestamp=at, payload_sha256="x", items=list(items), teams_listed=len({i.source_team_id for i in items}))


def _player(db, source_player_id="p1") -> NbaPlayer:
    return db.query(NbaPlayer).filter_by(source=SRC, source_player_id=source_player_id).one()


def _rows(db, player) -> list[NbaPlayerAvailabilityReport]:
    return db.query(NbaPlayerAvailabilityReport).filter_by(player_id=player.id).order_by(NbaPlayerAvailabilityReport.id).all()


def test_first_observation_creates_the_player_by_provider_id_and_one_row(db_session):
    _teams(db_session)
    report = record_availability(db_session, _feed([_item(published=T0 - timedelta(hours=3))], T0))
    assert (report.observations_added, report.newly_listed, report.players_created, report.unchanged) == (1, 1, 1, 0)

    player = _player(db_session)
    row = _rows(db_session, player)[0]
    assert (player.display_name, player.source_player_id, player.current_team.name) == ("Player One", "p1", "Team 1")
    assert (row.is_listed, row.source_status, row.status, row.injury_type, row.team.name) == (True, "Day-To-Day", None, "Knee", "Team 1")
    assert (row.source, row.source_report_id, row.source_team_id, row.game_id) == (SRC, "r-p1", "1", None)
    assert row.raw["status"] == "Day-To-Day" and row.content_hash and row.poll_id is not None
    assert db_session.query(NbaEvidencePoll).filter_by(kind="availability").count() == 1


def test_an_unchanged_observation_adds_a_poll_but_no_row(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item()], T0))
    again = record_availability(db_session, _feed([_item()], T0 + timedelta(minutes=30)))
    assert (again.observations_added, again.unchanged) == (0, 1)
    assert len(_rows(db_session, _player(db_session))) == 1
    assert db_session.query(NbaEvidencePoll).count() == 2

    # The state was first seen at T0 and is still confirmed at the later poll.
    evidence = availability_known_at(db_session, _player(db_session).id, T0 + timedelta(hours=1))
    assert evidence.first_observed_at == T0 and evidence.last_confirmed_at == T0 + timedelta(minutes=30)


def test_a_status_transition_keeps_every_state_with_its_observation_time(db_session):
    """Day-To-Day -> Questionable -> Out: three rows, none rewritten."""
    _teams(db_session)
    times = [T0, T0 + timedelta(hours=2), T0 + timedelta(hours=5)]
    for status, at in zip(["Day-To-Day", "Questionable", "Out"], times):
        record_availability(db_session, _feed([_item(status=status)], at))

    rows = _rows(db_session, _player(db_session))
    assert [(r.source_status, r.status) for r in rows] == [("Day-To-Day", None), ("Questionable", "questionable"), ("Out", "out")]
    assert [r.observed_at.replace(tzinfo=timezone.utc) for r in rows] == times
    assert len({r.poll_id for r in rows}) == 3


def test_as_of_query_before_and_after_a_status_change(db_session):
    _teams(db_session)
    change_at = T0 + timedelta(minutes=17)  # 3:17 PM
    record_availability(db_session, _feed([_item(status="Questionable")], T0))
    record_availability(db_session, _feed([_item(status="Out")], change_at))
    player_id = _player(db_session).id

    assert availability_known_at(db_session, player_id, T0 - timedelta(seconds=1)).observation is None
    assert availability_known_at(db_session, player_id, T0).observation.source_status == "Questionable"
    assert availability_known_at(db_session, player_id, change_at - timedelta(seconds=1)).observation.source_status == "Questionable"
    at_change = availability_known_at(db_session, player_id, change_at)
    assert at_change.observation.source_status == "Out" and at_change.first_observed_at == change_at
    assert availability_known_at(db_session, player_id, change_at + timedelta(days=3)).observation.source_status == "Out"


def test_no_future_information_leaks_backwards(db_session):
    """The source dated the entry 1:00 PM, but we only fetched it at 5:00 PM.
    At 3:17 PM the system did not know it - and must say so."""
    _teams(db_session)
    fetched = T0 + timedelta(hours=2)
    record_availability(db_session, _feed([_item(status="Out", published=T0 - timedelta(hours=2))], fetched))
    player_id = _player(db_session).id

    at_317 = availability_known_at(db_session, player_id, T0 + timedelta(minutes=17))
    assert at_317.observation is None and at_317.last_confirmed_at is None and at_317.is_listed is False
    assert team_availability_known_at(db_session, _player(db_session).current_team_id, T0 + timedelta(minutes=17)) == []
    known = availability_known_at(db_session, player_id, fetched)
    assert known.observation.source_status == "Out"
    assert known.observation.source_published_at.replace(tzinfo=timezone.utc) < known.first_observed_at


def test_a_later_poll_never_changes_what_an_earlier_cutoff_returns(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item(status="Questionable")], T0))
    player_id = _player(db_session).id
    before = availability_known_at(db_session, player_id, T0 + timedelta(minutes=30))
    snapshot = (before.observation.id, before.observation.source_status, before.first_observed_at, before.last_confirmed_at)

    record_availability(db_session, _feed([_item(status="Out")], T0 + timedelta(hours=1)))
    record_availability(db_session, _feed([], T0 + timedelta(hours=2)))
    after = availability_known_at(db_session, player_id, T0 + timedelta(minutes=30))
    assert (after.observation.id, after.observation.source_status, after.first_observed_at, after.last_confirmed_at) == snapshot


def test_an_unknown_raw_status_is_preserved_exactly_and_left_unmapped(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item(status="Game Time Decision")], T0))
    row = _rows(db_session, _player(db_session))[0]
    assert (row.source_status, row.status) == ("Game Time Decision", None)


@pytest.mark.parametrize(
    "raw, expected",
    [("Out", "out"), ("out", "out"), ("Questionable", "questionable"), ("Doubtful", "doubtful"), ("Probable", "probable"), ("Day-To-Day", None), ("OFS", None), ("", None), (None, None)],
)
def test_only_the_leagues_own_terms_get_a_canonical_status(raw, expected):
    assert canonical_status(raw) == expected


def test_a_player_disappearing_from_the_feed_is_recorded_as_not_listed_never_as_healthy(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item("p1", "Out"), _item("p2", "Day-To-Day", name="Player Two")], T0))
    gone_at = T0 + timedelta(hours=4)
    report = record_availability(db_session, _feed([_item("p2", "Day-To-Day", name="Player Two")], gone_at))
    assert (report.delisted, report.observations_added, report.unchanged) == (1, 1, 1)

    player = _player(db_session, "p1")
    rows = _rows(db_session, player)
    assert [(r.is_listed, r.source_status, r.status) for r in rows] == [(True, "Out", "out"), (False, None, None)]
    evidence = availability_known_at(db_session, player.id, gone_at)
    assert evidence.is_listed is False and evidence.observation.status is None and evidence.first_observed_at == gone_at
    # Before he dropped off, the earlier state is still what was known.
    assert availability_known_at(db_session, player.id, gone_at - timedelta(minutes=1)).observation.source_status == "Out"

    # Still absent on the next poll: no second "not listed" row.
    record_availability(db_session, _feed([_item("p2", "Day-To-Day", name="Player Two")], gone_at + timedelta(hours=1)))
    assert len(_rows(db_session, player)) == 2
    # Listed again later: a new row, and the gap stays in the history.
    record_availability(db_session, _feed([_item("p1", "Questionable"), _item("p2", "Day-To-Day", name="Player Two")], gone_at + timedelta(hours=2)))
    assert [r.source_status for r in _rows(db_session, player)] == ["Out", None, "Questionable"]


def test_a_player_the_feed_never_listed_has_no_evidence(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item("p1")], T0))
    other = NbaPlayer(display_name="Never Listed", source=SRC, source_player_id="p9")
    db_session.add(other)
    db_session.commit()
    evidence = availability_known_at(db_session, other.id, T0 + timedelta(hours=1))
    assert evidence.observation is None and evidence.is_listed is False
    assert evidence.last_confirmed_at == T0  # the feed WAS read; it simply did not list him


def test_duplicates_are_not_stored(db_session):
    _teams(db_session)
    report = record_availability(db_session, _feed([_item("p1"), _item("p1")], T0))
    assert (report.observations_added, report.duplicate_items) == (1, 1)
    for minutes in (10, 20, 30):
        record_availability(db_session, _feed([_item("p1")], T0 + timedelta(minutes=minutes)))
    assert db_session.query(NbaPlayerAvailabilityReport).count() == 1 and db_session.query(NbaPlayer).count() == 1
    assert db_session.query(NbaEvidencePoll).count() == 4


def test_any_change_in_what_the_source_shows_is_a_new_observation(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item(comment="expected back next week")], T0))
    report = record_availability(db_session, _feed([_item(comment="ruled out for two more weeks")], T0 + timedelta(hours=1)))
    assert (report.changed, report.observations_added) == (1, 1)
    assert [r.short_comment for r in _rows(db_session, _player(db_session))] == ["expected back next week", "ruled out for two more weeks"]


def test_same_name_different_provider_ids_are_different_players(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item("p1", "Out", name="Same Name"), _item("p2", "Day-To-Day", name="Same Name", team="2")], T0))
    players = db_session.query(NbaPlayer).filter_by(display_name="Same Name").all()
    assert {p.source_player_id for p in players} == {"p1", "p2"}
    assert {availability_known_at(db_session, p.id, T0).observation.source_status for p in players} == {"Out", "Day-To-Day"}


def test_team_availability_as_of(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item("p1", "Out"), _item("p2", "Day-To-Day", name="Two"), _item("p3", "Out", name="Three", team="2")], T0))
    record_availability(db_session, _feed([_item("p2", "Out", name="Two"), _item("p3", "Out", name="Three", team="2")], T0 + timedelta(hours=1)))
    team_1 = db_session.query(NbaTeam).filter_by(name="Team 1").one().id

    assert {r.source_status for r in team_availability_known_at(db_session, team_1, T0)} == {"Out", "Day-To-Day"}
    later = team_availability_known_at(db_session, team_1, T0 + timedelta(hours=1))
    assert [(r.player.display_name, r.source_status) for r in later] == [("Two", "Out")]  # p1 is no longer listed


def test_a_feed_that_suddenly_lists_almost_nobody_is_rejected_and_nothing_is_written(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item(f"p{i}", name=f"Player {i}") for i in range(30)], T0))
    with pytest.raises(EvidenceRejected, match="broken response"):
        record_availability(db_session, _feed([_item("p0", name="Player 0")], T0 + timedelta(hours=1)))
    db_session.rollback()
    assert db_session.query(NbaPlayerAvailabilityReport).count() == 30 and db_session.query(NbaEvidencePoll).count() == 1


def test_availability_rows_and_polls_cannot_be_edited_or_deleted(db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item(status="Questionable")], T0))
    row = db_session.query(NbaPlayerAvailabilityReport).one()
    poll = db_session.query(NbaEvidencePoll).one()
    for target, field, value in [(row, "source_status", "Out"), (row, "observed_at", T0 - timedelta(days=1)), (row, "is_listed", False), (poll, "observed_at", T0 - timedelta(days=1))]:
        setattr(target, field, value)
        with pytest.raises(FrozenRecordError):
            db_session.commit()
        db_session.rollback()
    db_session.delete(row)
    with pytest.raises(FrozenRecordError):
        db_session.commit()
    db_session.rollback()
    assert db_session.query(NbaPlayerAvailabilityReport).one().source_status == "Questionable"


def test_real_feed_records_and_rereads_without_duplicates(db_session):
    ingest_teams(db_session, [NbaTeamRecord(source=SRC, source_team_id=i, name=f"Team {i}", abbreviation=f"T{i}") for i in ("1", "5", "6", "11", "28")])
    provider = _provider()
    first = record_availability(db_session, provider.get_injuries())
    second = record_availability(db_session, provider.get_injuries())
    assert (first.observations_added, first.players_created, first.statuses) == (17, 17, {"Out": 2, "Day-To-Day": 15})
    assert (second.observations_added, second.unchanged) == (0, 17)
    watson = db_session.query(NbaPlayerAvailabilityReport).join(NbaPlayer).filter(NbaPlayer.display_name == "Peyton Watson").one()
    assert (watson.source_team_id, watson.source_athlete_team_id, watson.status) == ("5", "7", None)


# --- team roster / depth chart / lineup observations -----------------------


def _roster(players, at) -> NbaTeamRoster:
    return NbaTeamRoster(source=SRC, source_team_id="1", fetched_at=at, source_timestamp=at, players=[NbaRosterPlayer(p, f"Name {p}", "G", None, "Active") for p in players])


def test_team_roster_observations_are_change_only_and_queryable_as_of(db_session):
    _teams(db_session)
    team = db_session.query(NbaTeam).filter_by(name="Team 1").one()
    assert record_team_roster(db_session, _roster(["a", "b"], T0)) is True
    assert record_team_roster(db_session, _roster(["b", "a"], T0 + timedelta(hours=1))) is False  # same roster, different order
    assert record_team_roster(db_session, _roster(["a", "b", "c"], T0 + timedelta(hours=2))) is True
    assert db_session.query(NbaTeamObservation).count() == 2 and db_session.query(NbaEvidencePoll).filter_by(kind="team_roster").count() == 3

    def ids(cutoff):
        obs = team_observation_known_at(db_session, team.id, "roster", cutoff)
        return None if obs is None else [p["id"] for p in obs.payload["players"]]

    assert ids(T0 - timedelta(seconds=1)) is None
    assert ids(T0 + timedelta(minutes=90)) == ["a", "b"]
    assert ids(T0 + timedelta(hours=2)) == ["a", "b", "c"]


def test_depth_chart_observations_are_separate_from_roster_observations(db_session):
    _teams(db_session)
    team = db_session.query(NbaTeam).filter_by(name="Team 1").one()
    record_team_roster(db_session, _roster(["a"], T0))
    chart = NbaDepthChart(source=SRC, source_team_id="1", fetched_at=T0, positions={"pg": ["a", "b"]})
    assert record_team_depth_chart(db_session, chart) is True
    assert record_team_depth_chart(db_session, replace(chart, fetched_at=T0 + timedelta(hours=1), positions={"pg": ["b", "a"]})) is True  # rank order changed
    assert team_observation_known_at(db_session, team.id, "depth_chart", T0).payload == {"positions": {"pg": ["a", "b"]}}
    assert team_observation_known_at(db_session, team.id, "depth_chart", T0 + timedelta(hours=1)).payload == {"positions": {"pg": ["b", "a"]}}
    with pytest.raises(EvidenceRejected):
        record_team_roster(db_session, replace(_roster(["a"], T0), source_team_id="99"))


def _game(db, game_id="g1", *, tipoff=TIPOFF, status="scheduled", home="1", away="2", now=T0, **scores) -> NbaGame:
    record = NbaGameRecord(
        source=SRC, source_game_id=game_id, season_start_year=2026, season_type="regular", game_date=(tipoff - timedelta(hours=5)).date(), scheduled_start=tipoff,
        status=status, home_source_team_id=home, away_source_team_id=away, home_team_name="Team 1", away_team_name="Team 2", source_status=f"STATUS_{status.upper()}", **scores,
    )
    ingest_games(db, [record], now=now)
    return db.query(NbaGame).filter_by(source=SRC, source_game_id=game_id).one()


def _lineup(at, *, starters: list[str] | None, players=("a", "b", "c", "d", "e", "f"), team="1", game="g1") -> NbaGameLineup:
    entries = [
        NbaLineupEntry(p, None if starters is None else p in starters, None if starters is None else True, None if starters is None else False, None) for p in players
    ]
    return NbaGameLineup(source=SRC, source_game_id=game, source_team_id=team, fetched_at=at, lineup_available=starters is not None, has_starter_field=starters is not None, entries=entries)


def test_lineup_observations_record_when_starters_became_visible(db_session):
    _teams(db_session)
    game = _game(db_session)
    team = db_session.query(NbaTeam).filter_by(name="Team 1").one()
    early, late = TIPOFF - timedelta(hours=5), TIPOFF - timedelta(minutes=25)

    assert record_game_lineup(db_session, game, _lineup(early, starters=None)) is True
    assert record_game_lineup(db_session, game, _lineup(early + timedelta(hours=1), starters=None)) is False
    assert record_game_lineup(db_session, game, _lineup(late, starters=["a", "b", "c", "d", "e"])) is True

    before = game_lineup_known_at(db_session, game.id, team.id, late - timedelta(seconds=1))
    assert (before.has_starter_field, before.starters_flagged, before.players_listed) == (False, 0, 6)
    after = game_lineup_known_at(db_session, game.id, team.id, late)
    assert (after.has_starter_field, after.starters_flagged, after.lineup_available) == (True, 5, True)
    assert after.tipoff_at_observation.replace(tzinfo=timezone.utc) == TIPOFF and after.game_status_at_observation == "scheduled"
    assert game_lineup_known_at(db_session, game.id, team.id, early - timedelta(seconds=1)) is None
    assert db_session.query(NbaGameLineupObservation).count() == 2

    with pytest.raises(EvidenceRejected):
        record_game_lineup(db_session, game, _lineup(late, starters=None, team="3"))  # team 3 is not in this game


# --- schedule observations: postponed and rescheduled games ----------------


def test_a_postponed_game_keeps_its_earlier_schedule_as_history(db_session):
    _teams(db_session)
    game = _game(db_session, now=T0)
    postponed_at = T0 + timedelta(hours=3)
    _game(db_session, status="postponed", now=postponed_at)
    db_session.refresh(game)

    assert game.status == "postponed"  # nba_games holds the CURRENT state
    observations = db_session.query(NbaGameScheduleObservation).filter_by(game_id=game.id).order_by(NbaGameScheduleObservation.id).all()
    assert [(o.status, o.source_status) for o in observations] == [("scheduled", "STATUS_SCHEDULED"), ("postponed", "STATUS_POSTPONED")]
    assert game_schedule_known_at(db_session, game.id, postponed_at - timedelta(minutes=1)).status == "scheduled"
    assert game_schedule_known_at(db_session, game.id, postponed_at).status == "postponed"
    assert game_schedule_known_at(db_session, game.id, T0 - timedelta(seconds=1)) is None


def test_a_rescheduled_game_remembers_the_tipoff_time_we_believed_then(db_session):
    _teams(db_session)
    game = _game(db_session, now=T0)
    moved_at, new_tipoff = T0 + timedelta(hours=2), TIPOFF + timedelta(days=1, hours=1)
    _game(db_session, tipoff=new_tipoff, now=moved_at)
    db_session.refresh(game)

    assert game.scheduled_start.replace(tzinfo=timezone.utc) == new_tipoff
    assert game_schedule_known_at(db_session, game.id, moved_at - timedelta(seconds=1)).scheduled_start.replace(tzinfo=timezone.utc) == TIPOFF
    assert game_schedule_known_at(db_session, game.id, moved_at).scheduled_start.replace(tzinfo=timezone.utc) == new_tipoff
    # Seeing the same schedule again adds nothing.
    _game(db_session, tipoff=new_tipoff, now=moved_at + timedelta(hours=1))
    assert db_session.query(NbaGameScheduleObservation).count() == 2


def test_schedule_observations_cannot_be_rewritten(db_session):
    _teams(db_session)
    _game(db_session)
    observation = db_session.query(NbaGameScheduleObservation).one()
    observation.scheduled_start = TIPOFF + timedelta(days=5)
    with pytest.raises(FrozenRecordError):
        db_session.commit()
    db_session.rollback()


# --- API -------------------------------------------------------------------


def test_availability_api_answers_as_of_a_given_time(client, db_session):
    _teams(db_session)
    record_availability(db_session, _feed([_item(status="Questionable")], T0))
    record_availability(db_session, _feed([_item(status="Out")], T0 + timedelta(minutes=17)))
    player_id = _player(db_session).id

    before = client.get(f"/api/nba/players/{player_id}/availability", params={"as_of": "2026-10-20T15:16:59Z"}).json()
    after = client.get(f"/api/nba/players/{player_id}/availability", params={"as_of": "2026-10-20T15:17:00Z"}).json()
    assert before["observation"]["source_status"] == "Questionable" and before["observation"]["status"] == "questionable"
    assert after["observation"]["source_status"] == "Out" and after["first_observed_at"].startswith("2026-10-20T15:17:00")
    assert (after["player_name"], after["source_player_id"]) == ("Player One", "p1")

    earlier = client.get(f"/api/nba/players/{player_id}/availability", params={"as_of": "2026-10-20T14:00:00Z"}).json()
    assert earlier["observation"] is None and earlier["last_confirmed_at"] is None
    assert client.get("/api/nba/players/999999/availability").status_code == 404
