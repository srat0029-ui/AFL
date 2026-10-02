"""NBA stats provider parsing and ingestion.

Parsing is exercised against trimmed copies of REAL ESPN responses captured
on 2026-10-02 (tests/fixtures/nba/). Identity, trade, resume and
duplicate-handling rules are exercised with small hand-built provider
records, where a real response would not contain the case being tested.
All HTTP is mocked - never a real network call in an automated test."""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.nba import (
    NbaBoxScoreState,
    NbaGame,
    NbaGameStatus,
    NbaPlayer,
    NbaPlayerAvailabilityReport,
    NbaPlayerGameLog,
    NbaScheduleSyncDate,
    NbaTeam,
)
from app.nba.asof import game_logs_known_at
from app.nba.cli import season_date_range
from app.nba.ingestion import ingest_box_score, ingest_games, ingest_teams, sync_box_scores, sync_schedule, sync_teams
from app.providers.nba.espn import PROVIDER_NAME, EspnNbaError, EspnNbaProvider, _status
from app.providers.nba.types import NbaBoxScore, NbaGameRecord, NbaPlayerBoxLine, NbaTeamRecord

FIXTURES = Path(__file__).parent / "fixtures" / "nba"
GAME_ID = "401810433"  # Memphis @ Orlando, 2026-01-15, final 111-118
SHELL_GAME_ID = "400827889"  # Cleveland @ Chicago, 2015-10-27, final 95-97 - ESPN's box score is an empty shell
TODAY = date(2026, 10, 2)

_SCOREBOARDS = {
    "20260115": "espn_scoreboard_20260115_final.json",
    "20260215": "espn_scoreboard_20260215_allstar.json",
    "20261022": "espn_scoreboard_20261022_scheduled.json",
    "20151027": "espn_scoreboard_20151027_final.json",
    "20200801": "espn_scoreboard_20200801_bubble.json",
    "20201223": "espn_scoreboard_20201223_postponed.json",
}
_SUMMARIES = {GAME_ID: "espn_summary_401810433.json", SHELL_GAME_ID: "espn_summary_400827889_empty_shell.json"}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _provider(overrides: dict | None = None, requests: list | None = None) -> EspnNbaProvider:
    """A provider whose transport serves the recorded fixtures.
    `overrides` maps a path ("/summary") to a replacement body or status."""
    overrides = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/nba", 1)[1]
        if requests is not None:
            requests.append((path, dict(request.url.params)))
        if path in overrides:
            override = overrides[path]
            return httpx.Response(override) if isinstance(override, int) else httpx.Response(200, json=override)
        if path == "/teams":
            return httpx.Response(200, json=_fixture("espn_teams.json"))
        if path == "/scoreboard":
            name = _SCOREBOARDS.get(request.url.params["dates"])
            return httpx.Response(200, json=_fixture(name) if name else {"events": []})
        if path == "/summary":
            name = _SUMMARIES.get(request.url.params["event"])
            return httpx.Response(200, json=_fixture(name)) if name else httpx.Response(404)
        return httpx.Response(404)

    client = httpx.Client(base_url="https://site.api.espn.com/apis/site/v2/sports/basketball/nba", transport=httpx.MockTransport(handler))
    return EspnNbaProvider(client=client, request_interval_seconds=0)


# --- provider parsing (real fixtures) --------------------------------------


def test_teams_are_the_thirty_franchises():
    teams = _provider().get_teams()
    assert len(teams) == 30 and {t.source for t in teams} == {"espn"}
    celtics = next(t for t in teams if t.abbreviation == "BOS")
    assert (celtics.name, celtics.source_team_id) == ("Boston Celtics", "2")


def test_final_game_is_parsed_with_scores_season_and_raw_source_values():
    game = next(g for g in _provider().get_games(date(2026, 1, 15)) if g.source_game_id == GAME_ID)
    assert (game.away_team_name, game.home_team_name) == ("Memphis Grizzlies", "Orlando Magic")
    assert (game.away_source_team_id, game.home_source_team_id) == ("29", "19")
    assert (game.away_score, game.home_score, game.status) == (111, 118, "final")
    assert game.game_date == date(2026, 1, 15)
    assert game.scheduled_start == datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc)
    # Normalised values, with exactly what ESPN said kept alongside.
    assert (game.season_start_year, game.season_type) == (2025, "regular")
    assert (game.source_status, game.source_season_type) == ("STATUS_FINAL", "2:regular-season")


def test_scheduled_game_has_no_score():
    games = _provider().get_games(date(2026, 10, 22))
    assert games and all(g.status == "scheduled" and g.home_score is None and g.away_score is None for g in games)
    assert {g.season_start_year for g in games} == {2026}


def test_a_real_postponed_game_has_no_score_and_keeps_its_raw_status():
    """Oklahoma City at Houston, 23 December 2020, postponed. ESPN still
    publishes "0" for each team's score; that is not a result."""
    game = _provider().get_games(date(2020, 12, 23))[0]
    assert (game.source_game_id, game.status, game.source_status) == ("401267176", "postponed", "STATUS_POSTPONED")
    assert game.home_score is None and game.away_score is None
    assert game.season_start_year == 2020


