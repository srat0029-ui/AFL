"""Regression tests against a REAL saved provider response (Section 17 of
the second automated-odds audit brief) — captured from a real, live The
Odds API event-odds request for AFL round 24 2026 (Collingwood v Brisbane
Lions), sanitized of nothing sensitive (the response body never contained
the API key — see tests/fixtures/the_odds_api_real_event_odds_response.json's
provenance note below) and used here so parser/mapping/resolver fixes are
verified against real-world data shape without spending API credits.

Two real, previously-unknown things this fixture proved and this test
locks in:
  1. This bookmaker returns the ALTERNATE market key
     ("player_disposals_over") for AFL disposals, not the paired
     ("player_disposals") key the docs describe as the "standard" one -
     and returns ONLY the "Over" side, never "Under", for every threshold.
     Devigging is therefore never triggered on this real data; the raw
     implied probability path must be exercised correctly.
  2. Real bookmaker player-name text includes full given names ("Cameron
     Rayner", "Lachlan Schultz") where this project's own records use the
     commonly-published nickname ("Cam Rayner", "Lachie Schultz") - see
     the nickname resolution tier added to prop_player_resolution.py as a
     direct result of this real fixture.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as dtparser

from app.models import Match, MatchStatus, Player, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.prop_odds_ingestion import PropOddsRefreshReport, _ingest_one_quote
from app.player_modelling.prop_player_resolution import RESOLUTION_EXACT, RESOLUTION_SAFELY_RESOLVED, resolve_prop_player
from app.providers.types import PlayerPropQuote

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "the_odds_api_real_event_odds_response.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _seed_collingwood_v_brisbane(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=24)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Brisbane Lions", short_name="BRI")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 21, 9, 40, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()

    # Only the two real players this fixture-based regression cares about -
    # a full 44-player roster isn't needed to prove the parsing/resolution
    # behavior itself.
    daicos = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    rayner = Player(sport_id=sport.id, display_name="Cam Rayner", source="afltables", source_player_id="p2", current_team_id=away.id)
    schultz = Player(sport_id=sport.id, display_name="Lachie Schultz", source="afltables", source_player_id="p3", current_team_id=home.id)
    db.add_all([daicos, rayner, schultz])
    db.commit()
    return match, home, away, {"Nick Daicos": daicos, "Cam Rayner": rayner, "Lachie Schultz": schultz}


def test_fixture_loads_and_has_expected_real_shape():
    body = _load_fixture()
    assert body["home_team"] == "Collingwood Magpies"
    assert body["away_team"] == "Brisbane Lions"
    assert len(body["bookmakers"]) == 1
    assert body["bookmakers"][0]["key"] == "sportsbet"


def test_fixture_market_key_is_the_alternate_over_only_key_not_the_paired_one():
    """Real, previously-undocumented-by-experience finding: verify it's
    still true of the saved fixture, so a future edit to the fixture (or a
    naive "let's just regenerate it") doesn't silently drop this case."""
    body = _load_fixture()
    market_keys = {mk["key"] for bm in body["bookmakers"] for mk in bm["markets"]}
    assert market_keys == {"player_disposals_over"}
    all_selections = {o["name"] for bm in body["bookmakers"] for mk in bm["markets"] for o in mk["outcomes"]}
    assert all_selections == {"Over"}  # never "Under" - devig cannot trigger on this real data


def test_fixture_contains_no_api_key_or_auth_material():
    raw = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "apiKey" not in raw
    assert "Authorization" not in raw
    assert "Bearer" not in raw


def test_full_name_resolves_to_our_nickname_record(db_session):
    """Real bug found via this fixture: "Cameron Rayner" (provider) must
    resolve to our "Cam Rayner" record via the nickname tier, not fail."""
    match, home, away, players = _seed_collingwood_v_brisbane(db_session)
    resolution = resolve_prop_player(db_session, match, "Cameron Rayner")
    assert resolution.tier == RESOLUTION_SAFELY_RESOLVED
    assert resolution.player.id == players["Cam Rayner"].id


def test_lachlan_resolves_to_lachie(db_session):
    match, home, away, players = _seed_collingwood_v_brisbane(db_session)
    resolution = resolve_prop_player(db_session, match, "Lachlan Schultz")
    assert resolution.tier == RESOLUTION_SAFELY_RESOLVED
    assert resolution.player.id == players["Lachie Schultz"].id


def test_reprocessing_real_fixture_end_to_end_persists_expected_quotes(db_session):
    """Feeds every real outcome for Nick Daicos through the actual
    ingestion path (_ingest_one_quote) exactly as refresh-prop-odds would,
    proving the full real response shape -> normalize -> resolve ->
    persist pipeline works end to end against real data, with zero API
    calls (Section 4's stated purpose for this fixture)."""
    match, home, away, players = _seed_collingwood_v_brisbane(db_session)
    body = _load_fixture()
    fetched_at = datetime.now(timezone.utc)
    report = PropOddsRefreshReport()

    daicos_outcomes = [
        o for bm in body["bookmakers"] for mk in bm["markets"] for o in mk["outcomes"] if o.get("description") == "Nick Daicos"
    ]
    assert len(daicos_outcomes) > 0
    market_last_update = dtparser.isoparse(body["bookmakers"][0]["markets"][0]["last_update"])

    for outcome in daicos_outcomes:
        quote = PlayerPropQuote(
            provider="the_odds_api", event_id=body["id"], sport_code="AFL", bookmaker_key="sportsbet",
            bookmaker_title="SportsBet", bookmaker_region="au", market_key="player_disposals_over",
            player_name="Nick Daicos", selection=outcome["name"], price_decimal=float(outcome["price"]),
            bookmaker_last_update=market_last_update, fetched_at=fetched_at, threshold=outcome.get("point"), raw_outcome=outcome,
        )
        _ingest_one_quote(db_session, match, quote, report)
    db_session.commit()

    assert report.quotes_created == len(daicos_outcomes)
    assert report.unresolved_players == []
    assert report.ambiguous_players == []

    from sqlalchemy import select

    rows = db_session.scalars(select(PlayerPropMarket).where(PlayerPropMarket.player_id == players["Nick Daicos"].id)).all()
    assert len(rows) == len(daicos_outcomes)
    assert all(r.selection == "over" for r in rows)
    assert all(r.provider_market_key == "player_disposals_over" for r in rows)
    assert all(r.source == "the_odds_api" for r in rows)
