"""Tests for the Market Movement Explorer composition layer
(app/player_modelling/market_movement_series.py) — chronological ordering,
pre/post-kickoff splitting, first/latest-observed selection, sparse/no
history, multi-bookmaker consensus (and when it must NOT be fabricated),
model-vs-bookmaker timestamp independence, and lineup-status-change
detection."""

from datetime import datetime, timedelta, timezone

from app.models import (
    Bookmaker,
    KIND_PROBABILITY,
    KIND_PROJECTED_MEAN,
    Match,
    MatchStatus,
    ModelValueObservation,
    OddsQuote,
    Player,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
    VALUE_PLAYER_DISPOSAL_PROBABILITY,
    VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN,
    VALUE_TEAM_WIN_PROBABILITY,
)
from app.models.bookmaker import ELIGIBILITY_EXCLUDED
from app.player_modelling.market_movement_series import (
    list_market_options,
    list_matches_with_history,
    load_player_market_series,
    load_team_market_series,
)

KICKOFF = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _seed_match(db, *, scheduled_start=KICKOFF, status=MatchStatus.SCHEDULED):
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
        scheduled_start=scheduled_start, status=status,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.commit()
    return match, home, away, player


def _quote(match, bookmaker, *, selection, price, recorded_at, line_value=None, market_type="h2h", source="manual", is_closing_line=False):
    return OddsQuote(
        match_id=match.id, bookmaker_id=bookmaker.id, market_type=market_type, selection=selection, line_value=line_value,
        price_decimal=price, recorded_at=recorded_at, source=source, is_closing_line=is_closing_line,
    )


def _team_obs(match, *, selection, value, recorded_at, value_type=VALUE_TEAM_WIN_PROBABILITY, value_kind=KIND_PROBABILITY):
    return ModelValueObservation(
        match_id=match.id, player_id=None, value_type=value_type, value_kind=value_kind, selection=selection,
        threshold=None, value=value, lineup_status=None, model_name="team_elo", model_version="v1",
        data_cutoff=recorded_at, recorded_at=recorded_at,
    )


class TestTeamSeriesChronologyAndSplitting:
    def test_chronological_ordering_independent_of_insertion_order(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        t1, t2, t3 = KICKOFF - timedelta(hours=48), KICKOFF - timedelta(hours=24), KICKOFF - timedelta(hours=6)
        # Inserted out of chronological order deliberately.
        db_session.add(_quote(match, tab, selection=home.name, price=2.10, recorded_at=t3))
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=t1))
        db_session.add(_quote(match, tab, selection=home.name, price=2.00, recorded_at=t2))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        recorded = [q.recorded_at.replace(tzinfo=timezone.utc) if q.recorded_at.tzinfo is None else q.recorded_at for q in series.bookmaker_quotes]
        assert recorded == sorted(recorded)
        assert series.bookmaker_quotes[0].price_decimal == 1.90
        assert series.bookmaker_quotes[-1].price_decimal == 2.10

    def test_post_kickoff_quotes_excluded_from_pre_kickoff_series_and_summary(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        pre = KICKOFF - timedelta(hours=2)
        post = KICKOFF + timedelta(minutes=5)
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=pre))
        db_session.add(_quote(match, tab, selection=home.name, price=1.70, recorded_at=post))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        assert len(series.bookmaker_quotes) == 1
        assert series.bookmaker_quotes[0].price_decimal == 1.90
        assert len(series.bookmaker_quotes_post_kickoff) == 1
        assert series.bookmaker_quotes_post_kickoff[0].price_decimal == 1.70
        # The post-kickoff row must never leak into the pre-match summary.
        assert series.bookmaker_summary.n_observations == 1
        assert series.bookmaker_summary.latest_observed_pre_kickoff.price_decimal == 1.90

    def test_first_and_latest_observed_selection(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        db_session.add(_quote(match, tab, selection=home.name, price=1.80, recorded_at=KICKOFF - timedelta(days=3)))
        db_session.add(_quote(match, tab, selection=home.name, price=1.95, recorded_at=KICKOFF - timedelta(hours=1)))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        assert series.bookmaker_summary.first_observed.price_decimal == 1.80
        assert series.bookmaker_summary.latest_observed_pre_kickoff.price_decimal == 1.95
        assert series.bookmaker_summary.total_probability_change == series.bookmaker_summary.latest_observed_pre_kickoff.probability - series.bookmaker_summary.first_observed.probability

    def test_largest_single_movement_is_the_biggest_consecutive_jump_not_first_vs_latest(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        # Probabilities: 0.500 -> 0.526 -> 0.909 -> 0.870 - the big jump is
        # the middle one, even though first-vs-latest is a smaller net move.
        db_session.add(_quote(match, tab, selection=home.name, price=2.00, recorded_at=KICKOFF - timedelta(hours=48)))
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=KICKOFF - timedelta(hours=36)))
        db_session.add(_quote(match, tab, selection=home.name, price=1.10, recorded_at=KICKOFF - timedelta(hours=24)))
        db_session.add(_quote(match, tab, selection=home.name, price=1.15, recorded_at=KICKOFF - timedelta(hours=12)))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        largest = series.bookmaker_summary.largest_single_movement
        assert largest is not None
        assert largest.from_probability == 1 / 1.90
        assert largest.to_probability == 1 / 1.10