def test_all_star_games_are_dropped_when_real_team_ids_are_supplied():
    provider = _provider()
    assert len(provider.get_games(date(2026, 2, 15))) == 4  # ESPN lists them as regular-season events
    team_ids = {t.source_team_id for t in provider.get_teams()}
    assert provider.get_games(date(2026, 2, 15), team_ids=team_ids) == []
    assert len(provider.get_games(date(2026, 1, 15), team_ids=team_ids)) == 3


@pytest.mark.parametrize(
    "status_type, expected",
    [
        ({"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}, "scheduled"),
        ({"name": "STATUS_IN_PROGRESS", "state": "in", "completed": False}, "in_progress"),
        ({"name": "STATUS_HALFTIME", "state": "in", "completed": False}, "in_progress"),
        ({"name": "STATUS_FINAL", "state": "post", "completed": True}, "final"),
        ({"name": "STATUS_POSTPONED", "state": "post", "completed": False}, "postponed"),
        ({"name": "STATUS_CANCELED", "state": "post", "completed": False}, "cancelled"),
        # "post" without completed is not a result.
        ({"name": "STATUS_SOMETHING_NEW", "state": "post", "completed": False}, "in_progress"),
    ],
)
def test_status_mapping(status_type, expected):
    assert _status(status_type) == expected


def test_box_score_lines_are_read_by_stat_key():
    box = _provider().get_box_score(GAME_ID)
    assert box.status == "final" and box.source_status == "STATUS_FINAL" and len(box.lines) == 26
    assert box.team_scores == {"19": 118, "29": 111}
    jackson = next(line for line in box.lines if line.source_player_id == "4277961")
    assert jackson.player_name == "Jaren Jackson Jr." and jackson.position == "F" and jackson.source_team_id == "29"
    assert (jackson.started, jackson.did_not_play, jackson.minutes) == (True, False, 33.0)
    assert (jackson.points, jackson.rebounds, jackson.assists) == (30, 3, 1)
    assert (jackson.field_goals_made, jackson.field_goals_attempted) == (12, 22)
    assert (jackson.three_pointers_made, jackson.three_pointers_attempted) == (3, 5)
    assert (jackson.free_throws_made, jackson.free_throws_attempted) == (3, 3)
    assert (jackson.offensive_rebounds, jackson.defensive_rebounds, jackson.plus_minus) == (0, 3, -21)


def test_box_score_starters_are_five_per_team():
    box = _provider().get_box_score(GAME_ID)
    for team_id in ("19", "29"):
        assert sum(1 for line in box.lines if line.source_team_id == team_id and line.started) == 5
    # A bench player who played is not a starter, whatever ESPN's `active` flag says.
    williams = next(line for line in box.lines if line.source_player_id == "4397227")
    assert williams.started is False and williams.did_not_play is False and williams.minutes == 21.0


def test_minutes_are_whole_numbers_exactly_as_published():
    """ESPN publishes whole minutes. They are stored as given - no seconds
    are invented - and typed as a number so a model can use them directly."""
    played = [line for line in _provider().get_box_score(GAME_ID).lines if not line.did_not_play]
    assert played and all(isinstance(line.minutes, float) and line.minutes == int(line.minutes) for line in played)


def test_box_score_did_not_play_keeps_the_reason_only_then():
    box = _provider().get_box_score(GAME_ID)
    clarke = next(line for line in box.lines if line.source_player_id == "3906665")
    assert clarke.did_not_play is True and clarke.did_not_play_reason == "RIGHT CALF STRAIN"
    assert clarke.minutes is None and clarke.points is None and clarke.started is None
    # ESPN fills `reason` with "COACH'S DECISION" even for players who played.
    assert all(line.did_not_play_reason is None for line in box.lines if not line.did_not_play)


def test_box_score_parsing_does_not_depend_on_stat_order():
    body = _fixture("espn_summary_401810433.json")
    for team in body["boxscore"]["players"]:
        for group in team["statistics"]:
            group["keys"] = list(reversed(group["keys"]))
            for athlete in group["athletes"]:
                athlete["stats"] = list(reversed(athlete["stats"]))
    assert _provider({"/summary": body}).get_box_score(GAME_ID).lines == _provider().get_box_score(GAME_ID).lines


def test_empty_shell_box_score_is_parsed_as_published_not_repaired():
    """The provider reports what ESPN gave: placeholder dashes become None,
    the zeros stay zeros. Deciding it is unusable is ingestion's job."""
    box = _provider().get_box_score(SHELL_GAME_ID)
    assert box.status == "final" and box.team_scores == {"4": 97, "5": 95}
    played = [line for line in box.lines if not line.did_not_play]
    assert played and all(line.minutes is None and line.points == 0 for line in played)


# --- malformed and missing fields ------------------------------------------


def test_provider_errors_are_raised_as_espn_errors():
    with pytest.raises(EspnNbaError, match="HTTP 403"):
        _provider({"/scoreboard": 403}).get_games(date(2026, 1, 15))
    with pytest.raises(EspnNbaError, match="expected shape"):
        _provider({"/summary": {"unexpected": True}}).get_box_score(GAME_ID)
    with pytest.raises(EspnNbaError, match="expected shape"):
        _provider({"/teams": {"sports": []}}).get_teams()
    broken_event = {"events": [{"id": "1", "season": {"year": 2026, "type": 2}, "competitions": [{"competitors": []}], "status": {"type": {}}}]}
    with pytest.raises(EspnNbaError, match="expected shape"):
        _provider({"/scoreboard": broken_event}).get_games(date(2026, 1, 15))


def test_a_player_row_without_an_id_is_counted_not_guessed_at():
    body = _fixture("espn_summary_401810433.json")
    athletes = body["boxscore"]["players"][0]["statistics"][0]["athletes"]
    del athletes[0]["athlete"]["id"]
    athletes[1]["athlete"]["displayName"] = None
    points = {a["athlete"]["displayName"] or "x": int(a["stats"][1]) for a in athletes[:2]}
    box = _provider({"/summary": body}).get_box_score(GAME_ID)
    assert box.malformed_rows == 2 and len(box.lines) == 24
    # Their points cannot be attributed to a player but are still carried, per team.
    assert box.unattributed_points == {"29": sum(points.values())} and sum(points.values()) > 0


def test_missing_and_placeholder_stat_values_become_none_not_zero():
    body = _fixture("espn_summary_401810433.json")
    group = body["boxscore"]["players"][0]["statistics"][0]
    row = next(a for a in group["athletes"] if a["athlete"]["id"] == "4277961")
    row["stats"][group["keys"].index("minutes")] = "--"
    row["stats"][group["keys"].index("assists")] = ""
    row["stats"][group["keys"].index("fieldGoalsMade-fieldGoalsAttempted")] = "garbage"
    row["stats"] = row["stats"][:-1]  # a stat list shorter than the key list
    line = next(x for x in _provider({"/summary": body}).get_box_score(GAME_ID).lines if x.source_player_id == "4277961")
    assert line.minutes is None and line.assists is None and line.plus_minus is None
    assert (line.field_goals_made, line.field_goals_attempted) == (None, None)
    assert line.points == 30


def test_a_season_type_this_project_does_not_model_is_dropped():
    body = _fixture("espn_scoreboard_20260115_final.json")
    body["events"][0]["season"]["type"] = 4
    assert len(_provider({"/scoreboard": body}).get_games(date(2026, 1, 15))) == 2


def test_one_request_per_date_never_a_range():
    requests: list = []
    _provider(requests=requests).get_games(date(2026, 1, 15))
    assert requests == [("/scoreboard", {"dates": "20260115"})]


# --- season boundaries -----------------------------------------------------


def test_season_date_ranges_including_the_two_pandemic_seasons():
    assert season_date_range(2025) == (date(2025, 9, 20), date(2026, 6, 30))
    # 2019-20 finished in the Orlando bubble in October 2020; 2020-21 started in December.
    assert season_date_range(2019)[1] == date(2020, 10, 12)
    assert season_date_range(2020) == (date(2020, 12, 1), date(2021, 7, 21))


def test_season_membership_comes_from_the_provider_not_the_calendar():
    """A bubble game played on 1 August 2020 belongs to the 2019-20 season,
    even though by the calendar it sits where a new season would normally
    be about to start."""
    games = _provider().get_games(date(2020, 8, 1))
    assert games and {g.season_start_year for g in games} == {2019}
    assert {g.season_type for g in games} == {"regular"}


@pytest.mark.parametrize("espn_type, slug, expected", [(1, "preseason", "preseason"), (2, "regular-season", "regular"), (3, "post-season", "playoffs"), (5, "play-in-season", "play_in")])
def test_season_types_are_classified_and_the_raw_value_kept(espn_type, slug, expected):
    body = _fixture("espn_scoreboard_20260115_final.json")
    body["events"] = body["events"][:1]
    body["events"][0]["season"] = {"year": 2026, "type": espn_type, "slug": slug}
    game = _provider({"/scoreboard": body}).get_games(date(2026, 1, 15))[0]
    assert (game.season_type, game.source_season_type) == (expected, f"{espn_type}:{slug}")


# --- ingestion against the real fixtures -----------------------------------


def _synced(db, start=date(2026, 1, 15), end=date(2026, 1, 15)):
    provider = _provider()
    sync_teams(db, provider)
    sync_schedule(db, provider, start, end, source=PROVIDER_NAME, today=TODAY)
    return provider


def test_team_sync_is_idempotent_and_keeps_the_provider_id(db_session):
    provider = _provider()
    first, second = sync_teams(db_session, provider), sync_teams(db_session, provider)
    assert (first.created, second.created, second.updated, second.renamed) == (30, 0, 0, [])
    assert db_session.query(NbaTeam).count() == 30
    assert db_session.query(NbaTeam).filter_by(abbreviation="ORL").one().external_ids == {"espn": "19"}


def test_schedule_sync_requires_teams_first(db_session):
    with pytest.raises(ValueError, match="team sync first"):
        sync_schedule(db_session, _provider(), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, today=TODAY)


def test_schedule_sync_stores_provenance_and_skips_all_star(db_session):
    provider = _provider()
    sync_teams(db_session, provider)
    report = sync_schedule(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, today=TODAY)
    assert (report.dates_requested, report.games.created) == (1, 3)

    all_star = sync_schedule(db_session, provider, date(2026, 2, 15), date(2026, 2, 15), source=PROVIDER_NAME, today=TODAY)
    assert all_star.games.seen == 0 and db_session.query(NbaGame).count() == 3

    game = db_session.query(NbaGame).filter_by(source="espn", source_game_id=GAME_ID).one()
    assert (game.away_team.abbreviation, game.home_team.abbreviation, game.status) == ("MEM", "ORL", "final")
    assert (game.away_score, game.home_score, game.season_start_year, game.season_type) == (111, 118, 2025, "regular")
    assert (game.source_status, game.source_season_type) == ("STATUS_FINAL", "2:regular-season")
    assert game.source_synced_at is not None and game.box_score_state is None


def test_rerunning_the_schedule_sync_skips_settled_dates_and_changes_nothing(db_session):
    _synced(db_session)
    requests: list = []
    again = sync_schedule(db_session, _provider(requests=requests), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, today=TODAY)
    assert requests == [] and (again.dates_requested, again.dates_skipped_settled) == (0, 1)

    refreshed = sync_schedule(db_session, _provider(), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, today=TODAY, refresh=True)
    assert (refreshed.games.created, refreshed.games.updated, refreshed.games.unchanged) == (0, 0, 3)
    assert db_session.query(NbaGame).count() == 3 and db_session.query(NbaScheduleSyncDate).count() == 1


def test_dates_with_unplayed_games_or_too_recent_are_always_requested_again(db_session):
    provider = _provider()
    sync_teams(db_session, provider)
    # Future fixtures, and a past date fetched "today": neither is settled.
    sync_schedule(db_session, provider, date(2026, 10, 22), date(2026, 10, 22), source=PROVIDER_NAME, today=TODAY)
    sync_schedule(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, today=date(2026, 1, 16))
    assert [c.is_settled for c in db_session.query(NbaScheduleSyncDate).order_by(NbaScheduleSyncDate.game_date)] == [False, False]

    requests: list = []
    sync_schedule(db_session, _provider(requests=requests), date(2026, 1, 15), date(2026, 10, 22), source=PROVIDER_NAME, today=TODAY)
    assert {params["dates"] for _, params in requests} >= {"20260115", "20261022"}
    assert db_session.query(NbaScheduleSyncDate).filter_by(game_date=date(2026, 1, 15)).one().is_settled is True


def test_schedule_sync_carries_on_past_a_failed_date_and_retries_it_next_time(db_session):
    sync_teams(db_session, _provider())
    report = sync_schedule(db_session, _provider({"/scoreboard": 500}), date(2026, 1, 15), date(2026, 1, 16), source=PROVIDER_NAME, today=TODAY)
    assert report.dates_requested == 2 and len(report.dates_failed) == 2 and report.games.seen == 0
    assert db_session.query(NbaScheduleSyncDate).count() == 0  # a failed date leaves no checkpoint

    retry = sync_schedule(db_session, _provider(), date(2026, 1, 15), date(2026, 1, 16), source=PROVIDER_NAME, today=TODAY)
    assert retry.dates_requested == 2 and retry.games.created == 3


def test_box_score_sync_writes_logs_and_players_with_provider_ids(db_session):
    provider = _synced(db_session)
    report = sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, limit=1)
    assert (report.games_ingested, report.players_created, report.logs_created) == (1, 26, 26)

    game = db_session.query(NbaGame).filter_by(source_game_id=GAME_ID).one()
    assert game.box_score_state == NbaBoxScoreState.INGESTED.value and game.box_score_synced_at is not None

    player = db_session.query(NbaPlayer).filter_by(source="espn", source_player_id="4277961").one()
    log = db_session.query(NbaPlayerGameLog).filter_by(player_id=player.id).one()
    assert (log.team.abbreviation, log.opponent_team.abbreviation, log.is_home, log.source) == ("MEM", "ORL", False, "espn")
    assert (log.started, log.minutes, log.points, log.rebounds, log.assists) == (True, 33.0, 30, 3, 1)
    assert log.recorded_at is not None and player.current_team.abbreviation == "MEM"
    assert db_session.query(NbaPlayerGameLog).filter_by(started=True).count() == 10


def test_dnp_players_are_stored_as_rows_with_no_stats(db_session):
    provider = _synced(db_session)
    sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, limit=1)
    dnp = db_session.query(NbaPlayerGameLog).filter_by(did_not_play=True).all()
    assert len(dnp) == 6
    assert all(log.minutes is None and log.points is None and log.started is None and log.did_not_play_reason for log in dnp)


