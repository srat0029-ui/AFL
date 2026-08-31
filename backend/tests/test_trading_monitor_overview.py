from datetime import datetime, timedelta, timezone

from app.models import KIND_PROBABILITY, ModelValueObservation, Match, MatchStatus, Round, Season, Sport, Team, VALUE_TEAM_WIN_PROBABILITY
from app.trading_monitor.overview import load_trading_monitor_overview

NOW = datetime.now(timezone.utc)


def _seed_match(db):
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
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match


class TestLoadTradingMonitorOverview:
    def test_empty_db_does_not_crash(self, db_session):
        overview = load_trading_monitor_overview(db_session)

        assert overview.summary.n_upcoming_matches == 0
        assert overview.needs_attention == []
        assert overview.market_movers == []
        assert overview.model_movers == []
        assert overview.sgm.n_recent_snapshots == 0
        assert overview.data_health.backlog.prop_observations_unsettled == 0

    def test_material_model_movements_surface_in_summary(self, db_session):
        match = _seed_match(db_session)
        db_session.add(ModelValueObservation(
            match_id=match.id, player_id=None, value_type=VALUE_TEAM_WIN_PROBABILITY, value_kind=KIND_PROBABILITY,
            selection="Collingwood", threshold=None, value=0.40, lineup_status=None, model_name="elo_poisson",
            model_version="v1", data_cutoff=NOW, recorded_at=NOW - timedelta(hours=6),
        ))
        db_session.add(ModelValueObservation(
            match_id=match.id, player_id=None, value_type=VALUE_TEAM_WIN_PROBABILITY, value_kind=KIND_PROBABILITY,
            selection="Collingwood", threshold=None, value=0.55, lineup_status=None, model_name="elo_poisson",
            model_version="v1", data_cutoff=NOW, recorded_at=NOW,
        ))
        db_session.commit()

        overview = load_trading_monitor_overview(db_session)

        assert len(overview.model_movers) == 1
        assert overview.model_movers[0].is_material is True
        assert overview.summary.n_material_model_movements == 1

    def test_limit_caps_returned_lists(self, db_session):
        match = _seed_match(db_session)
        for i in range(30):
            db_session.add(ModelValueObservation(
                match_id=match.id, player_id=i, value_type="player_disposal_projected_mean", value_kind="projected_mean",
                selection=None, threshold=None, value=20.0, lineup_status=None, model_name="disposal_nb", model_version="v1",
                data_cutoff=NOW, recorded_at=NOW - timedelta(hours=6),
            ))
            db_session.add(ModelValueObservation(
                match_id=match.id, player_id=i, value_type="player_disposal_projected_mean", value_kind="projected_mean",
                selection=None, threshold=None, value=30.0, lineup_status=None, model_name="disposal_nb", model_version="v1",
                data_cutoff=NOW, recorded_at=NOW,
            ))
        db_session.commit()

        overview = load_trading_monitor_overview(db_session, limit=5)

        assert len(overview.model_movers) == 5
