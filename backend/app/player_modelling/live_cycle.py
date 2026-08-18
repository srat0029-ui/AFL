"""Orchestrates one end-to-end operational cycle (Sections 1-3 of the
live-operations stage brief): refresh fixtures -> update newly-completed
player stats -> settle completed prop observations -> detect & regenerate
stale projections -> sanity-check them -> quota-aware, match-time-aware
odds refresh + observation creation -> persist a concise operational
summary. Every step below delegates to an already-existing, already-tested
implementation elsewhere in this codebase (named in each step's comment) —
this module contains orchestration and failure classification only, no
duplicated business logic.

Failure classification (Section 2): each step is independently wrapped so
one failing component (e.g. AFL Tables temporarily blocking scraper
traffic) doesn't take down the rest of the cycle — fixture refresh can fail
while odds refresh still proceeds normally.

  - WARNING: an expected/benign no-op (nothing to do, provider not configured).
  - RECOVERABLE_FAILURE: a step failed, but the cycle continues; the run is
    still useful, just incomplete for that one step. This is the default
    classification for genuinely unexpected exceptions in most steps.
  - BLOCKING_FAILURE: reserved for the one case where nothing downstream
    can be meaningful at all — see identify_upcoming_round below; this is
    the ONLY step that can end the cycle early.

Section 14 requires settlement to run BEFORE new data collection, hence the
step order below (settle_props is step 3, before regeneration/odds).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingestion.fixtures import ingest_fixtures
from app.ingestion.player_stats import ingest_player_stats
from app.models import (
    RUN_BLOCKED,
    RUN_OK,
    RUN_PARTIAL,
    STEP_BLOCKING_FAILURE,
    STEP_RECOVERABLE_FAILURE,
    STEP_SUCCESS,
    STEP_WARNING,
    LiveCycleRun,
    Match,
    MatchStatus,
    PlayerMatchStat,
    Season,
    Team,
)
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.live_engine import ModelsUnavailableError, PromotedModelsUnavailableError, generate_live_projections
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.live_sanity import confirmed_out_player_ids_by_match, run_all_sanity_checks
from app.player_modelling.prop_observation import ObservationCreationReport, create_observations_for_match
from app.player_modelling.prop_odds_ingestion import run_prop_odds_refresh
from app.player_modelling.prop_settlement import settle_all_completed_matches
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.providers.afl.afltables_players import AFLTablesPlayerStatsProvider
from app.providers.afl.squiggle import SquiggleFixtureProvider
from app.providers.afl.the_odds_api import MODELLED_MARKET_KEYS, TheOddsApiProvider


@dataclass
class StepResult:
    step: str
    status: str
    detail: str


@dataclass
class LiveCycleReport:
    steps: list[StepResult] = field(default_factory=list)
    matches_affected: set[int] = field(default_factory=set)
    quotes_added: int = 0
    observations_added: int = 0
    observations_settled: int = 0
    odds_credits_consumed: int | None = None
    odds_credits_remaining: int | None = None

    def add(self, step: str, status: str, detail: str) -> None:
        self.steps.append(StepResult(step=step, status=status, detail=detail))

    @property
    def overall_status(self) -> str:
        if any(s.status == STEP_BLOCKING_FAILURE for s in self.steps):
            return RUN_BLOCKED
        if any(s.status == STEP_RECOVERABLE_FAILURE for s in self.steps):
            return RUN_PARTIAL
        return RUN_OK

    @property
    def exit_code(self) -> int:
        """2 = blocking (nothing meaningful could be done at all), 1 =
        partial (at least one step failed but the cycle still made
        progress), 0 = every step succeeded or only warned."""
        status = self.overall_status
        if status == RUN_BLOCKED:
            return 2
        if status == RUN_PARTIAL:
            return 1
        return 0


def _update_completed_player_stats(db: Session) -> tuple[int, int]:
    """Section 1's step 3: 'update any newly completed player data where
    available.' Scoped tightly — only (team, season) pairs that actually
    have a COMPLETED match with ZERO PlayerMatchStat rows get a fresh AFL
    Tables request, never a full historical backfill (see
    app/ingestion/cli.py's backfill_player_stats for that heavier,
    deliberately-separate command). Returns (team_seasons_updated,
    team_seasons_failed) — a single team-season failing (e.g. AFL Tables
    temporarily blocking scraper traffic) doesn't stop the others."""
    rows = db.execute(
        select(Match.id, Match.season_id, Match.home_team_id, Match.away_team_id)
        .outerjoin(PlayerMatchStat, PlayerMatchStat.match_id == Match.id)
        .where(Match.status == MatchStatus.COMPLETED, PlayerMatchStat.id.is_(None))
        .distinct()
    ).all()
    if not rows:
        return 0, 0

    seasons = {s.id: s.year for s in db.scalars(select(Season).where(Season.id.in_({r.season_id for r in rows}))).all()}
    team_seasons: set[tuple[int, int]] = set()
    for r in rows:
        year = seasons.get(r.season_id)
        if year is None:
            continue
        team_seasons.add((r.home_team_id, year))
        team_seasons.add((r.away_team_id, year))

    provider = AFLTablesPlayerStatsProvider()
    updated = failed = 0
    for team_id, year in team_seasons:
        team = db.get(Team, team_id)
        if team is None:
            continue
        try:
            player_rows = provider.get_team_season_player_stats("AFL", year, team.name)
            ingest_player_stats(db, player_rows, season_year=year)
            updated += 1
        except Exception:  # noqa: BLE001 — one team-season failing must not stop the others
            failed += 1
    return updated, failed