def test_rerunning_the_box_score_sync_fetches_only_what_is_outstanding(db_session):
    provider = _synced(db_session)
    sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, limit=1)

    requests: list = []
    again = sync_box_scores(db_session, _provider(requests=requests), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME)
    # The two games with no recorded fixture 404; the ingested one is not asked for.
    assert len(requests) == 2 and GAME_ID not in {params["event"] for _, params in requests}
    assert len(again.games_failed) == 2 and db_session.query(NbaPlayerGameLog).count() == 26
    assert db_session.query(NbaGame).filter_by(box_score_state=NbaBoxScoreState.FAILED.value).count() == 2

    # A failed game is retried on the next run; an ingested one still is not.
    retry: list = []
    sync_box_scores(db_session, _provider(requests=retry), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME)
    assert len(retry) == 2

    refreshed = sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, refresh=True, limit=1)
    assert (refreshed.logs_created, refreshed.logs_updated, refreshed.logs_unchanged) == (0, 0, 26)


def test_an_interrupted_backfill_resumes_without_duplicates(db_session):
    """Kill the process partway through (after one game is committed and
    mid-way through the schedule), then run it again."""

    class Killed(BaseException):  # not an Exception: nothing in the pipeline may swallow it
        pass

    provider = _provider()
    sync_teams(db_session, provider)

    class DiesOnSecondDate:
        def __init__(self):
            self.calls = 0

        def get_games(self, on, *, team_ids=None):
            self.calls += 1
            if self.calls == 2:
                raise Killed()
            return provider.get_games(on, team_ids=team_ids)

    with pytest.raises(Killed):
        sync_schedule(db_session, DiesOnSecondDate(), date(2026, 1, 15), date(2026, 1, 17), source=PROVIDER_NAME, today=TODAY)
    db_session.rollback()
    assert db_session.query(NbaGame).count() == 3 and db_session.query(NbaScheduleSyncDate).count() == 1

    requests: list = []
    resumed = sync_schedule(db_session, _provider(requests=requests), date(2026, 1, 15), date(2026, 1, 17), source=PROVIDER_NAME, today=TODAY)
    assert [params["dates"] for _, params in requests] == ["20260116", "20260117"]  # the finished date is not re-requested
    assert resumed.dates_skipped_settled == 1 and db_session.query(NbaGame).count() == 3

    class DiesOnSecondGame:
        def __init__(self):
            self.calls = 0

        def get_box_score(self, source_game_id):
            self.calls += 1
            if self.calls == 2:
                raise Killed()
            return provider.get_box_score(source_game_id)

    with pytest.raises(Killed):
        sync_box_scores(db_session, DiesOnSecondGame(), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME)
    db_session.rollback()
    assert db_session.query(NbaPlayerGameLog).count() == 26  # the first game was committed before the kill

    requests = []
    sync_box_scores(db_session, _provider(requests=requests), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME)
    assert GAME_ID not in {params["event"] for _, params in requests} and len(requests) == 2
    assert db_session.query(NbaPlayerGameLog).count() == 26 and db_session.query(NbaPlayer).count() == 26