class TestSparseAndNoHistory:
    def test_single_observation_is_insufficient_history(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=KICKOFF - timedelta(hours=6)))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        assert series.bookmaker_summary.n_observations == 1
        assert series.bookmaker_summary.insufficient_history is True
        assert series.bookmaker_summary.total_probability_change is None
        assert series.bookmaker_summary.largest_single_movement is None

    def test_no_history_returns_honest_empty_series_not_none(self, db_session):
        match, home, away, _ = _seed_match(db_session)

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        assert series is not None
        assert series.bookmaker_quotes == []
        assert series.bookmaker_summary.n_observations == 0
        assert series.bookmaker_summary.insufficient_history is True
        assert series.bookmaker_summary.first_observed is None
        assert series.consensus_series == []
        assert series.model_summary is None

    def test_unknown_match_returns_none(self, db_session):
        assert load_team_market_series(db_session, match_id=999999, market_type="h2h", selection="Collingwood", line_value=None) is None


class TestConsensusAcrossBookmakers:
    def test_multiple_bookmakers_devigged_using_own_book_opposite_side(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        sportsbet = Bookmaker(name="SportsBet")
        db_session.add_all([tab, sportsbet])
        db_session.flush()
        t = KICKOFF - timedelta(hours=10)
        # Both bookmakers quote BOTH sides at the same instant - genuinely devig-able.
        db_session.add(_quote(match, tab, selection=home.name, price=1.80, recorded_at=t))
        db_session.add(_quote(match, tab, selection=away.name, price=2.20, recorded_at=t))
        db_session.add(_quote(match, sportsbet, selection=home.name, price=1.85, recorded_at=t))
        db_session.add(_quote(match, sportsbet, selection=away.name, price=2.10, recorded_at=t))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        assert len(series.consensus_series) == 1
        point = series.consensus_series[0]
        assert point.n_bookmakers == 2
        assert point.n_devigged == 2  # both books had their opposite side known at the same instant

    def test_raw_implied_probability_used_when_opposite_side_unknown(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        t = KICKOFF - timedelta(hours=10)
        db_session.add(_quote(match, tab, selection=home.name, price=1.80, recorded_at=t))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        assert len(series.consensus_series) == 1
        assert series.consensus_series[0].n_devigged == 0
        assert series.consensus_series[0].consensus_probability == 1 / 1.80

    def test_excluded_bookmaker_never_contributes_to_consensus(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        betfair = Bookmaker(name="Betfair Exchange", eligibility=ELIGIBILITY_EXCLUDED)
        db_session.add_all([tab, betfair])
        db_session.flush()
        t = KICKOFF - timedelta(hours=10)
        db_session.add(_quote(match, tab, selection=home.name, price=1.80, recorded_at=t))
        db_session.add(_quote(match, betfair, selection=home.name, price=1.50, recorded_at=t))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        assert len(series.consensus_series) == 1
        assert series.consensus_series[0].n_bookmakers == 1  # excluded bookmaker's quote still shown raw, but never in consensus
        assert any(q.bookmaker_name == "Betfair Exchange" for q in series.bookmaker_quotes)

    def test_line_market_never_devigged_even_with_opposite_present(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        t = KICKOFF - timedelta(hours=10)
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=t, line_value=24.5, market_type="line"))
        db_session.add(_quote(match, tab, selection=away.name, price=1.90, recorded_at=t, line_value=-24.5, market_type="line"))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="line", selection=home.name, line_value=24.5)

        assert len(series.consensus_series) == 1
        assert series.consensus_series[0].n_devigged == 0  # never fabricated for handicap markets


class TestModelAndBookmakerIndependentTimestamps:
    def test_model_and_bookmaker_observations_never_share_or_align_timestamps(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        quote_t = KICKOFF - timedelta(hours=20)
        model_t1 = KICKOFF - timedelta(hours=18, minutes=13)
        model_t2 = KICKOFF - timedelta(hours=2, minutes=47)
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=quote_t))
        db_session.add(_team_obs(match, selection=home.name, value=0.50, recorded_at=model_t1))
        db_session.add(_team_obs(match, selection=home.name, value=0.55, recorded_at=model_t2))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None)

        bookmaker_times = {q.recorded_at.replace(tzinfo=timezone.utc) for q in series.bookmaker_quotes}
        model_times = {o.recorded_at.replace(tzinfo=timezone.utc) for o in series.model_observations}
        assert bookmaker_times == {quote_t}
        assert model_times == {model_t1, model_t2}
        assert bookmaker_times.isdisjoint(model_times)  # never fabricated onto a shared instant

    def test_model_summary_absent_for_non_h2h_team_markets(self, db_session):
        match, home, away, _ = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        db_session.add(_quote(match, tab, selection="over", price=1.90, recorded_at=KICKOFF - timedelta(hours=5), market_type="total", line_value=150.5))
        db_session.commit()

        series = load_team_market_series(db_session, match_id=match.id, market_type="total", selection="over", line_value=150.5)

        # No model_value_observations table entry represents a "total"
        # market at all (only team win probability is tracked) - never
        # invent a model series where none was ever recorded.
        assert series.model_observations == []
        assert series.model_summary is None


