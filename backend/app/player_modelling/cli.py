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
                       and persists quote snapshots. Idempotent and quota-
                       aware (see prop_odds_quota.py) — safe to run on a
                       schedule later without changing this command.

Kept as its own entry point (not folded into disposal_cli.py/goal_cli.py,
which run the much slower historical research backtests) so re-running it
before a round is fast and safe to do repeatedly.

Usage:
    python -m app.player_modelling.cli project-upcoming
    python -m app.player_modelling.cli refresh-live
    python -m app.player_modelling.cli refresh-prop-odds [--force] [--min-interval-minutes N]
"""

import sys
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ExpectedLineup, SelectionStatus
from app.player_modelling.live_change_detection import detect_matches_needing_regeneration
from app.player_modelling.live_engine import (
    ModelsUnavailableError,
    PromotedModelsUnavailableError,
    generate_live_projections,
)
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.live_sanity import run_all_sanity_checks
from app.player_modelling.prop_odds_ingestion import run_prop_odds_refresh
from app.player_modelling.prop_odds_quota import DEFAULT_MIN_REFRESH_INTERVAL
from app.player_modelling.upcoming_features import count_missing_lineup_candidates, load_all_lineup_player_ids, load_next_upcoming_round
from app.providers.afl.the_odds_api import MODELLED_MARKET_KEYS, TheOddsApiProvider


def _confirmed_out_player_ids_by_match(db, match_ids: list[int]) -> dict[int, set[int]]:
    if not match_ids:
        return {}
    rows = db.scalars(
        select(ExpectedLineup).where(ExpectedLineup.match_id.in_(match_ids), ExpectedLineup.selection_status == SelectionStatus.CONFIRMED_OUT.value)
    ).all()
    result: dict[int, set[int]] = {}
    for r in rows:
        result.setdefault(r.match_id, set()).add(r.player_id)
    return result


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
    confirmed_out_by_match = _confirmed_out_player_ids_by_match(db, [m.match_id for m in run.upcoming_matches])
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
        min_interval = DEFAULT_MIN_REFRESH_INTERVAL if min_interval_minutes is None else timedelta(minutes=min_interval_minutes)
        report = run_prop_odds_refresh(
            db, provider, upcoming_matches, MODELLED_MARKET_KEYS, min_refresh_interval=min_interval, force=force
        )

        print(f"  {report.events_seen} AFL events returned by the provider.")
        print(f"\nStep 3: resolving events to matches, fetching quotes for matches due a refresh...")
        print(f"  matches refreshed (quota spent): {report.matches_resolved}")
        print(f"  matches skipped (fresh within {min_interval}): {report.matches_skipped_fresh}")
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

        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("project-upcoming", "refresh-live", "refresh-prop-odds"):
        print("Usage: python -m app.player_modelling.cli project-upcoming|refresh-live|refresh-prop-odds [--force] [--min-interval-minutes N]")
        return 2
    if argv[0] == "project-upcoming":
        return _project_upcoming()
    if argv[0] == "refresh-live":
        return _refresh_live()

    force = "--force" in argv
    min_interval_minutes: float | None = None
    if "--min-interval-minutes" in argv:
        idx = argv.index("--min-interval-minutes")
        min_interval_minutes = float(argv[idx + 1])
    return _refresh_prop_odds(force=force, min_interval_minutes=min_interval_minutes)


if __name__ == "__main__":
    sys.exit(main())