def test_an_empty_shell_box_score_is_rejected_and_nothing_is_stored(db_session):
    """Real case: ESPN lists every player in this 2015 game with "--" minutes
    and 0 points. Stored as-is, that is LeBron James scoring zero."""
    provider = _synced(db_session, date(2015, 10, 27), date(2015, 10, 27))
    report = sync_box_scores(db_session, provider, date(2015, 10, 27), date(2015, 10, 27), source=PROVIDER_NAME)

    assert report.games_ingested == 0 and len(report.games_rejected) == 1
    assert "player points 0 != score 97" in report.games_rejected[0]
    assert db_session.query(NbaPlayerGameLog).count() == 0 and db_session.query(NbaPlayer).count() == 0
    game = db_session.query(NbaGame).filter_by(source_game_id=SHELL_GAME_ID).one()
    assert game.box_score_state == NbaBoxScoreState.REJECTED.value

    # Not retried on a normal re-run (it would loop forever); retried on request.
    requests: list = []
    sync_box_scores(db_session, _provider(requests=requests), date(2015, 10, 27), date(2015, 10, 27), source=PROVIDER_NAME)
    assert requests == []
    sync_box_scores(db_session, _provider(requests=requests), date(2015, 10, 27), date(2015, 10, 27), source=PROVIDER_NAME, retry_empty=True)
    assert len(requests) == 1


