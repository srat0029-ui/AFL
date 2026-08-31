from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import (
    Match, MatchStatus, ModelValueObservation, Player, PlayerDisposalProjection, Round, Season, Sport, Team,
    VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN, VALUE_TEAM_WIN_PROBABILITY,
)
from app.player_modelling.model_value_observations import record_model_value_observations

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, status=MatchStatus.SCHEDULED):
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
        scheduled_start=NOW + timedelta(days=1), status=status,
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
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 10, "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )


def _seed_disposal_player(db, match, team, *, predicted_mean=22.0, nb_alpha=0.15, lineup_status="expected_in"):
    player = Player(sport_id=match.sport_id, display_name="Test Player", source="afltables", source_player_id="test-player", current_team_id=team.id)
    db.add(player)
    db.flush()
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id, model_name="disposals_ridge", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation=lineup_status, games_of_history=40,
        predicted_mean=predicted_mean, distribution_method="nb", nb_alpha=nb_alpha,
        confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    db.commit()
    return player


class TestRecordModelValueObservations:
    def test_only_considers_scheduled_matches(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
        report = record_model_value_observations(db_session, [match.id])
        assert report.matches_considered == 0
        assert report.observations_created == 0

    def test_creates_team_and_player_observations(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home)

        report = record_model_value_observations(db_session, [match.id])

        assert report.matches_considered == 1
        assert report.observations_created > 0
        team_rows = db_session.scalars(select(ModelValueObservation).where(ModelValueObservation.value_type == VALUE_TEAM_WIN_PROBABILITY)).all()
        assert {r.selection for r in team_rows} == {"Collingwood", "Carlton"}
        mean_row = db_session.scalar(select(ModelValueObservation).where(
            ModelValueObservation.value_type == VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN, ModelValueObservation.player_id == player.id,
        ))
        assert mean_row is not None
        assert mean_row.value == 22.0
        assert mean_row.lineup_status == "expected_in"

    def test_second_run_with_no_changes_creates_nothing(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        _seed_disposal_player(db_session, match, home)

        first = record_model_value_observations(db_session, [match.id])
        second = record_model_value_observations(db_session, [match.id])

        assert first.observations_created > 0
        assert second.observations_created == 0

    def test_real_change_creates_a_new_row_not_an_overwrite(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home, predicted_mean=22.0)

        record_model_value_observations(db_session, [match.id])
        n_before = len(db_session.scalars(select(ModelValueObservation)).all())

        # Projection regenerates with a materially different mean.
        proj = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
        proj.predicted_mean = 29.4
        db_session.commit()

        report = record_model_value_observations(db_session, [match.id])
        n_after = len(db_session.scalars(select(ModelValueObservation)).all())

        assert report.observations_created >= 1
        assert n_after > n_before  # a NEW row, old one still present
        mean_rows = db_session.scalars(
            select(ModelValueObservation)
            .where(ModelValueObservation.value_type == VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN, ModelValueObservation.player_id == player.id)
            .order_by(ModelValueObservation.recorded_at)
        ).all()
        assert len(mean_rows) == 2
        assert mean_rows[0].value == 22.0
        assert mean_rows[1].value == 29.4

    def test_negligible_float_noise_does_not_create_a_new_row(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home, predicted_mean=22.0)

        record_model_value_observations(db_session, [match.id])

        proj = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
        proj.predicted_mean = 22.02  # rounds to the same 0.1 precision as 22.0
        db_session.commit()

        report = record_model_value_observations(db_session, [match.id])
        mean_rows = db_session.scalars(
            select(ModelValueObservation).where(ModelValueObservation.value_type == VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN, ModelValueObservation.player_id == player.id)
        ).all()
        assert len(mean_rows) == 1  # no new row for noise below the rounding floor

    def test_lineup_status_change_alone_creates_a_new_row(self, db_session):
        _seed_model_runs(db_session)
        match, home, away = _seed_match(db_session)
        player = _seed_disposal_player(db_session, match, home, lineup_status="uncertain")

        record_model_value_observations(db_session, [match.id])

        proj = db_session.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.player_id == player.id))
        proj.lineup_status_at_generation = "expected_in"
        db_session.commit()

        report = record_model_value_observations(db_session, [match.id])
        mean_rows = db_session.scalars(
            select(ModelValueObservation).where(ModelValueObservation.value_type == VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN, ModelValueObservation.player_id == player.id)
        ).all()
        assert len(mean_rows) == 2
        assert mean_rows[0].lineup_status == "uncertain"
        assert mean_rows[1].lineup_status == "expected_in"