def run_live_cycle(db: Session) -> LiveCycleRun:
    report = LiveCycleReport()
    now = datetime.now(timezone.utc)

    # Step 1: refresh upcoming AFL fixtures/results (free — Squiggle).
    try:
        fixture_provider = SquiggleFixtureProvider()
        fixtures = fixture_provider.get_upcoming_fixtures("AFL")
        result = ingest_fixtures(db, fixtures)
        report.add(
            "refresh_fixtures", STEP_SUCCESS,
            f"{len(fixtures)} fixtures seen, created={result.matches_created} updated={result.matches_updated} unchanged={result.matches_unchanged}",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("refresh_fixtures", STEP_RECOVERABLE_FAILURE, f"fixture refresh failed: {exc}")

    # Step 2: identify the round to operate on. This is the ONLY step that
    # can BLOCK the cycle — every following step needs a round to work with,
    # and if fixtures were never ingested at all (fresh database, or the
    # fixture refresh above failed AND nothing was ever ingested before),
    # there is genuinely nothing meaningful left to do this cycle.
    upcoming_matches = load_next_upcoming_round(db)
    if not upcoming_matches:
        report.add(
            "identify_upcoming_round", STEP_BLOCKING_FAILURE if not any(s.step == "refresh_fixtures" and s.status == STEP_SUCCESS for s in report.steps) else STEP_WARNING,
            "no upcoming (scheduled) AFL matches found in the database",
        )
        return _persist_run(db, report, now)
    report.add("identify_upcoming_round", STEP_SUCCESS, f"{len(upcoming_matches)} upcoming match(es)")
    report.matches_affected.update(m.match_id for m in upcoming_matches)

    # Step 3: update newly-completed player data where available.
    try:
        updated, failed = _update_completed_player_stats(db)
        if updated == 0 and failed == 0:
            report.add("update_completed_player_stats", STEP_SUCCESS, "no completed matches are missing player stats")
        elif failed and not updated:
            report.add("update_completed_player_stats", STEP_RECOVERABLE_FAILURE, f"player-stat source temporarily unavailable for all {failed} team-season(s) attempted")
        else:
            status = STEP_SUCCESS if not failed else STEP_RECOVERABLE_FAILURE
            report.add("update_completed_player_stats", status, f"{updated} team-season(s) updated, {failed} failed")
    except Exception as exc:  # noqa: BLE001
        report.add("update_completed_player_stats", STEP_RECOVERABLE_FAILURE, f"player-stat update failed: {exc}")

    # Step 4: settle completed prop observations BEFORE collecting new data
    # (Section 14 — settlement must never be starved by new-data collection).
    try:
        settlement = settle_all_completed_matches(db)
        report.observations_settled += settlement.observations_settled
        detail = (
            f"settled {settlement.observations_settled} (won={settlement.observations_won} lost={settlement.observations_lost} "
            f"push={settlement.observations_pushed} void={settlement.observations_voided}), "
            f"{settlement.awaiting_player_stats} awaiting player stats, {settlement.observations_flagged_for_review} flagged for review"
        )
        report.add("settle_props", STEP_SUCCESS, detail)
    except Exception as exc:  # noqa: BLE001
        report.add("settle_props", STEP_RECOVERABLE_FAILURE, f"settlement failed: {exc}")

    # Step 5-6: detect stale projections and regenerate only those (reuses
    # exactly what `refresh-live` already does — see cli.py's _refresh_live).
    changed_match_ids: set[int] = set()
    try:
        changed_match_ids = detect_matches_needing_regeneration(db, upcoming_matches)
        if not changed_match_ids:
            report.add("regenerate_projections", STEP_SUCCESS, "no matches need regeneration — every persisted projection is already current")
        else:
            run = generate_live_projections(db, target_match_ids=changed_match_ids)
            n_disposals, n_goals = persist_projection_run(db, run)
            report.add("regenerate_projections", STEP_SUCCESS, f"regenerated {len(changed_match_ids)} match(es): {n_disposals} disposal + {n_goals} goal projections")

            # Step 9 (sanity checks) - only meaningful right after a
            # regeneration, since it inspects the in-memory run just built.
            confirmed_out_by_match = confirmed_out_player_ids_by_match(db, [m.match_id for m in run.upcoming_matches])
            anomalies = run_all_sanity_checks(run, confirmed_out_by_match)
            if anomalies:
                report.add("sanity_checks", STEP_WARNING, f"{len(anomalies)} anomaly/anomalies flagged (see live_sanity.py)")
            else:
                report.add("sanity_checks", STEP_SUCCESS, "no anomalies found")
    except (ModelsUnavailableError, PromotedModelsUnavailableError) as exc:
        report.add("regenerate_projections", STEP_RECOVERABLE_FAILURE, f"models unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001
        report.add("regenerate_projections", STEP_RECOVERABLE_FAILURE, f"projection regeneration failed: {exc}")

    # Step 7-8: quota-aware, match-time-aware odds refresh + frozen
    # model-market observation creation for whatever changed.
    try:
        settings = get_settings()
        odds_provider = TheOddsApiProvider(api_key=settings.the_odds_api_key)
        if not odds_provider.is_available:
            report.add("refresh_prop_odds", STEP_WARNING, "THE_ODDS_API_KEY not configured — skipped (manual prop entry still works)")
        else:
            odds_report = run_prop_odds_refresh(db, odds_provider, upcoming_matches, MODELLED_MARKET_KEYS)  # min_refresh_interval=None -> match-time-aware policy
            report.quotes_added += odds_report.quotes_created
            if odds_report.last_quota is not None:
                report.odds_credits_consumed = odds_report.last_quota.requests_used
                report.odds_credits_remaining = odds_report.last_quota.requests_remaining
            report.add(
                "refresh_prop_odds", STEP_SUCCESS,
                f"{odds_report.matches_resolved} match(es) refreshed, {odds_report.quotes_created} new quote(s), "
                f"{odds_report.matches_skipped_fresh} skipped (still within their refresh interval)",
            )

            obs_report = ObservationCreationReport()
            for m in upcoming_matches:
                match_report = create_observations_for_match(db, m.match_id)
                obs_report.observations_created += match_report.observations_created
                obs_report.observations_unchanged += match_report.observations_unchanged
            report.observations_added += obs_report.observations_created
            report.add("create_observations", STEP_SUCCESS, f"{obs_report.observations_created} new observation(s), {obs_report.observations_unchanged} unchanged (idempotent)")
    except Exception as exc:  # noqa: BLE001
        report.add("refresh_prop_odds", STEP_RECOVERABLE_FAILURE, f"odds refresh failed: {exc}")

    from app.player_modelling.request_cache import clear_ttl_cache

    clear_ttl_cache()
    return _persist_run(db, report, now)


def _persist_run(db: Session, report: LiveCycleReport, run_at: datetime) -> LiveCycleRun:
    row = LiveCycleRun(
        run_at=run_at,
        finished_at=datetime.now(timezone.utc),
        overall_status=report.overall_status,
        steps=[{"step": s.step, "status": s.status, "detail": s.detail} for s in report.steps],
        odds_credits_consumed=report.odds_credits_consumed,
        odds_credits_remaining=report.odds_credits_remaining,
        matches_affected=len(report.matches_affected),
        quotes_added=report.quotes_added,
        observations_added=report.observations_added,
        observations_settled=report.observations_settled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