def test_a_box_score_that_is_not_final_is_never_stored(db_session):
    provider = _synced(db_session)
    game = db_session.query(NbaGame).filter_by(source_game_id=GAME_ID).one()
    in_progress = replace(provider.get_box_score(GAME_ID), status=NbaGameStatus.IN_PROGRESS.value)

    report = ingest_box_score(db_session, game, in_progress)
    assert report.games_ingested == 0 and report.games_not_final == [GAME_ID]
    assert db_session.query(NbaPlayerGameLog).count() == 0 and db_session.query(NbaPlayer).count() == 0
    assert game.box_score_state == NbaBoxScoreState.NOT_FINAL.value


def test_a_final_box_score_with_no_player_rows_is_reported_not_counted(db_session):
    provider = _synced(db_session)
    game = db_session.query(NbaGame).filter_by(source_game_id=GAME_ID).one()
    report = ingest_box_score(db_session, game, replace(provider.get_box_score(GAME_ID), lines=[]))
    assert report.games_ingested == 0 and report.games_without_lines == [GAME_ID]
    assert game.box_score_state == NbaBoxScoreState.NO_PLAYER_ROWS.value


def test_box_score_sync_only_fetches_final_competitive_games(db_session):
    _synced(db_session, date(2026, 10, 22), date(2026, 10, 22))  # scheduled games only
    requests: list = []
    report = sync_box_scores(db_session, _provider(requests=requests), date(2026, 10, 22), date(2026, 10, 22), source=PROVIDER_NAME)
    assert report.games_seen == 0 and requests == []

    # A final PRESEASON game is kept in the schedule but its box score is not fetched by default.
    _synced(db_session)
    game = db_session.query(NbaGame).filter_by(source_game_id=GAME_ID).one()
    game.season_type = "preseason"
    db_session.commit()
    sync_box_scores(db_session, _provider(requests=requests), date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME)
    assert GAME_ID not in {params["event"] for _, params in requests}


