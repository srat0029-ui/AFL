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

from sqlalchemy import func, select
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
    OddsQuote,
    PlayerMatchStat,
    Season,
    Team,
)
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.live_engine import ModelsUnavailableError, PromotedModelsUnavailableError, generate_live_projections
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.live_sanity import confirmed_out_player_ids_by_match, run_all_sanity_checks
from app.player_modelling.placed_bets import settle_placed_bets
from app.player_modelling.prop_observation import ObservationCreationReport, create_observations_for_match
from app.player_modelling.model_value_observations import record_model_value_observations
from app.pricing.sgm_snapshot_service import settle_sgm_snapshots, snapshot_sgm_pricing
from app.pricing.snapshot_service import settle_pricing_snapshots, snapshot_round_pricing
from app.player_modelling.prop_odds_ingestion import run_prop_odds_refresh
from app.player_modelling.prop_odds_quota import recommended_refresh_interval
from app.player_modelling.prop_settlement import settle_all_completed_matches
from app.player_modelling.team_odds_ingestion import PROVIDER_NAME as TEAM_ODDS_PROVIDER_NAME, ingest_team_odds
from app.player_modelling.upcoming_features import UpcomingMatchTeams, load_next_upcoming_round
from app.player_modelling.weather_ingestion import refresh_weather_for_matches
from app.providers.afl.afltables_players import AFLTablesPlayerStatsProvider
from app.providers.afl.squiggle import SquiggleFixtureProvider
from app.providers.afl.the_odds_api import MODELLED_MARKET_KEYS, TheOddsApiProvider

