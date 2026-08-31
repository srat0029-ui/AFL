from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    Match, MatchStatus, Player, PlayerDisposalProjection, PlayerMatchStat, Round, Season,
    SgmDependenceCoefficient, SgmPriceSnapshot, SgmSnapshotLeg, Sport, Team,
)
from app.models.sgm_price_snapshot import (
    SNAPSHOT_HORIZON_1H_6H, SNAPSHOT_HORIZON_6H_24H, SNAPSHOT_HORIZON_24H_PLUS, SNAPSHOT_HORIZON_UNDER_1H,
)
from app.pricing.sgm_snapshot_service import (
    _combo_status,
    _settle_leg,
    compute_snapshot_horizon,
    freeze_sgm_price,
    settle_sgm_snapshots,
    snapshot_sgm_pricing,
)

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, home_name="Collingwood", away_name="Carlton", scheduled_start=None, status=MatchStatus.SCHEDULED):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name=home_name, short_name=home_name[:3].upper())
    away = Team(sport_id=sport.id, name=away_name, short_name=away_name[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=scheduled_start or (NOW + timedelta(days=1)), status=status,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _seed_model_runs(db):
    persist_model_run(
        db, "elo", EloConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 10, "holdout_value": 0.2, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db, "poisson", PoissonConfig(), 2022,
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 10, "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
            {"market_type": "line", "metric_name": "mae", "holdout_n": 10, "holdout_value": 27.2, "naive_baseline_value": 31.3, "has_edge_over_naive": True},
        ],
    )


def _seed_disposal_player(db, match, team, *, predicted_mean=22.0, nb_alpha=0.15, name="Test Disposals Player"):
    player = Player(sport_id=match.sport_id, display_name=name, source="afltables", source_player_id=name, current_team_id=team.id)
    db.add(player)
    db.flush()
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=predicted_mean, distribution_method="nb", nb_alpha=nb_alpha,
        confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    db.commit()
    return player


_SAMPLE_OPTION = {
    "same_game_pricing": {
        "model_joint_probability": 0.30, "model_joint_fair_odds": 3.33,
        "naive_independence_probability": 0.28, "correlation_adjustment_pp": 2.0,
        "dependence_validated": True, "model_version": "sgm_joint_conditional_mc_v1@test",
        "n_simulations": 20000, "mc_standard_error": 0.003,
        "_dependence_coefficients_used": {"disposals": {"slope": 0.016, "intercept": -0.04, "n_observations": 45216}},
        "_naive_independence_fair_odds": 3.57,
    },
    "legs": [
        {"opportunity_type": "team", "market_type": "h2h", "team_id": 1, "player_id": None, "selection": "Collingwood", "threshold": None, "line_value": None, "model_probability": 0.55},
        {"opportunity_type": "player", "market_type": "player_disposals", "team_id": None, "player_id": 42, "selection": None, "threshold": 21.5, "line_value": None, "model_probability": 0.6},
    ],
}


class TestComputeSnapshotHorizon:
    def test_boundaries(self):
        assert compute_snapshot_horizon(48) == SNAPSHOT_HORIZON_24H_PLUS
        assert compute_snapshot_horizon(24.0) == SNAPSHOT_HORIZON_24H_PLUS
        assert compute_snapshot_horizon(23.99) == SNAPSHOT_HORIZON_6H_24H
        assert compute_snapshot_horizon(6.0) == SNAPSHOT_HORIZON_6H_24H
        assert compute_snapshot_horizon(5.99) == SNAPSHOT_HORIZON_1H_6H
        assert compute_snapshot_horizon(1.0) == SNAPSHOT_HORIZON_1H_6H
        assert compute_snapshot_horizon(0.99) == SNAPSHOT_HORIZON_UNDER_1H
        assert compute_snapshot_horizon(0.0) == SNAPSHOT_HORIZON_UNDER_1H
        assert compute_snapshot_horizon(-1.0) == SNAPSHOT_HORIZON_UNDER_1H  # kickoff already passed


class TestFreezeSgmPrice:
    def test_creates_snapshot_and_legs(self, db_session):
        match, home, away = _seed_match(db_session)

        snap = freeze_sgm_price(db_session, match_id=match.id, option=_SAMPLE_OPTION, generated_at=NOW, hours_to_kickoff=30.0)
        db_session.commit()

        assert snap is not None
        assert snap.snapshot_horizon == SNAPSHOT_HORIZON_24H_PLUS
        assert snap.n_legs == 2
        assert snap.model_probability == 0.30
        assert snap.dependence_coefficients_used == {"disposals": {"slope": 0.016, "intercept": -0.04, "n_observations": 45216}}
        assert snap.bookmaker_sgm_price is None  # never fabricated

        legs = db_session.scalars(select(SgmSnapshotLeg).where(SgmSnapshotLeg.snapshot_id == snap.id).order_by(SgmSnapshotLeg.leg_index)).all()
        assert len(legs) == 2
        assert legs[0].leg_type == "h2h" and legs[0].selection == "Collingwood"
        assert legs[1].leg_type == "disposals" and legs[1].player_id == 42 and legs[1].threshold == 21.5 and legs[1].selection == "over"

    def test_idempotent_same_horizon(self, db_session):
        match, home, away = _seed_match(db_session)

        first = freeze_sgm_price(db_session, match_id=match.id, option=_SAMPLE_OPTION, generated_at=NOW, hours_to_kickoff=30.0)
        db_session.commit()
        second = freeze_sgm_price(db_session, match_id=match.id, option=_SAMPLE_OPTION, generated_at=NOW, hours_to_kickoff=29.5)
        db_session.commit()

        assert first is not None
        assert second is None  # same leg signature + model_version + horizon bucket -> no-op
        assert db_session.scalar(select(SgmPriceSnapshot).where(SgmPriceSnapshot.match_id == match.id)) is not None
        count = len(db_session.scalars(select(SgmPriceSnapshot).where(SgmPriceSnapshot.match_id == match.id)).all())
        assert count == 1

    def test_new_row_in_a_new_horizon_bucket(self, db_session):
        match, home, away = _seed_match(db_session)

        freeze_sgm_price(db_session, match_id=match.id, option=_SAMPLE_OPTION, generated_at=NOW, hours_to_kickoff=30.0)
        db_session.commit()
        second = freeze_sgm_price(db_session, match_id=match.id, option=_SAMPLE_OPTION, generated_at=NOW, hours_to_kickoff=3.0)
        db_session.commit()

        assert second is not None
        assert second.snapshot_horizon == SNAPSHOT_HORIZON_1H_6H
        count = len(db_session.scalars(select(SgmPriceSnapshot).where(SgmPriceSnapshot.match_id == match.id)).all())
        assert count == 2

    def test_returns_none_when_no_sgm_pricing(self, db_session):
        match, home, away = _seed_match(db_session)
        option_without_sgm = {**_SAMPLE_OPTION, "same_game_pricing": None}

        assert freeze_sgm_price(db_session, match_id=match.id, option=option_without_sgm, generated_at=NOW, hours_to_kickoff=30.0) is None

    def test_coefficients_denormalized_survive_later_refit(self, db_session):
        """Point-in-time provenance: SgmDependenceCoefficient is upserted in
        place, so a snapshot must carry the raw values it actually used,
        not just a reference that could later resolve to something else."""
        match, home, away = _seed_match(db_session)
        snap = freeze_sgm_price(db_session, match_id=match.id, option=_SAMPLE_OPTION, generated_at=NOW, hours_to_kickoff=30.0)
        db_session.commit()
        frozen_coeffs = dict(snap.dependence_coefficients_used)

        # Simulate a later refit changing the live coefficient to a new value.
        db_session.add(SgmDependenceCoefficient(
            market="disposals", slope=0.99, intercept=0.5, n_observations=99999,
            fit_cutoff_year=2025, model_version="sgm_joint_conditional_mc_v1@later", fitted_at=NOW,
        ))
        db_session.commit()

        db_session.refresh(snap)
        assert snap.dependence_coefficients_used == frozen_coeffs
        assert snap.dependence_coefficients_used["disposals"]["slope"] == 0.016  # unchanged despite the new live coefficient


class TestSettleLeg:
    def test_team_h2h_won(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        match.home_score, match.away_score = 100, 80
        db_session.commit()
        leg = SgmSnapshotLeg(snapshot_id=1, leg_index=0, leg_type="h2h", team_id=home.id, selection=home.name, naive_leg_probability=0.6)

        _settle_leg(db_session, leg, match, NOW)

        assert leg.leg_outcome == "won"
        assert leg.actual_value == 20.0
        assert leg.leg_resolved_at == NOW

    def test_team_h2h_lost(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        match.home_score, match.away_score = 70, 90
        db_session.commit()
        leg = SgmSnapshotLeg(snapshot_id=1, leg_index=0, leg_type="h2h", team_id=home.id, selection=home.name, naive_leg_probability=0.4)

        _settle_leg(db_session, leg, match, NOW)

        assert leg.leg_outcome == "lost"

    def test_awaiting_when_match_not_completed(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED)
        leg = SgmSnapshotLeg(snapshot_id=1, leg_index=0, leg_type="h2h", team_id=home.id, selection=home.name, naive_leg_probability=0.6)

        _settle_leg(db_session, leg, match, NOW)

        assert leg.leg_outcome is None  # stays pending, not an error

    def test_player_disposals_won_and_lost(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        player = _seed_disposal_player(db_session, match, home)
        db_session.add(PlayerMatchStat(match_id=match.id, player_id=player.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=25, goals=1))
        db_session.commit()

        won_leg = SgmSnapshotLeg(snapshot_id=1, leg_index=0, leg_type="disposals", player_id=player.id, selection="over", threshold=21.5, naive_leg_probability=0.6)
        lost_leg = SgmSnapshotLeg(snapshot_id=1, leg_index=1, leg_type="disposals", player_id=player.id, selection="over", threshold=30.5, naive_leg_probability=0.2)

        _settle_leg(db_session, won_leg, match, NOW)
        _settle_leg(db_session, lost_leg, match, NOW)

        assert won_leg.leg_outcome == "won" and won_leg.actual_value == 25.0
        assert lost_leg.leg_outcome == "lost"

    def test_void_when_player_dnp(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        player = _seed_disposal_player(db_session, match, home)
        # Another player on the match DID get a stat row, but not this one -> genuine DNP, not "awaiting".
        other = Player(sport_id=match.sport_id, display_name="Other Player", source="afltables", source_player_id="other", current_team_id=home.id)
        db_session.add(other)
        db_session.flush()
        db_session.add(PlayerMatchStat(match_id=match.id, player_id=other.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=10, goals=0))
        db_session.commit()

        leg = SgmSnapshotLeg(snapshot_id=1, leg_index=0, leg_type="disposals", player_id=player.id, selection="over", threshold=21.5, naive_leg_probability=0.6)
        _settle_leg(db_session, leg, match, NOW)

        assert leg.leg_outcome == "void"

    def test_awaiting_when_no_stats_ingested_at_all(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        player = _seed_disposal_player(db_session, match, home)
        leg = SgmSnapshotLeg(snapshot_id=1, leg_index=0, leg_type="disposals", player_id=player.id, selection="over", threshold=21.5, naive_leg_probability=0.6)

        _settle_leg(db_session, leg, match, NOW)

        assert leg.leg_outcome is None  # no PlayerMatchStat rows exist yet for this match at all -> retry later, not a DNP

    def test_already_resolved_leg_is_never_touched_again(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        match.home_score, match.away_score = 100, 80
        db_session.commit()
        leg = SgmSnapshotLeg(
            snapshot_id=1, leg_index=0, leg_type="h2h", team_id=home.id, selection=home.name, naive_leg_probability=0.6,
            leg_outcome="lost", actual_value=-999.0, leg_resolved_at=NOW - timedelta(days=1),
        )

        _settle_leg(db_session, leg, match, NOW)

        assert leg.leg_outcome == "lost"  # unchanged, even though the real result would say "won"
        assert leg.actual_value == -999.0
        assert leg.leg_resolved_at == NOW - timedelta(days=1)


class TestComboStatus:
    def _leg(self, outcome):
        return SgmSnapshotLeg(snapshot_id=1, leg_index=0, leg_type="h2h", selection="x", naive_leg_probability=0.5, leg_outcome=outcome)

    def test_any_lost_wins_immediately_even_with_pending(self):
        legs = [self._leg("lost"), self._leg(None)]
        assert _combo_status(legs) == "lost"

    def test_pending_when_nothing_lost_but_something_unresolved(self):
        legs = [self._leg("won"), self._leg(None)]
        assert _combo_status(legs) is None

    def test_won_when_all_resolved_and_none_lost(self):
        legs = [self._leg("won"), self._leg("push")]
        assert _combo_status(legs) == "won"

    def test_void_when_every_leg_is_void_or_push(self):
        legs = [self._leg("void"), self._leg("push")]
        assert _combo_status(legs) == "void"


class TestEndToEndLifecycle:
    def test_freeze_then_settle_full_combo(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED)
        player = _seed_disposal_player(db_session, match, home, predicted_mean=22.0)
        option = {
            "same_game_pricing": _SAMPLE_OPTION["same_game_pricing"],
            "legs": [
                {"opportunity_type": "team", "market_type": "h2h", "team_id": home.id, "player_id": None, "selection": home.name, "threshold": None, "line_value": None, "model_probability": 0.55},
                {"opportunity_type": "player", "market_type": "player_disposals", "team_id": None, "player_id": player.id, "selection": None, "threshold": 21.5, "line_value": None, "model_probability": 0.6},
            ],
        }
        snap = freeze_sgm_price(db_session, match_id=match.id, option=option, generated_at=NOW, hours_to_kickoff=2.0)
        db_session.commit()
        assert snap.outcome is None

        # Not settleable yet - match still scheduled.
        report = settle_sgm_snapshots(db_session)
        assert report.combos_settled == 0
        assert report.awaiting_data == 1

        # Match completes: home wins by 20, player gets 25 disposals -> both legs win.
        match.status = MatchStatus.COMPLETED
        match.home_score, match.away_score = 100, 80
        db_session.add(PlayerMatchStat(match_id=match.id, player_id=player.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=25, goals=1))
        db_session.commit()

        report2 = settle_sgm_snapshots(db_session)
        db_session.refresh(snap)

        assert report2.combos_settled == 1
        assert report2.combos_won == 1
        assert snap.outcome == "won"
        assert snap.settled_at is not None
        for leg in snap.legs:
            assert leg.leg_outcome == "won"

        # Idempotent: settling again changes nothing.
        settled_at_first = snap.settled_at
        report3 = settle_sgm_snapshots(db_session)
        db_session.refresh(snap)
        assert report3.combos_settled == 0
        assert snap.settled_at == settled_at_first

    def test_snapshot_sgm_pricing_only_considers_scheduled_matches(self, db_session):
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        report = snapshot_sgm_pricing(db_session, [match.id])
        assert report.matches_considered == 0
        assert report.snapshots_created == 0

    def test_snapshot_sgm_pricing_creates_real_rows(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED, scheduled_start=NOW + timedelta(hours=30))
        player = _seed_disposal_player(db_session, match, home)

        from app.models import Bookmaker, OddsQuote, PlayerPropMarket

        bookmaker = Bookmaker(name="SportsBet")
        db_session.add(bookmaker)
        db_session.flush()
        db_session.add(OddsQuote(
            match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection=home.name,
            line_value=None, price_decimal=1.40, recorded_at=NOW, source="manual", is_closing_line=False,
        ))
        db_session.add(PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
            line_type="over_under", threshold=10.5, selection="over", price_decimal=1.60,
            recorded_at=NOW, source="the_odds_api",
        ))
        db_session.commit()

        report = snapshot_sgm_pricing(db_session, [match.id])

        assert report.matches_considered == 1
        rows = db_session.scalars(select(SgmPriceSnapshot).where(SgmPriceSnapshot.match_id == match.id)).all()
        assert len(rows) == report.snapshots_created
        if rows:
            assert rows[0].bookmaker_sgm_price is None
            assert rows[0].snapshot_horizon == SNAPSHOT_HORIZON_24H_PLUS

    def test_snapshot_sgm_pricing_is_idempotent(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED, scheduled_start=NOW + timedelta(hours=30))
        player = _seed_disposal_player(db_session, match, home)

        from app.models import Bookmaker, OddsQuote, PlayerPropMarket

        bookmaker = Bookmaker(name="SportsBet")
        db_session.add(bookmaker)
        db_session.flush()
        db_session.add(OddsQuote(
            match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection=home.name,
            line_value=None, price_decimal=1.40, recorded_at=NOW, source="manual", is_closing_line=False,
        ))
        db_session.add(PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
            line_type="over_under", threshold=10.5, selection="over", price_decimal=1.60,
            recorded_at=NOW, source="the_odds_api",
        ))
        db_session.commit()

        first = snapshot_sgm_pricing(db_session, [match.id])
        second = snapshot_sgm_pricing(db_session, [match.id])

        assert second.snapshots_created == 0
        total_rows = len(db_session.scalars(select(SgmPriceSnapshot).where(SgmPriceSnapshot.match_id == match.id)).all())
        assert total_rows == first.snapshots_created