class TestPlayerSeriesAndLineupStatus:
    def _seed_player_quote(self, db, match, bookmaker, player, *, price, recorded_at, threshold=23.5, market_type="player_disposals"):
        db.add(PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=market_type,
            line_type="over_under", threshold=threshold, selection="over", price_decimal=price, recorded_at=recorded_at, source="manual",
        ))

    def _seed_player_model_obs(self, db, match, player, *, value, recorded_at, lineup_status, threshold=23.5):
        db.add(ModelValueObservation(
            match_id=match.id, player_id=player.id, value_type=VALUE_PLAYER_DISPOSAL_PROBABILITY, value_kind=KIND_PROBABILITY,
            selection=None, threshold=threshold, value=value, lineup_status=lineup_status, model_name="disposals_huber",
            model_version="v1", data_cutoff=recorded_at, recorded_at=recorded_at,
        ))

    def test_lineup_status_change_detected_and_value_delta_reported_honestly(self, db_session):
        match, home, away, player = _seed_match(db_session)
        self._seed_player_model_obs(db_session, match, player, value=0.40, recorded_at=KICKOFF - timedelta(hours=30), lineup_status="uncertain")
        self._seed_player_model_obs(db_session, match, player, value=0.52, recorded_at=KICKOFF - timedelta(hours=6), lineup_status="confirmed_selected")
        db_session.commit()

        series = load_player_market_series(db_session, match_id=match.id, player_id=player.id, market_type="player_disposals", line_type="over_under", threshold=23.5)

        assert len(series.lineup_status_changes) == 1
        change = series.lineup_status_changes[0]
        assert change.from_status == "uncertain"
        assert change.to_status == "confirmed_selected"
        assert change.value_changed_at_same_observation is True
        assert change.value_before == 0.40
        assert change.value_after == 0.52

    def test_no_lineup_status_change_when_status_stable(self, db_session):
        match, home, away, player = _seed_match(db_session)
        self._seed_player_model_obs(db_session, match, player, value=0.40, recorded_at=KICKOFF - timedelta(hours=30), lineup_status="confirmed_selected")
        self._seed_player_model_obs(db_session, match, player, value=0.44, recorded_at=KICKOFF - timedelta(hours=6), lineup_status="confirmed_selected")
        db_session.commit()

        series = load_player_market_series(db_session, match_id=match.id, player_id=player.id, market_type="player_disposals", line_type="over_under", threshold=23.5)

        assert series.lineup_status_changes == []

    def test_multiple_bookmakers_player_consensus(self, db_session):
        match, home, away, player = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        sportsbet = Bookmaker(name="SportsBet")
        db_session.add_all([tab, sportsbet])
        db_session.flush()
        t = KICKOFF - timedelta(hours=8)
        self._seed_player_quote(db_session, match, tab, player, price=1.90, recorded_at=t)
        self._seed_player_quote(db_session, match, sportsbet, player, price=1.95, recorded_at=t)
        db_session.commit()

        series = load_player_market_series(db_session, match_id=match.id, player_id=player.id, market_type="player_disposals", line_type="over_under", threshold=23.5)

        assert len(series.bookmaker_quotes) == 2
        assert series.consensus_series[0].n_bookmakers == 2

    def test_multi_plus_market_never_devigged_no_opposite_side_exists(self, db_session):
        match, home, away, player = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        db_session.add(PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=tab.id, market_type="player_goals",
            line_type="multi_plus", threshold=2.5, selection="yes", price_decimal=3.50, recorded_at=KICKOFF - timedelta(hours=5), source="manual",
        ))
        db_session.commit()

        series = load_player_market_series(db_session, match_id=match.id, player_id=player.id, market_type="player_goals", line_type="multi_plus", threshold=2.5)

        assert len(series.consensus_series) == 1
        assert series.consensus_series[0].n_devigged == 0

    def test_no_history_for_player_market_is_honest_empty_series(self, db_session):
        match, home, away, player = _seed_match(db_session)

        series = load_player_market_series(db_session, match_id=match.id, player_id=player.id, market_type="player_disposals", line_type="over_under", threshold=23.5)

        assert series is not None
        assert series.bookmaker_quotes == []
        assert series.lineup_status_changes == []
        assert series.bookmaker_summary.insufficient_history is True

    def test_unknown_player_returns_none(self, db_session):
        match, home, away, player = _seed_match(db_session)
        assert load_player_market_series(db_session, match_id=match.id, player_id=999999, market_type="player_disposals", line_type="over_under", threshold=23.5) is None


