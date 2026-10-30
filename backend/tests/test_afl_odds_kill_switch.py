"""AFL_ODDS_API_ENABLED: with it off (the default), no AFL request reaches The
Odds API from any entry point - the frontend Refresh button
(POST /api/afl/refresh), the live cycle, the CLI odds commands or the
scheduler - even with a key configured. NBA requests are not switched off,
and the key itself is untouched.

Every test runs behind a network sentinel: all outgoing httpx requests are
recorded and refused (no test here can reach the internet), so "zero
requests to api.the-odds-api.com" is measured, not assumed."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import app.player_modelling.cli as cli_module
import app.player_modelling.live_cycle as live_cycle_module
import app.player_modelling.scheduler as scheduler_module
from app.config import get_settings
from app.models import Match, MatchStatus, Round, Season, Sport, Team
from app.player_modelling.live_cycle import AFL_ODDS_DISABLED_MESSAGE, run_live_cycle
from app.providers.the_odds_api import OddsApiSportDisabledError, TheOddsApiProvider, odds_api_enabled_for
from app.providers.types import ProviderEvent

ODDS_HOST = "api.the-odds-api.com"
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "live-cycle.yml"


@pytest.fixture()
def sentinel(monkeypatch):
    """Records every outgoing httpx request and refuses it. The in-process
    TestClient (itself an httpx.Client, host "testserver") passes through."""
    sent: list[httpx.Request] = []
    original = httpx.Client.send

    def send(self, request, *args, **kwargs):
        if request.url.host == "testserver":
            return original(self, request, *args, **kwargs)
        sent.append(request)
        raise httpx.ConnectError("network disabled in tests", request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return sent


def _odds_requests(sent):
    return [r for r in sent if r.url.host == ODDS_HOST]


@pytest.fixture()
def key_configured(monkeypatch):
    """A key is set and AFL_ODDS_API_ENABLED is NOT - the case the switch exists for."""
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key-not-real")
    monkeypatch.delenv("AFL_ODDS_API_ENABLED", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _NoFixtures:
    def get_upcoming_fixtures(self, sport_code):
        return []


class _NoPlayerStats:
    def get_team_season_player_stats(self, sport_code, season_year, team_name):
        return []


@pytest.fixture()
def offline_free_sources(monkeypatch):
    """Squiggle and AFL Tables fetch through curl, outside the httpx
    sentinel; stub them so the cycle stays offline. The Odds API provider is
    left REAL - it is what these tests measure."""
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: _NoFixtures())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: _NoPlayerStats())


def _seed_upcoming_afl_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    db.add(Match(sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
                 scheduled_start=datetime.now(timezone.utc) + timedelta(days=2), status=MatchStatus.SCHEDULED))
    db.commit()


EVENT = ProviderEvent(provider="the_odds_api", event_id="e1", sport_key="aussierules_afl", home_team="A", away_team="B", commence_time=datetime.now(timezone.utc))


def test_off_by_default(key_configured):
    assert get_settings().afl_odds_api_enabled is False
    assert odds_api_enabled_for("AFL") is False
    assert odds_api_enabled_for("NBA") is True
    assert get_settings().the_odds_api_key == "test-key-not-real"  # the key is kept


def test_provider_refuses_every_afl_request_before_sending(key_configured, sentinel):
    provider = TheOddsApiProvider(api_key=get_settings().the_odds_api_key)
    assert provider.is_available
    with pytest.raises(OddsApiSportDisabledError):
        provider.list_events("AFL")  # free endpoint - still not called
    with pytest.raises(OddsApiSportDisabledError):
        provider.get_player_prop_quotes("AFL", EVENT, ["player_disposals"])
    with pytest.raises(OddsApiSportDisabledError):
        provider.get_standard_match_odds("AFL")
    assert _odds_requests(sentinel) == []


def test_nba_requests_are_not_switched_off(key_configured, sentinel):
    provider = TheOddsApiProvider(api_key=get_settings().the_odds_api_key)
    with pytest.raises(Exception) as exc:
        provider.list_events("NBA")
    assert not isinstance(exc.value, OddsApiSportDisabledError)
    (request,) = _odds_requests(sentinel)  # attempted (and refused by the sentinel)
    assert "basketball_nba" in request.url.path


def test_enabled_afl_requests_do_reach_the_provider(key_configured, sentinel, monkeypatch):
    """Control: the sentinel does see AFL requests once the switch is on."""
    monkeypatch.setenv("AFL_ODDS_API_ENABLED", "true")
    get_settings.cache_clear()
    provider = TheOddsApiProvider(api_key=get_settings().the_odds_api_key)
    with pytest.raises(Exception):
        provider.list_events("AFL")
    assert len(_odds_requests(sentinel)) == 1


def test_live_cycle_sends_no_afl_odds_request(db_session, key_configured, sentinel, offline_free_sources):
    _seed_upcoming_afl_match(db_session)
    run = run_live_cycle(db_session)
    steps = {s["step"]: s for s in run.steps}
    for name in ("refresh_prop_odds", "refresh_team_odds"):
        assert steps[name]["detail"] == AFL_ODDS_DISABLED_MESSAGE
    assert _odds_requests(sentinel) == []


def test_refresh_button_endpoint_sends_no_afl_odds_request(client, db_session, key_configured, sentinel, offline_free_sources):
    _seed_upcoming_afl_match(db_session)
    response = client.post("/api/afl/refresh")
    assert response.status_code == 200
    steps = {s["step"]: s for s in response.json()["steps"]}
    assert steps["refresh_prop_odds"]["detail"] == AFL_ODDS_DISABLED_MESSAGE
    assert steps["refresh_team_odds"]["detail"] == AFL_ODDS_DISABLED_MESSAGE
    assert _odds_requests(sentinel) == []


class _Db:
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


@pytest.mark.parametrize("command", [["refresh-prop-odds"], ["refresh-prop-odds", "--force"], ["refresh-team-odds"]])
def test_cli_odds_commands_send_nothing(db_session, key_configured, sentinel, monkeypatch, capsys, command):
    _seed_upcoming_afl_match(db_session)
    monkeypatch.setattr(cli_module, "SessionLocal", lambda: _Db(db_session))
    assert cli_module.main(command) == 0
    assert "AFL odds fetching is disabled" in capsys.readouterr().out
    assert _odds_requests(sentinel) == []


def test_scheduler_sends_no_afl_odds_request(db_session, key_configured, sentinel, offline_free_sources, monkeypatch, tmp_path):
    _seed_upcoming_afl_match(db_session)
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: _Db(db_session))
    scheduler_module.run_scheduler_loop(interval_minutes=60, max_iterations=1, lock_path=tmp_path / "lock", pause_path=tmp_path / "pause", log_path=tmp_path / "log")
    assert _odds_requests(sentinel) == []
    assert live_cycle_module.AFL_ODDS_DISABLED_MESSAGE  # same switch the live cycle reports


def test_live_cycle_workflow_withholds_the_key_unless_enabled():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "THE_ODDS_API_KEY: ${{ vars.AFL_ODDS_API_ENABLED == 'true' && secrets.THE_ODDS_API_KEY || '' }}" in wf
    assert "AFL_ODDS_API_ENABLED: ${{ vars.AFL_ODDS_API_ENABLED == 'true' && 'true' || 'false' }}" in wf
    assert "-e AFL_ODDS_API_ENABLED" in wf
    assert "THE_ODDS_API_KEY: ${{ secrets.THE_ODDS_API_KEY }}" not in wf