def test_ingested_logs_respect_the_as_of_boundary(db_session):
    """A box score that is in the table must still be invisible to a
    prediction made before that game finished."""
    provider = _synced(db_session)
    sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, limit=1)
    player = db_session.query(NbaPlayer).filter_by(source_player_id="4277961").one()
    tipoff = datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc)

    assert game_logs_known_at(db_session, player.id, tipoff - timedelta(hours=1)) == []
    assert game_logs_known_at(db_session, player.id, tipoff + timedelta(hours=2)) == []
    assert [log.points for log in game_logs_known_at(db_session, player.id, tipoff + timedelta(hours=12))] == [30]

    # Exhibition games are outside the modelling dataset unless asked for.
    db_session.query(NbaGame).filter_by(source_game_id=GAME_ID).one().season_type = "preseason"
    db_session.commit()
    assert game_logs_known_at(db_session, player.id, tipoff + timedelta(hours=12)) == []
    assert len(game_logs_known_at(db_session, player.id, tipoff + timedelta(hours=12), season_types=("preseason",))) == 1


# --- identity, trades and duplicates (hand-built provider records) ---------

SRC = "testsrc"
T0 = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)


def _teams(db, *names):
    ingest_teams(db, [NbaTeamRecord(source=SRC, source_team_id=f"t{i}", name=name, abbreviation=name[:3].upper()) for i, name in enumerate(names, start=1)])


def _game(db, game_id, home, away, *, day=0, home_score=10, away_score=8, status="final") -> NbaGame:
    start = T0 + timedelta(days=day)
    record = NbaGameRecord(
        source=SRC, source_game_id=game_id, season_start_year=2025, season_type="regular", game_date=start.date(), scheduled_start=start, status=status,
        home_source_team_id=home, away_source_team_id=away, home_team_name=home, away_team_name=away,
        home_score=home_score if status == "final" else None, away_score=away_score if status == "final" else None, source_status=f"RAW_{status.upper()}",
    )
    ingest_games(db, [record])
    return db.query(NbaGame).filter_by(source=SRC, source_game_id=game_id).one()


def _line(player_id, name, team, points, **kwargs) -> NbaPlayerBoxLine:
    return NbaPlayerBoxLine(source_player_id=player_id, player_name=name, position="G", source_team_id=team, did_not_play=False, started=True, minutes=30.0, points=points, **kwargs)


def _box(game_id, lines) -> NbaBoxScore:
    return NbaBoxScore(source=SRC, source_game_id=game_id, status="final", fetched_at=T0 + timedelta(days=30), lines=lines)


def test_a_traded_player_keeps_one_identity_and_the_right_team_per_game(db_session):
    _teams(db_session, "Alpha", "Bravo", "Charlie")
    before = _game(db_session, "g1", "t1", "t2", day=0)
    after = _game(db_session, "g2", "t3", "t1", day=20)

    ingest_box_score(db_session, before, _box("g1", [_line("p1", "Traded Player", "t1", 10), _line("p2", "Other", "t2", 8)]))
    ingest_box_score(db_session, after, _box("g2", [_line("p1", "Traded Player", "t3", 10), _line("p3", "Third", "t1", 8)]))

    player = db_session.query(NbaPlayer).filter_by(source=SRC, source_player_id="p1").one()
    logs = {log.game.source_game_id: log for log in db_session.query(NbaPlayerGameLog).filter_by(player_id=player.id)}
    assert (logs["g1"].team.name, logs["g1"].opponent_team.name, logs["g1"].is_home) == ("Alpha", "Bravo", True)
    assert (logs["g2"].team.name, logs["g2"].opponent_team.name, logs["g2"].is_home) == ("Charlie", "Alpha", True)
    assert player.current_team.name == "Charlie"

    # Re-ingesting the OLDER game must not move the player back to the old team.
    ingest_box_score(db_session, before, _box("g1", [_line("p1", "Traded Player", "t1", 10), _line("p2", "Other", "t2", 8)]))
    db_session.refresh(player)
    assert player.current_team.name == "Charlie" and db_session.query(NbaPlayer).count() == 3


def test_games_ingested_out_of_order_still_leave_the_latest_team(db_session):
    _teams(db_session, "Alpha", "Bravo", "Charlie")
    early, late = _game(db_session, "g1", "t1", "t2", day=0), _game(db_session, "g2", "t3", "t2", day=20)
    ingest_box_score(db_session, late, _box("g2", [_line("p1", "Player", "t3", 10), _line("p2", "Other", "t2", 8)]))
    ingest_box_score(db_session, early, _box("g1", [_line("p1", "Player", "t1", 10), _line("p2", "Other", "t2", 8)]))
    assert db_session.query(NbaPlayer).filter_by(source_player_id="p1").one().current_team.name == "Charlie"


def test_two_players_with_the_same_name_are_two_players(db_session):
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2")
    ingest_box_score(db_session, game, _box("g1", [_line("p1", "Same Name", "t1", 10), _line("p2", "Same Name", "t2", 8)]))
    players = db_session.query(NbaPlayer).filter_by(display_name="Same Name").all()
    assert {p.source_player_id for p in players} == {"p1", "p2"}
    assert {p.current_team.name for p in players} == {"Alpha", "Bravo"}


