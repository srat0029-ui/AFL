"""General player-modelling CLI. Two subcommands:

  project-upcoming  — regenerates live pre-match projections for the WHOLE
                       next upcoming round (see live_engine.py). Simple and
                       unconditional; good for a first run or after a big
                       change.
  refresh-live       — Sections 11-12 of the team-selection stage brief:
                       detects which matches actually changed since the
                       last run (lineup/model/team-context) and regenerates
                       only those, then runs sanity checks and reports a
                       summary. Idempotent — safe to run on a schedule
                       later without new infrastructure (Section 12), since
                       running it with nothing changed is a fast no-op.
  refresh-prop-odds  — Sections 9/27 of the automated-odds stage brief:
                       fetches current AFL player-prop odds from The Odds
                       API for the upcoming round, resolves events/players,
                       persists quote snapshots, AND (Section 17 of the
                       market-logging stage) freezes a PropMarketObservation
                       for each new/changed quote against the model's
                       current belief. Idempotent and quota-aware (see
                       prop_odds_quota.py) — safe to run on a schedule.
  refresh-team-odds  — Section 23 of the Weekly Opportunity Discovery
                       stage brief: fetches standard AFL match odds
                       (h2h/spreads/totals) from The Odds API's
                       non-event-specific endpoint — one call covers
                       every upcoming event, so this is considerably
                       cheaper than refresh-prop-odds — and persists
                       real automated OddsQuote snapshots
                       (source="the_odds_api") so Best Opportunities can
                       show genuine current team-market prices.
  settle-props       — Section 16 of the market-logging stage brief:
                       finds completed matches with unsettled real prop
                       observations, settles each against real
                       PlayerMatchStat results (won/lost/push/void/
                       unresolved), and reports a summary. Idempotent —
                       an already-settled observation is never re-touched.
                       Also settles any pending Placed Bets tracker legs
                       (app/player_modelling/placed_bets.py) the same way —
                       previously that only happened inside run-live-cycle,
                       so running this command standalone left placed bets
                       stuck pending indefinitely even once their results
                       were available.
  run-live-cycle     — Sections 1-3 of the live-operations stage brief: the
                       single command to run through an AFL round —
                       orchestrates fixtures -> player-stat updates ->
                       settle-props -> stale-projection regeneration ->
                       quota-aware, match-time-aware odds refresh ->
                       observation creation -> sanity checks -> a persisted
                       operational summary (see live_cycle.py). Every step
                       reuses one of the commands above; nothing here is a
                       new implementation. Exit code 0 = fully clean run,
                       1 = partial (some step recoverably failed but the
                       cycle still made progress), 2 = blocked (nothing at
                       all could be done - see live_cycle.py's docstring).

Kept as its own entry point (not folded into disposal_cli.py/goal_cli.py,
which run the much slower historical research backtests) so re-running it
before a round is fast and safe to do repeatedly.

Usage:
    python -m app.player_modelling.cli project-upcoming
    python -m app.player_modelling.cli refresh-live
    python -m app.player_modelling.cli refresh-prop-odds [--force] [--min-interval-minutes N]
    python -m app.player_modelling.cli refresh-team-odds
    python -m app.player_modelling.cli settle-props
    python -m app.player_modelling.cli run-live-cycle
"""

import sys
from datetime import timedelta

from app.config import get_settings
from app.database import SessionLocal
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.live_cycle import run_live_cycle
from app.player_modelling.scheduler import SchedulerAlreadyRunningError, is_paused, pause, resume, run_scheduler_loop
from app.player_modelling.live_engine import (
    ModelsUnavailableError,
    PromotedModelsUnavailableError,
    generate_live_projections,
)
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.live_sanity import confirmed_out_player_ids_by_match, run_all_sanity_checks
from app.player_modelling.placed_bets import settle_placed_bets
from app.player_modelling.prop_observation import ObservationCreationReport, create_observations_for_match
from app.player_modelling.prop_odds_ingestion import run_prop_odds_refresh
from app.player_modelling.prop_settlement import settle_all_completed_matches
from app.player_modelling.request_cache import clear_model_fit_cache, clear_ttl_cache
from app.player_modelling.team_odds_ingestion import ingest_team_odds
from app.player_modelling.upcoming_features import count_missing_lineup_candidates, load_all_lineup_player_ids, load_next_upcoming_round
from app.player_modelling.weather_ingestion import refresh_weather_for_matches
from app.providers.afl.the_odds_api import MODELLED_MARKET_KEYS, TheOddsApiError, TheOddsApiProvider


