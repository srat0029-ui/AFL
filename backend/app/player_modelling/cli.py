"""General player-modelling CLI — Section 20 of the live-projection brief.
Currently one subcommand: `project-upcoming`, which regenerates live
pre-match projections for the next upcoming round (see live_engine.py).
Kept as its own entry point (not folded into disposal_cli.py/goal_cli.py,
which run the much slower historical research backtests) so re-running it
before a round is fast and safe to do repeatedly.

Usage:
    python -m app.player_modelling.cli project-upcoming
"""

import sys

from app.database import SessionLocal
from app.player_modelling.live_engine import (
    ModelsUnavailableError,
    PromotedModelsUnavailableError,
    generate_live_projections,
)
from app.player_modelling.live_persistence import persist_projection_run
from app.player_modelling.upcoming_features import count_missing_lineup_candidates, load_all_lineup_player_ids


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
                "  No ExpectedLineup records found for these matches. Populate lineups first via "
                "POST/PUT /api/afl/matches/{match_id}/lineup (or the Expected Lineups UI) - this CLI "
                "never guesses who is playing."
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

        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "project-upcoming":
        print("Usage: python -m app.player_modelling.cli project-upcoming")
        return 2
    return _project_upcoming()


if __name__ == "__main__":
    sys.exit(main())
