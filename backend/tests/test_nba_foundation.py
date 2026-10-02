"""NBA foundation boundaries: the market vocabulary, the status API, and the
guarantees that keep NBA and AFL from bleeding into each other."""

import ast
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.database import Base
from app.models.nba import NbaPlayerGameLog
from app.nba.markets import NbaPropMarket, actual_stat_value, market_for_odds_api_key
from app.nba.projection import StatDistribution, selection_probability
from app.providers.the_odds_api import NBA_SPORT_KEY, TheOddsApiProvider
from app.providers.types import ProviderEvent
from tests.nba_helpers import POINTS_DISTRIBUTION, TIPOFF, add_quote, seed_bookmaker, seed_game, seed_player

APP_DIR = Path(__file__).resolve().parents[1] / "app"


# --- markets and the model contract ----------------------------------------


def test_markets_settle_on_their_own_box_score_stat():
    log = NbaPlayerGameLog(points=27, rebounds=9, assists=None)
    assert actual_stat_value(log, NbaPropMarket.POINTS.value) == 27.0
    assert actual_stat_value(log, NbaPropMarket.REBOUNDS.value) == 9.0
    assert actual_stat_value(log, NbaPropMarket.ASSISTS.value) is None
    assert actual_stat_value(log, "player_disposals") is None


def test_odds_api_market_keys_map_main_and_alternate_lines():
    assert market_for_odds_api_key("player_points") == (NbaPropMarket.POINTS, False)
    assert market_for_odds_api_key("player_assists_alternate") == (NbaPropMarket.ASSISTS, True)
    # Combination props are deliberately not covered.
    assert market_for_odds_api_key("player_points_rebounds_assists") is None


def test_selection_probability_leaves_room_for_a_push():
    assert isinstance(POINTS_DISTRIBUTION, StatDistribution)
    over, under = selection_probability(POINTS_DISTRIBUTION, 25, "over"), selection_probability(POINTS_DISTRIBUTION, 25, "under")
    assert over == pytest.approx(0.4) and under == pytest.approx(0.4)
    with pytest.raises(ValueError):
        selection_probability(POINTS_DISTRIBUTION, 25, "yes")


# --- the shared odds client serves NBA -------------------------------------


def _provider(handler, **kwargs) -> TheOddsApiProvider:
    client = httpx.Client(base_url="https://api.the-odds-api.com/v4", transport=httpx.MockTransport(handler))
    return TheOddsApiProvider(api_key="key", client=client, **kwargs)


def _event() -> ProviderEvent:
    return ProviderEvent(provider="the_odds_api", event_id="evt1", sport_key=NBA_SPORT_KEY, home_team="Home Team", away_team="Away Team", commence_time=TIPOFF)


def test_odds_client_requests_the_nba_sport_key_and_us_region_by_default():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["regions"], seen["markets"] = request.url.path, request.url.params["regions"], request.url.params["markets"]
        outcome = {"name": "Over", "description": "Test Player", "price": 1.91, "point": 24.5}
        market = {"key": "player_points", "last_update": "2026-11-02T20:00:00Z", "outcomes": [outcome]}
        return httpx.Response(200, json={"bookmakers": [{"key": "book_a", "title": "Book A", "markets": [market]}]})

    result = _provider(handler).get_player_prop_quotes("NBA", _event(), ["player_points", "player_rebounds"])

    assert seen == {"path": f"/v4/sports/{NBA_SPORT_KEY}/events/evt1/odds", "regions": "us", "markets": "player_points,player_rebounds"}
    assert [(q.sport_code, q.market_key, q.player_name, q.threshold, q.price_decimal) for q in result.quotes] == [("NBA", "player_points", "Test Player", 24.5, 1.91)]


def test_odds_client_regions_can_be_overridden():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["regions"] = request.url.params["regions"]
        return httpx.Response(200, json={"bookmakers": []})

    _provider(handler, regions="us,au").get_player_prop_quotes("NBA", _event(), ["player_points"])
    assert seen["regions"] == "us,au"