# How much staler than the match-time-aware odds interval (Section 4, see
# prop_odds_quota.py) team odds are allowed to get before a manual refresh
# spends quota on the standard-match-odds call again. Team odds are one
# call for every upcoming match combined (not per-match like player props),
# so this is a single yes/no gate keyed off whichever upcoming match is
# soonest to bounce - the same match that would need the tightest player-
# prop refresh interval right now.
TEAM_ODDS_STALENESS_MULTIPLIER = 1


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
    team_odds_quotes_added: int = 0
    weather_snapshots_added: int = 0
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


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _team_odds_needs_refresh(db: Session, upcoming_matches: list[UpcomingMatchTeams]) -> bool:
    """Mirrors prop_odds_quota.event_needs_refresh's reasoning (reuses the
    same recommended_refresh_interval policy) but for the single
    all-matches-in-one-call team-odds endpoint: gated off whichever
    upcoming match is soonest to bounce, since that's the tightest
    freshness requirement in play right now."""
    if not upcoming_matches:
        return False
    now = datetime.now(timezone.utc)
    soonest_hours = min((_aware(m.scheduled_start) - now).total_seconds() / 3600.0 for m in upcoming_matches)
    interval = recommended_refresh_interval(soonest_hours) * TEAM_ODDS_STALENESS_MULTIPLIER
    match_ids = [m.match_id for m in upcoming_matches]
    latest = db.scalar(
        select(func.max(OddsQuote.recorded_at)).where(OddsQuote.match_id.in_(match_ids), OddsQuote.source == TEAM_ODDS_PROVIDER_NAME)
    )
    if latest is None:
        return True
    return now - _aware(latest) >= interval


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

    # Step 4b: settle any pending PlacedBet rows the same way, right after
    # prop settlement and before new-data collection (same Section 14
    # reasoning) - see app/player_modelling/placed_bets.py, which reuses
    # this exact settlement math rather than duplicating it.
    try:
        bet_settlement = settle_placed_bets(db)
        detail = (
            f"settled {bet_settlement.bets_settled} (won={bet_settlement.bets_won} lost={bet_settlement.bets_lost} "
            f"push={bet_settlement.bets_pushed} void={bet_settlement.bets_voided}), {bet_settlement.awaiting_data} awaiting data"
        )
        if bet_settlement.legs_repaired:
            detail += f", {bet_settlement.legs_repaired} repaired"
        if bet_settlement.settlement_failures:
            detail += f", {bet_settlement.settlement_failures} settlement failure(s)"
        if bet_settlement.multis_settled:
            detail += (
                f", {bet_settlement.multis_settled} multi(s) settled "
                f"(won={bet_settlement.multis_won} lost={bet_settlement.multis_lost} void={bet_settlement.multis_voided})"
            )
        report.add("settle_placed_bets", STEP_SUCCESS, detail)
    except Exception as exc:  # noqa: BLE001
        report.add("settle_placed_bets", STEP_RECOVERABLE_FAILURE, f"placed-bet settlement failed: {exc}")

    # Step 4c: settle any pending PricingSnapshot rows (B2B Pricing Engine's
    # prospective evaluation dataset) - same Section 14 reasoning: before
    # new-data collection, reusing the exact same settlement primitives as
    # every other settlement step above (see app/pricing/snapshot_service.py).
    try:
        snap_settlement = settle_pricing_snapshots(db)
        report.add(
            "settle_pricing_snapshots", STEP_SUCCESS,
            f"settled {snap_settlement.settled} (won={snap_settlement.won} lost={snap_settlement.lost} "
            f"push={snap_settlement.pushed} void={snap_settlement.voided}), {snap_settlement.awaiting_data} awaiting data",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("settle_pricing_snapshots", STEP_RECOVERABLE_FAILURE, f"pricing snapshot settlement failed: {exc}")

    # Step 4d: settle any pending SgmPriceSnapshot rows (Same Game Multi's
    # own prospective evaluation dataset) - same Section 14 reasoning as
    # step 4c, and the same shared settlement primitives underneath (see
    # app/pricing/sgm_snapshot_service.py's module docstring).
    try:
        sgm_settlement = settle_sgm_snapshots(db)
        report.add(
            "settle_sgm_snapshots", STEP_SUCCESS,
            f"settled {sgm_settlement.combos_settled} (won={sgm_settlement.combos_won} lost={sgm_settlement.combos_lost} "
            f"void={sgm_settlement.combos_voided}), {sgm_settlement.legs_resolved} leg(s) newly resolved, "
            f"{sgm_settlement.awaiting_data} awaiting data",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("settle_sgm_snapshots", STEP_RECOVERABLE_FAILURE, f"SGM snapshot settlement failed: {exc}")

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

    # Step 10: quota-aware standard team-market (h2h/spreads/totals) odds
    # refresh - one call covers every upcoming match, so it's gated as a
    # single freshness check rather than per-match (see
    # _team_odds_needs_refresh). Reuses ingest_team_odds/
    # get_standard_match_odds exactly as `refresh-team-odds` does.
    try:
        settings = get_settings()
        team_odds_provider = TheOddsApiProvider(api_key=settings.the_odds_api_key)
        if not team_odds_provider.is_available:
            report.add("refresh_team_odds", STEP_WARNING, "THE_ODDS_API_KEY not configured — skipped (manual team odds entry still works)")
        elif not _team_odds_needs_refresh(db, upcoming_matches):
            report.add("refresh_team_odds", STEP_SUCCESS, "skipped — still within the match-time-aware refresh interval")
        else:
            odds_result = team_odds_provider.get_standard_match_odds("AFL")
            team_odds_report = ingest_team_odds(db, odds_result)
            report.team_odds_quotes_added += team_odds_report.quotes_created
            report.add(
                "refresh_team_odds", STEP_SUCCESS,
                f"{team_odds_report.matches_resolved} match(es) resolved, {team_odds_report.quotes_created} new quote(s)",
            )
    except Exception as exc:  # noqa: BLE001
        report.add("refresh_team_odds", STEP_RECOVERABLE_FAILURE, f"team odds refresh failed: {exc}")

    # Step 10a: Finals Market Readiness + Auto-Population stage, items 1-2,
    # 5: turn freshly-refreshed, already-resolved player-prop evidence
    # (resolve_prop_player already ran inside refresh_prop_odds above -
    # never re-resolved or guessed here) into a provisional (unconfirmed)
    # roster for any upcoming match still missing official/manual lineup
    # data for that player, THEN regenerate projections for exactly the
    # match(es) that gained new provisional players this cycle - so
    # /multis can start showing provisional options without a manual
    # restart or a second cycle. Reuses generate_live_projections/
    # persist_projection_run identically to steps 5-6 above; this is a
    # second, narrowly-targeted pass, never new model/projection logic.
    try:
        from app.player_modelling.provisional_roster import populate_provisional_roster

        matches_with_new_provisional_players: set[int] = set()
        total_players_added = 0
        for m in upcoming_matches:
            roster_report = populate_provisional_roster(db, m.match_id)
            if roster_report.players_added > 0:
                matches_with_new_provisional_players.add(m.match_id)
                total_players_added += roster_report.players_added

        if matches_with_new_provisional_players:
            provisional_run = generate_live_projections(db, target_match_ids=matches_with_new_provisional_players)
            n_disposals, n_goals = persist_projection_run(db, provisional_run)
            report.add(
                "populate_provisional_rosters", STEP_SUCCESS,
                f"{total_players_added} provisional player(s) added across {len(matches_with_new_provisional_players)} match(es); "
                f"{n_disposals} disposal + {n_goals} goal projection(s) regenerated",
            )
        else:
            report.add("populate_provisional_rosters", STEP_SUCCESS, "no new provisional players found")
    except (ModelsUnavailableError, PromotedModelsUnavailableError) as exc:
        report.add("populate_provisional_rosters", STEP_RECOVERABLE_FAILURE, f"models unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001
        report.add("populate_provisional_rosters", STEP_RECOVERABLE_FAILURE, f"provisional roster population failed: {exc}")

    # Step 10b: freeze this cycle's pricing into the prospective evaluation
    # dataset (B2B Pricing Engine item 5) - deliberately AFTER odds refresh
    # above, so whatever market context exists this cycle is captured
    # alongside the model price, not a stale market snapshot from before
    # this cycle's odds refresh ran. Idempotent per model_version (see
    # snapshot_price's docstring) - safe to run every cycle.
    try:
        snap_report = snapshot_round_pricing(db, [m.match_id for m in upcoming_matches])
        report.add(
            "snapshot_pricing", STEP_SUCCESS,
            f"{snap_report.matches_considered} match(es) considered: "
            f"{snap_report.team_snapshots_created} team, {snap_report.disposal_snapshots_created} disposal, "
            f"{snap_report.goal_snapshots_created} goal snapshot(s) newly frozen",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("snapshot_pricing", STEP_RECOVERABLE_FAILURE, f"pricing snapshot creation failed: {exc}")

    # Step 10b-ii: freeze this cycle's Same Game Multi joint prices, same
    # placement reasoning as step 10b (after odds refresh, so leg
    # probabilities reflect current lineup/odds context) - see
    # app/pricing/sgm_snapshot_service.py. Which combos get frozen is
    # whatever Multi Builder's own existing combo search actually surfaces
    # this cycle, not a separately invented selection.
    try:
        sgm_snap_report = snapshot_sgm_pricing(db, [m.match_id for m in upcoming_matches])
        report.add(
            "snapshot_sgm_pricing", STEP_SUCCESS,
            f"{sgm_snap_report.matches_considered} match(es) considered: {sgm_snap_report.snapshots_created} SGM snapshot(s) newly frozen",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("snapshot_sgm_pricing", STEP_RECOVERABLE_FAILURE, f"SGM snapshot creation failed: {exc}")

    # Step 10b-iii: capture this cycle's model-side values (team win
    # probability, player projections) into ModelValueObservation - the
    # Trading Monitor's one genuinely new history table (see
    # app/models/model_value_observation.py's docstring for why nothing
    # else in this codebase already tracks this). Same placement reasoning
    # as 10b/10b-ii: after odds refresh, so this reflects the cycle's real
    # state. Insert-only-on-change, so a normal cycle with no real movement
    # writes nothing new.
    try:
        obs_report = record_model_value_observations(db, [m.match_id for m in upcoming_matches])
        report.add(
            "record_model_value_observations", STEP_SUCCESS,
            f"{obs_report.matches_considered} match(es) considered: {obs_report.observations_created} observation(s) newly recorded",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("record_model_value_observations", STEP_RECOVERABLE_FAILURE, f"model value observation capture failed: {exc}")

    # Step 10c: freeze/refresh/settle High Priority + Critical Market
    # Monitor cases (Genuine Prospective Operation stage, item 1) - same
    # placement reasoning as step 10b: after this cycle's odds refresh, so
    # freshly-detected cases see this cycle's real market context. Scans
    # every match active_match_ids() considers live (not just this round),
    # matching the API's own /cases breadth - never a separate poll loop
    # (item 3), just piggybacking on however often this cycle already runs.
    try:
        from app.market_monitor.case_snapshot_service import freeze_or_refresh_case_snapshots, settle_case_snapshots
        from app.market_monitor.detector import active_match_ids as monitor_active_match_ids
        from app.market_monitor.inbox import build_trader_inbox

        monitor_match_ids = monitor_active_match_ids(db)
        ranked_cases = build_trader_inbox(db, monitor_match_ids, track_persistence=True, full_scan=True, now=now)
        n_new_cases, n_refreshed_cases = freeze_or_refresh_case_snapshots(db, ranked_cases, now=now)
        n_settled_cases = settle_case_snapshots(db, now=now)
        report.add(
            "market_monitor_prospective_snapshots", STEP_SUCCESS,
            f"{len(monitor_match_ids)} match(es) scanned: {n_new_cases} case(s) newly frozen, {n_refreshed_cases} refreshed, {n_settled_cases} settled",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("market_monitor_prospective_snapshots", STEP_RECOVERABLE_FAILURE, f"case snapshot freeze/settle failed: {exc}")

    # Step 10d: freeze/evaluate the finer-grained per-ALERT prospective
    # snapshots (B2B Market Anomaly / Trading QA Engine, item 8) - this
    # table predates the case-level one above and was never wired into any
    # scheduled flow; wiring it in now is a pure operational-consistency
    # fix, not new logic. freeze_anomaly_alerts already only ever freezes
    # matches it finds still SCHEDULED (so every row it writes is
    # inherently genuinely prospective - no capture_mode needed, unlike the
    # case-level table which also has a retrospective backfill path), is
    # idempotent by identity (re-detecting the same alert is a no-op, never
    # a duplicate or rewrite), and evaluate_anomaly_snapshots only ever
    # settles a snapshot once its match has COMPLETED. Independent try
    # block (not reusing step 10c's match list) so a failure in either
    # prospective-snapshot step never masks the other, per this module's
    # own failure-isolation design.
    try:
        from app.market_monitor.detector import active_match_ids as alert_active_match_ids
        from app.market_monitor.snapshot_service import evaluate_anomaly_snapshots, freeze_anomaly_alerts

        alert_match_ids = alert_active_match_ids(db)
        n_alerts_frozen = freeze_anomaly_alerts(db, alert_match_ids)
        n_alerts_evaluated = evaluate_anomaly_snapshots(db)
        report.add(
            "market_monitor_alert_snapshots", STEP_SUCCESS,
            f"{len(alert_match_ids)} match(es) scanned: {n_alerts_frozen} alert(s) newly frozen, {n_alerts_evaluated} evaluated",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("market_monitor_alert_snapshots", STEP_RECOVERABLE_FAILURE, f"alert snapshot freeze/evaluate failed: {exc}")

    # Step 11: venue-local weather forecast refresh (free, keyless Open-Meteo
    # — reuses refresh_weather_for_matches exactly as `refresh-weather` does).
    try:
        weather_report = refresh_weather_for_matches(db, upcoming_matches)
        report.weather_snapshots_added += weather_report.snapshots_created
        report.add(
            "refresh_weather", STEP_SUCCESS,
            f"{weather_report.snapshots_created} snapshot(s) created of {weather_report.matches_considered} match(es) considered",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("refresh_weather", STEP_RECOVERABLE_FAILURE, f"weather refresh failed: {exc}")

    from app.player_modelling.request_cache import clear_model_fit_cache, clear_ttl_cache

    clear_ttl_cache()
    clear_model_fit_cache()
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
        team_odds_quotes_added=report.team_odds_quotes_added,
        weather_snapshots_added=report.weather_snapshots_added,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