def _report_confidence_distributions(run) -> None:
    if run.disposal_projections:
        means = sorted(p.predicted_mean for p in run.disposal_projections)
        print(f"\nDisposal projection range: {means[0]:.1f} - {means[-1]:.1f} (median {means[len(means)//2]:.1f})")
    if run.goal_projections:
        means = sorted(p.predicted_mean for p in run.goal_projections)
        print(f"Goal projection range: {means[0]:.2f} - {means[-1]:.2f} (median {means[len(means)//2]:.2f})")

    confidence_counts: dict[str, int] = {}
    for p in run.disposal_projections:
        confidence_counts[p.confidence_tier] = confidence_counts.get(p.confidence_tier, 0) + 1
    if confidence_counts:
        print(f"\nDisposal confidence distribution: {confidence_counts}")
    confidence_counts_goals: dict[str, int] = {}
    for p in run.goal_projections:
        confidence_counts_goals[p.confidence_tier] = confidence_counts_goals.get(p.confidence_tier, 0) + 1
    if confidence_counts_goals:
        print(f"Goal confidence distribution: {confidence_counts_goals}")


def _report_sanity_checks(db, run) -> None:
    confirmed_out_by_match = confirmed_out_player_ids_by_match(db, [m.match_id for m in run.upcoming_matches])
    anomalies = run_all_sanity_checks(run, confirmed_out_by_match)
    if not anomalies:
        print("\nSanity checks: no anomalies found.")
        return
    print(f"\nSanity checks: {len(anomalies)} anomalies flagged:")
    for a in anomalies[:20]:
        print(f"  [{a.category}] player={a.player_id} match={a.match_id}: {a.detail}")
    if len(anomalies) > 20:
        print(f"  ... and {len(anomalies) - 20} more")


def _project_upcoming() -> int:
    db = SessionLocal()
    try:
        print("Identifying the next upcoming AFL round...")
        try:
            run = generate_live_projections(db)
        except PromotedModelsUnavailableError as exc:
            print(f"ERROR: {exc}")
            return 1
        except ModelsUnavailableError as exc:
            print(f"ERROR: {exc}")
            return 1

        if not run.upcoming_matches:
            print("No upcoming (scheduled) AFL matches found in the database — nothing to project.")
            return 0

        match_ids = [m.match_id for m in run.upcoming_matches]
        print(f"Upcoming matches ({len(run.upcoming_matches)}): match_ids={match_ids}")
        for m in run.upcoming_matches:
            print(f"  match {m.match_id}: round {m.round_number}, {m.season_year}, kickoff {m.scheduled_start.isoformat()}")

        n_expected = len(run.expected_players)
        print(f"\nExpected players (status in expected_in/uncertain): {n_expected}")
        if n_expected == 0:
            print(
                "  No ExpectedLineup records found for these matches. Populate lineups first via the bulk "
                "roster-suggestion + apply workflow (POST /api/afl/matches/{match_id}/lineup/suggested-roster, "
                "POST .../lineup/bulk-apply) or single-player PUT — this CLI never guesses who is playing."
            )

        all_lineup_player_ids = load_all_lineup_player_ids(db, match_ids)
        blocked = count_missing_lineup_candidates(db, run.upcoming_matches, all_lineup_player_ids)
        n_blocked = sum(blocked.values())
        if n_blocked:
            print(
                f"\nPlayers blocked by missing lineup information: {n_blocked} "
                f"(recently-featured players for these teams with no current ExpectedLineup record; not projected)."
            )
            for team_id, count in blocked.items():
                if count:
                    print(f"  team {team_id}: {count} candidate(s) with no lineup entry")

        print(f"\nGenerating projections (model version disposals={run.disposal_model_version!r}, goals={run.goal_model_version!r})...")
        n_disposals, n_goals = persist_projection_run(db, run)
        print(f"Persisted {n_disposals} disposal projections and {n_goals} goal projections.")

        _report_confidence_distributions(run)
        _report_sanity_checks(db, run)

        clear_ttl_cache()
        clear_model_fit_cache()
        return 0
    finally:
        db.close()