def test_odds_client_lists_nba_events_and_still_rejects_unknown_sports():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v4/sports/{NBA_SPORT_KEY}/events"
        return httpx.Response(200, json=[])

    provider = _provider(handler)
    assert provider.list_events("NBA") == []
    with pytest.raises(ValueError):
        provider.list_events("NRL")


# --- status API ------------------------------------------------------------


def test_status_reports_an_empty_foundation_honestly(client):
    body = client.get("/api/nba/status").json()
    assert body["sport"] == "NBA"
    assert body["markets"] == ["player_points", "player_rebounds", "player_assists"]
    assert {d["key"] for d in body["datasets"]} == {
        "teams", "players", "games", "player_game_logs", "availability_reports", "team_observations", "game_lineup_observations",
        "game_schedule_observations", "prop_quotes", "projections", "predictions",
    }
    assert all(d["rows"] == 0 and d["latest_at"] is None for d in body["datasets"])
    assert (body["predictions_frozen"], body["predictions_with_closing_line"], body["predictions_settled"]) == (0, 0, 0)


def test_status_counts_real_rows(client, db_session):
    game, home, _ = seed_game(db_session)
    player = seed_player(db_session, home)
    add_quote(db_session, game=game, player=player, bookmaker=seed_bookmaker(db_session), observed_at=TIPOFF - timedelta(hours=2), price=1.9)
    db_session.commit()

    datasets = {d["key"]: d for d in client.get("/api/nba/status").json()["datasets"]}
    assert (datasets["teams"]["rows"], datasets["players"]["rows"], datasets["games"]["rows"], datasets["prop_quotes"]["rows"]) == (2, 1, 1, 1)
    assert datasets["prop_quotes"]["latest_at"].startswith("2026-11-02T22:30:00")
    assert datasets["predictions"]["rows"] == 0


# --- isolation between the sports ------------------------------------------


def test_nba_rows_never_appear_in_afl_surfaces(client, db_session):
    game, home, _ = seed_game(db_session)
    seed_player(db_session, home)
    db_session.commit()

    assert client.get("/api/afl/teams").json() == []
    assert client.get("/api/matches").json() == []
    assert client.get("/api/afl/matches/upcoming").json() == []
    assert client.get("/api/dashboard").json() == []


def test_nba_tables_reference_only_nba_tables_and_bookmakers():
    """The only table NBA shares with AFL is `bookmakers`. An NBA foreign
    key into matches/players/teams would re-create the coupling the separate
    tables exist to avoid."""
    for table in Base.metadata.tables.values():
        if not table.name.startswith("nba_"):
            assert not any(fk.column.table.name.startswith("nba_") for fk in table.foreign_keys), f"AFL table {table.name} references an NBA table"
            continue
        for fk in table.foreign_keys:
            target = fk.column.table.name
            assert target.startswith("nba_") or target == "bookmakers", f"{table.name} references {target}"


def _imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


AFL_ONLY_PACKAGES = ("app.player_modelling", "app.modelling", "app.pricing", "app.market_monitor", "app.trading_monitor", "app.ingestion", "app.providers.afl")


@pytest.mark.parametrize("package", ["nba", "models/nba", "core", "providers/nba"])
def test_nba_and_core_code_do_not_import_afl_packages(package):
    for path in (APP_DIR / package).rglob("*.py"):
        offending = {m for m in _imported_modules(path) if m.startswith(AFL_ONLY_PACKAGES)}
        assert not offending, f"{path.relative_to(APP_DIR)} imports AFL-specific code: {sorted(offending)}"


def test_core_does_not_import_any_sport():
    for path in (APP_DIR / "core").rglob("*.py"):
        offending = {m for m in _imported_modules(path) if m.startswith(("app.nba", "app.models"))}
        assert not offending, f"{path.relative_to(APP_DIR)} imports sport-specific code: {sorted(offending)}"
