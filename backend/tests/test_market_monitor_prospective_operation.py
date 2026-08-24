"""Targeted tests for the Genuine Prospective Market-Monitor Operation
stage (item 9): genuine prospective vs retrospective separation, immutable
initial freeze, repeated follow-up snapshots, time-to-kickoff bucket
capture, pre-kickoff-only updates, and player metadata frozen correctly."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.market_monitor.case_builder import build_cases
from app.market_monitor.case_snapshot_service import _stage_bucket, freeze_or_refresh_case_snapshots
from app.market_monitor.effectiveness import compute_effectiveness_summary
from app.market_monitor.inbox import RankedCase
from app.market_monitor.priority import TIER_CRITICAL, PriorityBreakdown
from app.market_monitor.types import MODEL_VS_MARKET_DIVERGENCE, Alert
from app.models import (
    AnomalyCaseFollowUp,
    AnomalyCaseSnapshot,
    Bookmaker,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.market import PlayerMarket

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _seed_match(db, *, status=MatchStatus.SCHEDULED, scheduled_start=None):
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
        scheduled_start=scheduled_start or (NOW + timedelta(hours=30)), status=status,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _seed_player(db, match, home):
    player = Player(sport_id=match.sport_id, display_name="Test Player", source="afltables", source_player_id="mm-p1", current_team_id=home.id)
    db.add(player)
    db.commit()
    return player


def _seed_disposal_projection(db, match, home, player, *, games_of_history=40, season_avg=25.0):
    db.add(PlayerModelRun(
        model_name="disposals_test", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=NOW,
    ))
    row = PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_test", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=games_of_history,
        predicted_mean=25.0, distribution_method="nb", nb_alpha=1.5, confidence_tier="higher_confidence",
        warnings=[], input_features={"disposals_season_avg": season_avg},
    )
    db.add(row)
    db.commit()
    return row


def _bookmaker(db, name):
    b = Bookmaker(name=name)
    db.add(b)
    db.commit()
    return b


def _alert(**overrides) -> Alert:
    base = dict(
        alert_type=MODEL_VS_MARKET_DIVERGENCE, severity="warning", reason_code="x", detail="x",
        match_id=1, home_team="Home", away_team="Away", player_id=None, player_name="Test Player", team_id=None,
        market_type=PlayerMarket.DISPOSALS.value, selection="over", threshold=25.5, line_value=None,
        model_probability=0.148, model_fair_odds=6.76, market_consensus_probability=0.45,
        bookmaker_prices=[], freshness="fresh", model_version="v1", lineup_status=None, context_state=None,
        model_risk_flags=[], generated_at=NOW, magnitude=0.30,
    )
    base.update(overrides)
    return Alert(**base)


def _critical_ranked_case(case):
    priority = PriorityBreakdown(total_score=75.0, tier=TIER_CRITICAL, components=[], persistence_label="transient", n_snapshots=1, model_support=None)
    return RankedCase(case=case, priority=priority, lifecycle="new", manual_status=None)


def _seed_case(db, *, match_status=MatchStatus.SCHEDULED, scheduled_start=None):
    match, home, away = _seed_match(db, status=match_status, scheduled_start=scheduled_start)
    player = _seed_player(db, match, home)
    _seed_disposal_projection(db, match, home, player)
    bookmaker = _bookmaker(db, "Ladbrokes")
    db.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=25.5, selection="over", price_decimal=1.96, recorded_at=NOW, source="the_odds_api",
    ))
    db.commit()
    case = build_cases([_alert(match_id=match.id, player_id=player.id, home_team=home.name, away_team=away.name)])[0]
    return match, player, case


# --- Time-to-kickoff bucket capture -------------------------------------------


def test_stage_bucket_boundaries():
    assert _stage_bucket(30.0) == "24h_plus"
    assert _stage_bucket(24.0) == "24h_plus"
    assert _stage_bucket(23.99) == "6_24h"
    assert _stage_bucket(6.0) == "6_24h"
    assert _stage_bucket(5.99) == "1_6h"
    assert _stage_bucket(1.0) == "1_6h"
    assert _stage_bucket(0.99) == "under_1h"
    assert _stage_bucket(0.0) == "under_1h"


# --- Genuine prospective vs retrospective separation --------------------------


def test_capture_mode_reflects_match_status_at_freeze_time(db_session):
    match, player, case = _seed_case(db_session, match_status=MatchStatus.SCHEDULED)
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    assert snap.capture_mode == "prospective"


def test_retrospective_case_is_tagged_and_excluded_from_prospective_metrics(db_session):
    match, player, case = _seed_case(db_session, match_status=MatchStatus.COMPLETED)
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    assert snap.capture_mode == "retrospective"

    assert compute_effectiveness_summary(db_session, capture_mode="prospective").n_frozen_cases == 0
    assert compute_effectiveness_summary(db_session, capture_mode="retrospective").n_frozen_cases == 1


# --- Immutable initial freeze --------------------------------------------------


def test_frozen_provenance_and_player_metadata_never_change_on_refresh(db_session):
    match, player, case = _seed_case(db_session, match_status=MatchStatus.SCHEDULED, scheduled_start=NOW + timedelta(hours=30))
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    frozen = (snap.capture_mode, snap.player_prior_games, snap.player_season_avg, snap.player_historical_volume_bucket, snap.frozen_at)

    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW + timedelta(hours=10))
    db_session.refresh(snap)
    assert (snap.capture_mode, snap.player_prior_games, snap.player_season_avg, snap.player_historical_volume_bucket, snap.frozen_at) == frozen


# --- Player metadata frozen correctly ------------------------------------------


def test_player_history_metadata_frozen_at_case_creation(db_session):
    match, player, case = _seed_case(db_session, match_status=MatchStatus.SCHEDULED)
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    assert snap.player_prior_games == 40
    assert snap.player_season_avg == 25.0
    assert snap.player_historical_volume_bucket == "22-28"  # VOLUME_EDGES=(0,15,22,28,1000)


def test_team_level_case_has_no_player_metadata(db_session):
    match, home, away = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    case = build_cases([_alert(
        match_id=match.id, player_id=None, team_id=None, market_type="h2h", selection=home.name, threshold=None,
        home_team=home.name, away_team=away.name,
    )])[0]
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    assert snap.player_prior_games is None
    assert snap.player_season_avg is None
    assert snap.player_historical_volume_bucket is None


# --- Repeated follow-up snapshots + pre-kickoff-only updates -------------------


def test_repeated_refreshes_capture_one_followup_per_stage_and_dedupe_within_stage(db_session):
    match, player, case = _seed_case(db_session, match_status=MatchStatus.SCHEDULED, scheduled_start=NOW + timedelta(hours=30))
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)  # initial freeze, ~30h out

    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))

    # Still 24h+ out - a second refresh in the SAME stage must not duplicate.
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW + timedelta(hours=2))
    followups = db_session.scalars(select(AnomalyCaseFollowUp).where(AnomalyCaseFollowUp.snapshot_id == snap.id)).all()
    assert len(followups) == 1
    assert followups[0].stage_bucket == "24h_plus"

    # Crosses into 6-24h.
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW + timedelta(hours=10))
    # Crosses into 1-6h.
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW + timedelta(hours=26))
    # Crosses into <1h.
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW + timedelta(hours=29, minutes=30))

    followups = db_session.scalars(select(AnomalyCaseFollowUp).where(AnomalyCaseFollowUp.snapshot_id == snap.id)).all()
    assert {f.stage_bucket for f in followups} == {"24h_plus", "6_24h", "1_6h", "under_1h"}
    assert len(followups) == 4


def test_no_followups_or_rolling_updates_once_match_completes(db_session):
    match, player, case = _seed_case(db_session, match_status=MatchStatus.SCHEDULED, scheduled_start=NOW + timedelta(hours=5))
    ranked = _critical_ranked_case(case)
    freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW)
    snap = db_session.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    n_refreshes_before = snap.n_prekickoff_refreshes
    latest_observed_before = snap.latest_observed_at

    match.status = MatchStatus.COMPLETED
    db_session.commit()

    n_new, n_refreshed = freeze_or_refresh_case_snapshots(db_session, [ranked], now=NOW + timedelta(hours=6))
    assert (n_new, n_refreshed) == (0, 0)
    db_session.refresh(snap)
    assert snap.n_prekickoff_refreshes == n_refreshes_before
    assert snap.latest_observed_at == latest_observed_before
    assert db_session.scalars(select(AnomalyCaseFollowUp).where(AnomalyCaseFollowUp.snapshot_id == snap.id)).all() == []