def test_a_renamed_player_stays_one_row_and_keeps_the_old_spelling_for_audit(db_session):
    _teams(db_session, "Alpha", "Bravo")
    first, second = _game(db_session, "g1", "t1", "t2", day=0), _game(db_session, "g2", "t1", "t2", day=5)
    ingest_box_score(db_session, first, _box("g1", [_line("p1", "Old Name", "t1", 10), _line("p2", "Other", "t2", 8)]))
    report = ingest_box_score(db_session, second, _box("g2", [_line("p1", "New Name III", "t1", 10), _line("p2", "Other", "t2", 8)]))

    player = db_session.query(NbaPlayer).filter_by(source_player_id="p1").one()
    assert report.players_renamed == 1 and report.players_created == 0
    assert player.display_name == "New Name III" and player.source_metadata == {"name_variants": ["Old Name"]}
    assert db_session.query(NbaPlayerGameLog).filter_by(player_id=player.id).count() == 2


def test_a_rebranded_team_is_the_same_team(db_session):
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2")
    report = ingest_teams(db_session, [NbaTeamRecord(source=SRC, source_team_id="t1", name="Alpha Reborn", abbreviation="ARB")])

    assert report.renamed == ["Alpha -> Alpha Reborn"] and report.created == 0
    assert db_session.query(NbaTeam).count() == 2
    db_session.refresh(game)
    assert game.home_team.name == "Alpha Reborn" and game.home_team.external_ids == {SRC: "t1"}


def test_a_second_provider_attaches_its_id_to_the_existing_team_by_exact_name_once(db_session):
    """A different source naming a team identically is the same franchise;
    after that, the provider id - not the name - is what identifies it."""
    _teams(db_session, "Alpha", "Bravo")
    ingest_teams(db_session, [NbaTeamRecord(source="other", source_team_id="x9", name="Alpha", abbreviation="ALP")])
    alpha = db_session.query(NbaTeam).filter_by(name="Alpha").one()
    assert alpha.external_ids == {SRC: "t1", "other": "x9"} and db_session.query(NbaTeam).count() == 2

    # A differently-spelled name from that source is NOT fuzzily matched to an existing team.
    report = ingest_teams(db_session, [NbaTeamRecord(source="other", source_team_id="x7", name="Bravo City", abbreviation="BRC")])
    assert report.created == 1 and db_session.query(NbaTeam).count() == 3


def test_duplicates_in_the_source_do_not_become_duplicate_rows(db_session):
    _teams(db_session, "Alpha", "Bravo")
    start = T0
    record = NbaGameRecord(
        source=SRC, source_game_id="g1", season_start_year=2025, season_type="regular", game_date=start.date(), scheduled_start=start, status="final",
        home_source_team_id="t1", away_source_team_id="t2", home_team_name="Alpha", away_team_name="Bravo", home_score=10, away_score=8,
    )
    report = ingest_games(db_session, [record, record])
    assert (report.created, report.unchanged) == (1, 1) and db_session.query(NbaGame).count() == 1

    game = db_session.query(NbaGame).one()
    box = _box("g1", [_line("p1", "Player", "t1", 10), _line("p2", "Other", "t2", 8), _line("p2", "Other", "t2", 8)])
    first = ingest_box_score(db_session, game, box)
    assert (first.games_ingested, first.lines_skipped_duplicate, first.logs_created) == (1, 1, 2)
    ingest_box_score(db_session, game, box)
    assert db_session.query(NbaPlayerGameLog).count() == 2 and db_session.query(NbaPlayer).count() == 2


def test_the_database_itself_refuses_duplicates(db_session):
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2")
    ingest_box_score(db_session, game, _box("g1", [_line("p1", "Player", "t1", 10), _line("p2", "Other", "t2", 8)]))
    player = db_session.query(NbaPlayer).filter_by(source_player_id="p1").one()

    duplicates = [
        NbaPlayer(display_name="Player", source=SRC, source_player_id="p1"),
        NbaGame(
            source=SRC, source_game_id="g1", season_start_year=2025, game_date=game.game_date, scheduled_start=game.scheduled_start,
            home_team_id=game.home_team_id, away_team_id=game.away_team_id,
        ),
        NbaPlayerGameLog(
            player_id=player.id, game_id=game.id, team_id=game.home_team_id, opponent_team_id=game.away_team_id, is_home=True, source=SRC, recorded_at=T0,
        ),
    ]
    for row in duplicates:
        db_session.add(row)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


def test_a_stat_correction_updates_the_log_in_place(db_session):
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2")
    ingest_box_score(db_session, game, _box("g1", [_line("p1", "Player", "t1", 10, assists=4), _line("p2", "Other", "t2", 8)]))
    report = ingest_box_score(db_session, game, _box("g1", [_line("p1", "Player", "t1", 10, assists=5), _line("p2", "Other", "t2", 8)]))
    assert (report.logs_created, report.logs_updated, report.logs_unchanged) == (0, 1, 1)
    assert db_session.query(NbaPlayerGameLog).filter_by(assists=5).count() == 1 and db_session.query(NbaPlayerGameLog).count() == 2


def test_points_that_do_not_add_up_reject_the_whole_box_score(db_session):
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2", home_score=10, away_score=8)
    report = ingest_box_score(db_session, game, _box("g1", [_line("p1", "Player", "t1", 9), _line("p2", "Other", "t2", 8)]))
    assert report.games_ingested == 0 and report.games_rejected == ["g1: team t1: player points 9 != score 10"]
    assert db_session.query(NbaPlayerGameLog).count() == 0 and game.box_score_state == NbaBoxScoreState.REJECTED.value