def _refresh_live() -> int:
    db = SessionLocal()
    try:
        print("Step 1: identifying upcoming AFL matches (assumes fixtures are already ingested via `python -m app.ingestion.cli`)...")
        upcoming_matches = load_next_upcoming_round(db)
        if not upcoming_matches:
            print("No upcoming (scheduled) AFL matches found — nothing to refresh.")
            return 0
        for m in upcoming_matches:
            print(f"  match {m.match_id}: round {m.round_number}, {m.season_year}, kickoff {m.scheduled_start.isoformat()}")

        print(
            "\nStep 2: reading current team-selection state (no reliable automated source exists yet — see the "
            "stage report's Section 1 source audit — this reads whatever is currently stored in ExpectedLineup, "
            "populated via the manual bulk-apply workflow or single-player edits)..."
        )

        print("\nStep 3: detecting which matches actually changed since the last projection run...")
        try:
            changed_match_ids = detect_matches_needing_regeneration(db, upcoming_matches)
        except ModelsUnavailableError as exc:
            print(f"ERROR: {exc}")
            return 1
        if not changed_match_ids:
            print("  No matches need regeneration — every persisted projection is already current. Nothing to do.")
            return 0
        print(f"  {len(changed_match_ids)} of {len(upcoming_matches)} matches need regeneration: {sorted(changed_match_ids)}")

        print("\nStep 4: regenerating projections for the changed matches only...")
        try:
            run = generate_live_projections(db, target_match_ids=changed_match_ids)
        except PromotedModelsUnavailableError as exc:
            print(f"ERROR: {exc}")
            return 1

        n_disposals, n_goals = persist_projection_run(db, run)
        print(f"  Persisted {n_disposals} disposal projections and {n_goals} goal projections.")

        print("\nStep 5: updating confidence/warnings summary and running sanity checks...")
        _report_confidence_distributions(run)
        _report_sanity_checks(db, run)

        print("\nStep 6: summary")
        print(f"  Matches regenerated: {sorted(changed_match_ids)}")
        print(f"  Matches unchanged (skipped): {sorted(set(m.match_id for m in upcoming_matches) - changed_match_ids)}")

        clear_ttl_cache()
        clear_model_fit_cache()
        return 0
    finally:
        db.close()