class TestDiscovery:
    def test_list_matches_with_history_includes_matches_with_any_source(self, db_session):
        match, home, away, player = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=KICKOFF - timedelta(hours=5)))
        db_session.commit()

        rows = list_matches_with_history(db_session)

        assert len(rows) == 1
        assert rows[0].match.match_id == match.id
        assert rows[0].n_odds_quotes == 1

    def test_list_matches_with_history_empty_when_nothing_persisted(self, db_session):
        assert list_matches_with_history(db_session) == []

    def test_market_options_reports_model_series_availability_only_for_h2h(self, db_session):
        match, home, away, player = _seed_match(db_session)
        tab = Bookmaker(name="TAB")
        db_session.add(tab)
        db_session.flush()
        db_session.add(_quote(match, tab, selection=home.name, price=1.90, recorded_at=KICKOFF - timedelta(hours=5), market_type="h2h"))
        db_session.add(_quote(match, tab, selection="over", price=1.90, recorded_at=KICKOFF - timedelta(hours=5), market_type="total", line_value=150.5))
        db_session.add(_team_obs(match, selection=home.name, value=0.55, recorded_at=KICKOFF - timedelta(hours=5)))
        db_session.commit()

        options = list_market_options(db_session, match_id=match.id)

        h2h_option = next(o for o in options.team_markets if o.market_type == "h2h")
        total_option = next(o for o in options.team_markets if o.market_type == "total")
        assert h2h_option.has_model_series is True
        assert total_option.has_model_series is False

    def test_market_options_unknown_match_returns_none(self, db_session):
        assert list_market_options(db_session, match_id=999999) is None