def test_a_player_row_with_no_id_is_left_out_and_the_rest_of_the_box_score_is_kept(db_session):
    """A player row with no athlete id cannot be stored. If its points are
    published the totals still reconcile, so everyone else's line is kept
    and the game is marked partial; if they are not (as in the real Chicago
    2025-26 cases, where the row was blank), the game is rejected."""
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2", home_score=10, away_score=8)
    box = replace(_box("g1", [_line("p1", "Player", "t1", 6), _line("p2", "Other", "t2", 8)]), malformed_rows=1, unattributed_points={"t1": 4})

    report = ingest_box_score(db_session, game, box)
    assert (report.games_ingested, report.games_ingested_partial, report.lines_malformed, report.games_rejected) == (1, 1, 1, [])
    assert game.box_score_state == NbaBoxScoreState.INGESTED_PARTIAL.value
    assert db_session.query(NbaPlayerGameLog).count() == 2

    # Without the unidentified row's points the totals would not reconcile, and it is rejected.
    other = _game(db_session, "g2", "t1", "t2", day=3, home_score=10, away_score=8)
    short = replace(_box("g2", [_line("p1", "Player", "t1", 6), _line("p2", "Other", "t2", 8)]), malformed_rows=1, unattributed_points={})
    assert ingest_box_score(db_session, other, short).games_rejected == ["g2: team t1: player points 6 != score 10"]


def test_a_postponed_game_and_its_replay_under_a_new_id_do_not_collide(db_session):
    """What ESPN actually does (verified in the backfill: Oklahoma City at
    Houston, postponed 2020-12-23 as event 401267176, replayed 2021-03-21 as
    event 401307433): the replay is a NEW event id and the postponed row
    stays as it was. The postponed row never gets a box score and is not
    part of the modelling dataset; the replay is an ordinary final game."""
    _teams(db_session, "Alpha", "Bravo")
    postponed = _game(db_session, "old-id", "t1", "t2", status="postponed")
    replay = _game(db_session, "new-id", "t1", "t2", day=40, status="final")
    assert postponed.id != replay.id and db_session.query(NbaGame).count() == 2

    requests: list = []

    class Records:
        def get_box_score(self, source_game_id):
            requests.append(source_game_id)
            return _box(source_game_id, [_line("p1", "Player", "t1", 10), _line("p2", "Other", "t2", 8)])

    sync_box_scores(db_session, Records(), T0.date(), T0.date() + timedelta(days=60), source=SRC)
    assert requests == ["new-id"]
    assert (postponed.status, postponed.box_score_state, postponed.home_score) == ("postponed", None, None)
    assert db_session.query(NbaPlayerGameLog).filter_by(game_id=postponed.id).count() == 0


def test_a_postponed_game_keeps_its_identity_when_it_is_rescheduled_and_played(db_session):
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2", status="postponed")
    assert (game.status, game.source_status, game.home_score) == ("postponed", "RAW_POSTPONED", None)

    # No box score is ever fetched for a game that is not final.
    class NeverCalled:
        def get_box_score(self, source_game_id):
            raise AssertionError("a postponed game's box score must not be requested")

    assert sync_box_scores(db_session, NeverCalled(), T0.date(), T0.date() + timedelta(days=30), source=SRC).games_seen == 0

    # The provider later lists the SAME game id on a new date, played.
    replayed = _game(db_session, "g1", "t1", "t2", day=12, status="final")
    assert replayed.id == game.id and db_session.query(NbaGame).count() == 1
    assert (replayed.status, replayed.game_date, replayed.home_score) == ("final", (T0 + timedelta(days=12)).date(), 10)


def test_a_game_between_unknown_teams_is_skipped_and_reported(db_session):
    _teams(db_session, "Alpha", "Bravo")
    record = NbaGameRecord(
        source=SRC, source_game_id="g9", season_start_year=2025, season_type="regular", game_date=T0.date(), scheduled_start=T0, status="final",
        home_source_team_id="t1", away_source_team_id="unknown", home_team_name="Alpha", away_team_name="Touring Side", home_score=10, away_score=8,
    )
    report = ingest_games(db_session, [record])
    assert report.created == 0 and report.skipped_unknown_team == ["g9: Touring Side @ Alpha"]


def test_an_unmapped_injury_status_is_stored_raw_with_no_canonical_value(db_session):
    """ESPN's "Day-To-Day" has no safe canonical meaning; it is kept exactly
    as published and the canonical status is left empty rather than guessed."""
    _teams(db_session, "Alpha", "Bravo")
    game = _game(db_session, "g1", "t1", "t2")
    ingest_box_score(db_session, game, _box("g1", [_line("p1", "Player", "t1", 10), _line("p2", "Other", "t2", 8)]))
    player = db_session.query(NbaPlayer).filter_by(source_player_id="p1").one()

    db_session.add(NbaPlayerAvailabilityReport(player_id=player.id, team_id=player.current_team_id, source_status="Day-To-Day", status=None, source=SRC, observed_at=T0))
    db_session.commit()
    stored = db_session.query(NbaPlayerAvailabilityReport).one()
    assert (stored.source_status, stored.status) == ("Day-To-Day", None)