def _refresh_prop_odds(force: bool = False, min_interval_minutes: float | None = None) -> int:
    db = SessionLocal()
    try:
        print("Step 1: identifying upcoming AFL matches...")
        upcoming_matches = load_next_upcoming_round(db)
        if not upcoming_matches:
            print("No upcoming (scheduled) AFL matches found — nothing to refresh.")
            return 0
        for m in upcoming_matches:
            print(f"  match {m.match_id}: round {m.round_number}, {m.season_year}, kickoff {m.scheduled_start.isoformat()}")

        settings = get_settings()
        provider = TheOddsApiProvider(api_key=settings.the_odds_api_key)
        if not provider.is_available:
            print(
                "\nProvider unavailable: THE_ODDS_API_KEY is not configured. Manual prop entry is unaffected — "
                "see .env.example for setup. Nothing was fetched."
            )
            return 0

        print("\nStep 2: listing current AFL events from The Odds API (free — does not use quota)...")
        if min_interval_minutes is not None:
            min_interval: timedelta | None = timedelta(minutes=min_interval_minutes)
            print(f"  using flat override interval: {min_interval}")
        else:
            min_interval = None
            print("  using match-time-aware refresh policy (more frequent as kickoff approaches — see prop_odds_quota.py)")
        report = run_prop_odds_refresh(
            db, provider, upcoming_matches, MODELLED_MARKET_KEYS, min_refresh_interval=min_interval, force=force
        )

        print(f"  {report.events_seen} AFL events returned by the provider.")
        print("\nStep 3: resolving events to matches, fetching quotes for matches due a refresh...")
        print(f"  matches refreshed (quota spent): {report.matches_resolved}")
        skip_label = f"fresh within {min_interval}" if min_interval is not None else "fresh per the match-time-aware policy"
        print(f"  matches skipped ({skip_label}): {report.matches_skipped_fresh}")
        if report.matches_unresolved:
            print(f"  {len(report.matches_unresolved)} event(s) could not be resolved to a match:")
            for msg in report.matches_unresolved[:10]:
                print(f"    {msg}")
            if len(report.matches_unresolved) > 10:
                print(f"    ... and {len(report.matches_unresolved) - 10} more")

        print(f"\nStep 4: quotes — {report.quotes_seen} seen, {report.quotes_created} new snapshots, {report.quotes_unchanged} unchanged (idempotent)")
        if report.unsupported_markets:
            unique_reasons = sorted(set(report.unsupported_markets))
            print(f"  {len(report.unsupported_markets)} quote(s) skipped — unsupported market/selection ({len(unique_reasons)} distinct reasons):")
            for msg in unique_reasons[:10]:
                print(f"    {msg}")
        if report.unresolved_players:
            print(f"  {len(report.unresolved_players)} quote(s) skipped — player name unresolved:")
            for msg in report.unresolved_players[:10]:
                print(f"    {msg}")
            if len(report.unresolved_players) > 10:
                print(f"    ... and {len(report.unresolved_players) - 10} more")
        if report.ambiguous_players:
            print(f"  {len(report.ambiguous_players)} quote(s) skipped — player name ambiguous:")
            for msg in report.ambiguous_players[:10]:
                print(f"    {msg}")

        print("\nStep 5: API quota usage")
        if report.last_quota is not None:
            print(
                f"  requests_used={report.last_quota.requests_used} "
                f"requests_remaining={report.last_quota.requests_remaining} "
                f"last_request_cost={report.last_quota.last_request_cost}"
            )
        else:
            print("  no request was made this run (nothing due a refresh).")

        print("\nStep 6: creating model-market observations (freezing quote + model snapshot pairs)...")
        obs_report = ObservationCreationReport()
        for m in upcoming_matches:
            match_report = create_observations_for_match(db, m.match_id)
            obs_report.quotes_considered += match_report.quotes_considered
            obs_report.observations_created += match_report.observations_created
            obs_report.observations_unchanged += match_report.observations_unchanged
            obs_report.skipped_manual_source += match_report.skipped_manual_source
            obs_report.skipped_complementary_side += match_report.skipped_complementary_side
            obs_report.skipped_no_projection += match_report.skipped_no_projection
            obs_report.skipped_confirmed_out += match_report.skipped_confirmed_out
            obs_report.skipped_unsupported_market += match_report.skipped_unsupported_market
        print(
            f"  {obs_report.observations_created} new observations, {obs_report.observations_unchanged} unchanged (idempotent) "
            f"of {obs_report.quotes_considered} quotes considered"
        )
        if obs_report.skipped_no_projection:
            print(f"  {obs_report.skipped_no_projection} quote(s) skipped — no live projection for this player yet")

        clear_ttl_cache()
        clear_model_fit_cache()
        return 0
    finally:
        db.close()


def _refresh_team_odds() -> int:
    """Section 23 of the Weekly Opportunity Discovery stage: fetches
    standard AFL match odds (h2h/spreads/totals) in ONE call — cheaper
    than player props since it's not event-specific — and persists real
    automated OddsQuote snapshots (source="the_odds_api"), so Best
    Opportunities can show genuine current team-market prices instead of
    stale manual entries (Section 22)."""
    db = SessionLocal()
    try:
        settings = get_settings()
        provider = TheOddsApiProvider(api_key=settings.the_odds_api_key)
        if not provider.is_available:
            print(
                "Provider unavailable: THE_ODDS_API_KEY is not configured. Manual odds entry is unaffected — "
                "see .env.example for setup. Nothing was fetched."
            )
            return 0

        print("Fetching standard AFL match odds (h2h/spreads/totals) — one call, not event-specific...")
        try:
            result = provider.get_standard_match_odds("AFL")
        except TheOddsApiError as exc:
            print(f"Request failed: {exc}")
            return 1

        print(f"  {len(result.events)} AFL event(s) returned, markets returned: {result.markets_returned}")
        report = ingest_team_odds(db, result)

        print(f"\nMatches resolved: {report.matches_resolved} of {report.events_seen} events")
        if report.matches_unresolved:
            print(f"  {len(report.matches_unresolved)} event(s) could not be resolved to a match:")
            for msg in report.matches_unresolved[:10]:
                print(f"    {msg}")

        print(f"\nQuotes: {report.quotes_seen} seen, {report.quotes_created} new snapshots, {report.quotes_unchanged} unchanged (idempotent)")
        if report.unsupported_markets:
            print(f"  unsupported market keys skipped: {report.unsupported_markets}")
        if report.unresolved_selections:
            print(f"  {len(report.unresolved_selections)} quote(s) skipped — team name unresolved:")
            for msg in report.unresolved_selections[:10]:
                print(f"    {msg}")

        print(
            f"\nAPI quota usage: requests_used={result.quota.requests_used} "
            f"requests_remaining={result.quota.requests_remaining} last_request_cost={result.quota.last_request_cost}"
        )
        clear_ttl_cache()
        clear_model_fit_cache()
        return 0
    finally:
        db.close()


