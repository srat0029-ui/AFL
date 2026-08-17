"""Tests for PropMarketObservation creation (Sections 1-4, 23 of the
market-logging stage brief): frozen quote+model snapshot pairing,
idempotency, model-change-creates-new-observation, and the skip cases that
keep the "real logged market observations" dataset honest (manual quotes,
complementary sides, no projection yet, confirmed-out players)."""

from datetime import datetime, timedelta, timezone

from app.models import (
    Bookmaker,
    ExpectedLineup,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerPropMarket,
    PropMarketObservation,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.prop_observation import (
    ObservationCreationReport,
    create_observation_for_quote,
    create_observations_for_match,
)

NOW = datetime.now(timezone.utc)


def _seed(db, *, threshold=29.5):
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
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db.add_all([player, bookmaker])
    db.flush()
    projection = PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id,
        model_name="disposals_nb", model_version="v1", generated_at=NOW, data_cutoff=NOW,
        lineup_status_at_generation="uncertain", games_of_history=20,
        predicted_mean=28.0, distribution_method="nb", nb_alpha=8.0, confidence_tier="moderate_confidence",
    )
    db.add(projection)
    db.flush()
    quote = PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=threshold, selection="over", price_decimal=1.9,
        recorded_at=NOW, source="the_odds_api", bookmaker_last_update=NOW,
    )
    db.add(quote)
    # Confirmed-selected by default so the base fixture reflects a player
    # who's a lock, not the confidence-downgrade path — tests that need the
    # downgrade path add their own ExpectedLineup row explicitly.
    db.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in",
        selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual",
    ))
    db.commit()
    return match, home, away, player, bookmaker, projection, quote


def test_observation_freezes_quote_and_model_snapshot(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)

    obs = create_observation_for_quote(db_session, quote, ObservationCreationReport())
    db_session.commit()

    assert obs is not None
    assert obs.quote_id == quote.id
    assert obs.offered_odds == 1.9
    assert obs.model_name == "disposals_nb"
    assert obs.model_version == "v1"
    assert obs.predicted_mean == 28.0
    assert obs.confidence_tier == "moderate_confidence"
    assert obs.threshold == 29.5
    assert obs.market_type == "player_disposals"

    # Now mutate the projection in place (as refresh-live does) and confirm
    # the ALREADY-CREATED observation is untouched - it must stay frozen at
    # what the model believed at observation time, never recomputed later.
    projection.predicted_mean = 40.0
    projection.confidence_tier = "higher_confidence"
    db_session.commit()

    reloaded = db_session.get(PropMarketObservation, obs.id)
    assert reloaded.predicted_mean == 28.0
    assert reloaded.confidence_tier == "moderate_confidence"


def test_unchanged_quote_and_model_creates_no_duplicate(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    report = ObservationCreationReport()

    create_observation_for_quote(db_session, quote, report)
    db_session.commit()
    create_observation_for_quote(db_session, quote, report)
    db_session.commit()

    assert report.observations_created == 1
    assert report.observations_unchanged == 1
    assert db_session.query(PropMarketObservation).count() == 1


def test_model_version_change_creates_new_observation_for_same_quote(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    report = ObservationCreationReport()

    create_observation_for_quote(db_session, quote, report)
    db_session.commit()

    projection.model_version = "v2"
    projection.predicted_mean = 32.0
    db_session.commit()

    create_observation_for_quote(db_session, quote, report)
    db_session.commit()

    assert report.observations_created == 2
    rows = db_session.query(PropMarketObservation).filter_by(quote_id=quote.id).order_by(PropMarketObservation.id).all()
    assert len(rows) == 2
    assert rows[0].model_version == "v1" and rows[0].predicted_mean == 28.0
    assert rows[1].model_version == "v2" and rows[1].predicted_mean == 32.0


def test_data_cutoff_change_creates_new_observation_for_same_quote(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    report = ObservationCreationReport()

    create_observation_for_quote(db_session, quote, report)
    db_session.commit()

    projection.data_cutoff = NOW + timedelta(hours=6)
    db_session.commit()

    create_observation_for_quote(db_session, quote, report)
    db_session.commit()

    assert report.observations_created == 2


def test_manual_quote_skipped(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    quote.source = "manual"
    db_session.commit()

    report = ObservationCreationReport()
    obs = create_observation_for_quote(db_session, quote, report)

    assert obs is None
    assert report.skipped_manual_source == 1
    assert db_session.query(PropMarketObservation).count() == 0


def test_complementary_side_skipped(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    quote.selection = "under"
    db_session.commit()

    report = ObservationCreationReport()
    obs = create_observation_for_quote(db_session, quote, report)

    assert obs is None
    assert report.skipped_complementary_side == 1


def test_no_projection_yet_skipped(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="No Projection Yet", source="afltables", source_player_id="p2", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    quote = PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=20.5, selection="over", price_decimal=1.9, recorded_at=NOW, source="the_odds_api",
    )
    db_session.add(quote)
    db_session.commit()

    report = ObservationCreationReport()
    obs = create_observation_for_quote(db_session, quote, report)

    assert obs is None
    assert report.skipped_no_projection == 1


def test_confirmed_out_player_skipped(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    lineup = db_session.query(ExpectedLineup).filter_by(match_id=match.id, player_id=player.id).one()
    lineup.status = "expected_out"
    lineup.selection_status = "confirmed_out"
    db_session.commit()

    report = ObservationCreationReport()
    obs = create_observation_for_quote(db_session, quote, report)

    assert obs is None
    assert report.skipped_confirmed_out == 1


def test_uncertain_participation_downgrades_confidence(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    lineup = db_session.query(ExpectedLineup).filter_by(match_id=match.id, player_id=player.id).one()
    lineup.status = "uncertain"
    lineup.selection_status = "placeholder"
    lineup.is_confirmed = False
    db_session.commit()

    obs = create_observation_for_quote(db_session, quote, ObservationCreationReport())
    db_session.commit()

    assert obs.confidence_tier == "lower_confidence"  # downgraded one tier from moderate_confidence
    assert obs.selection_status_at_observation == "placeholder"
    assert obs.is_confirmed_at_observation is False


def test_arbitrary_threshold_supported(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session, threshold=17.5)

    obs = create_observation_for_quote(db_session, quote, ObservationCreationReport())
    db_session.commit()

    assert obs.threshold == 17.5
    assert 0.0 < obs.model_probability < 1.0


def test_create_observations_for_match_processes_every_quote(db_session):
    match, home, away, player, bookmaker, projection, quote = _seed(db_session)
    second_quote = PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=31.5, selection="over", price_decimal=2.1, recorded_at=NOW, source="the_odds_api",
    )
    db_session.add(second_quote)
    db_session.commit()

    report = create_observations_for_match(db_session, match.id)

    assert report.quotes_considered == 2
    assert report.observations_created == 2
    assert db_session.query(PropMarketObservation).count() == 2