def _settle_props() -> int:
    db = SessionLocal()
    try:
        print("Step 1: finding completed matches with unsettled real prop observations...")
        report = settle_all_completed_matches(db)
        if report.matches_considered == 0:
            print("  No completed matches have unsettled observations — nothing to settle.")
        else:
            print(f"  {report.matches_considered} match(es) considered.")

            print("\nStep 2: settlement results")
            print(f"  settled: {report.observations_settled} (already settled, skipped: {report.already_settled_skipped})")
            print(
                f"  won={report.observations_won} lost={report.observations_lost} push={report.observations_pushed} "
                f"void={report.observations_voided} unresolved={report.observations_unresolved}"
            )
            if report.observations_voided:
                print(f"  {report.observations_voided} observation(s) voided — match complete, other players' stats present, but this player has none (DNP).")
            if report.awaiting_player_stats:
                print(f"  {report.awaiting_player_stats} observation(s) left pending — match complete but no player-match stats ingested for it yet (will retry next cycle).")
            if report.observations_flagged_for_review:
                print(f"  {report.observations_flagged_for_review} observation(s) flagged for manual review — implausible actual stat value.")

        # Step 3: Placed Bets (Section 3 of the settlement reliability audit
        # — this used to only run as part of run-live-cycle, so anyone who
        # only ever ran settle-props standalone would see prop observations
        # settle correctly while their own placed bets stayed pending
        # forever). Independent of Step 1/2 above — a match can have placed
        # bets with no matching prop observation (e.g. a manually-entered
        # or best_value bet) or vice versa.
        print("\nStep 3: settling placed bets (Placed Bets tracker)...")
        bet_report = settle_placed_bets(db)
        print(
            f"  checked {bet_report.legs_checked} pending leg(s): settled={bet_report.bets_settled} "
            f"(won={bet_report.bets_won} lost={bet_report.bets_lost} push={bet_report.bets_pushed} void={bet_report.bets_voided})"
        )
        if bet_report.legs_repaired:
            print(f"  {bet_report.legs_repaired} leg(s) had missing threshold/line_type recovered from market history before settling.")
        if bet_report.matches_awaiting_result:
            print(f"  {bet_report.matches_awaiting_result} leg(s) waiting on their match to complete.")
        if bet_report.matches_awaiting_player_stats:
            print(f"  {bet_report.matches_awaiting_player_stats} leg(s) waiting on player stats for a completed match.")
        if bet_report.settlement_failures:
            print(f"  {bet_report.settlement_failures} leg(s) flagged as settlement failures — needs investigation, see logs.")
        if bet_report.multis_settled:
            print(f"  {bet_report.multis_settled} multi(s) newly settled (won={bet_report.multis_won} lost={bet_report.multis_lost} void={bet_report.multis_voided}).")

        return 0
    finally:
        db.close()


def _run_live_cycle() -> int:
    db = SessionLocal()
    try:
        run = run_live_cycle(db)
        print(f"Live cycle run {run.id} — overall status: {run.overall_status.upper()}\n")
        for step in run.steps:
            print(f"  [{step['status']:>18}] {step['step']}: {step['detail']}")
        print("\nSummary:")
        print(f"  matches affected: {run.matches_affected}")
        print(f"  quotes added: {run.quotes_added}")
        print(f"  observations added: {run.observations_added}")
        print(f"  observations settled: {run.observations_settled}")
        print(f"  team odds quotes added: {run.team_odds_quotes_added}")
        print(f"  weather snapshots added: {run.weather_snapshots_added}")
        if run.odds_credits_consumed is not None:
            print(f"  odds API: requests_used={run.odds_credits_consumed} requests_remaining={run.odds_credits_remaining}")
        exit_code = {"ok": 0, "partial": 1, "blocked": 2}[run.overall_status]
        return exit_code
    finally:
        db.close()


def _refresh_weather() -> int:
    db = SessionLocal()
    try:
        print("Step 1: identifying upcoming AFL matches...")
        upcoming_matches = load_next_upcoming_round(db)
        if not upcoming_matches:
            print("No upcoming (scheduled) AFL matches found — nothing to refresh.")
            return 0
        for m in upcoming_matches:
            print(f"  match {m.match_id}: round {m.round_number}, {m.season_year}, kickoff {m.scheduled_start.isoformat()}")

        print("\nStep 2: fetching venue-local forecasts from Open-Meteo (free, keyless)...")
        report = refresh_weather_for_matches(db, upcoming_matches)
        print(f"  {report.snapshots_created} snapshot(s) created of {report.matches_considered} match(es) considered")
        if report.skipped_no_venue:
            print(f"  skipped (no venue set): {report.skipped_no_venue}")
        if report.skipped_no_coordinates:
            print(f"  skipped (venue has no lat/lon on record): {report.skipped_no_coordinates}")
        if report.skipped_too_far_out:
            print(f"  skipped (kickoff beyond Open-Meteo's ~16-day forecast window): {report.skipped_too_far_out}")
        if report.errors:
            print(f"  errors: {report.errors}")
        return 0
    finally:
        db.close()


def _run_scheduler(argv: list[str]) -> int:
    interval_minutes: float | None = None
    if "--interval-minutes" in argv:
        idx = argv.index("--interval-minutes")
        interval_minutes = float(argv[idx + 1])
    try:
        return run_scheduler_loop(interval_minutes=interval_minutes)
    except SchedulerAlreadyRunningError as exc:
        print(str(exc))
        return 1
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
        return 0


def _pause_scheduler() -> int:
    pause()
    print("Scheduler paused — a running instance will skip ticks until you run `resume-scheduler`.")
    return 0


def _resume_scheduler() -> int:
    resume()
    print("Scheduler resumed." if not is_paused() else "Scheduler was not paused.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in (
        "project-upcoming", "refresh-live", "refresh-prop-odds", "refresh-team-odds", "settle-props", "run-live-cycle",
        "refresh-weather", "run-scheduler", "pause-scheduler", "resume-scheduler",
    ):
        print(
            "Usage: python -m app.player_modelling.cli "
            "project-upcoming|refresh-live|refresh-prop-odds [--force] [--min-interval-minutes N]|"
            "refresh-team-odds|settle-props|run-live-cycle|refresh-weather|"
            "run-scheduler [--interval-minutes N]|pause-scheduler|resume-scheduler"
        )
        return 2
    if argv[0] == "project-upcoming":
        return _project_upcoming()
    if argv[0] == "refresh-live":
        return _refresh_live()
    if argv[0] == "refresh-team-odds":
        return _refresh_team_odds()
    if argv[0] == "settle-props":
        return _settle_props()
    if argv[0] == "run-live-cycle":
        return _run_live_cycle()
    if argv[0] == "refresh-weather":
        return _refresh_weather()
    if argv[0] == "run-scheduler":
        return _run_scheduler(argv[1:])
    if argv[0] == "pause-scheduler":
        return _pause_scheduler()
    if argv[0] == "resume-scheduler":
        return _resume_scheduler()

    force = "--force" in argv
    min_interval_minutes: float | None = None
    if "--min-interval-minutes" in argv:
        idx = argv.index("--min-interval-minutes")
        min_interval_minutes = float(argv[idx + 1])
    return _refresh_prop_odds(force=force, min_interval_minutes=min_interval_minutes)


if __name__ == "__main__":
    sys.exit(main())
