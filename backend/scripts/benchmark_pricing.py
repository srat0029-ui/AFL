"""Benchmark script (B2B Demo + Integration Readiness stage, item 6;
extended for API Productisation & Operational Hardening, item 14): times
single-match pricing, full-round pricing, an arbitrary player-threshold
query, and SGM Monte Carlo pricing — all in-process, against the real
current DB state, using exactly the same functions the API routes call (no
HTTP round-trip, so this isolates computation cost from transport/dev-
server overhead — see docs/API_USAGE.md's "expected response times"
section for why that distinction matters).

Every benchmark below runs N_REPS times and reports p50/p95, not a single
timing — a single measurement can't distinguish real cost from one-off
system noise (a GC pause, disk cache miss, etc.).

Read-only. Run: python -m scripts.benchmark_pricing
"""

import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.edges.calculator import ModelsUnavailableError, build_model_context
from app.models import Match, PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.pricing.player_pricing import price_disposals
from app.pricing.round_pricing import price_current_round
from app.pricing.same_game_pricing import SgmLegRequest, SgmValidationError, price_same_game_multi
from app.pricing.team_pricing import latest_completed_match_timestamp, price_team_market

N_REPS = 20


def _percentile(sorted_values: list[float], p: float) -> float:
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


def _time_reps(label: str, fn, n_reps: int = N_REPS) -> None:
    samples = []
    for _ in range(n_reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    print(f"  {label:<42} p50={_percentile(samples, 0.50):>8.1f}ms  p95={_percentile(samples, 0.95):>8.1f}ms  (n={n_reps})")


def _time(label: str, fn) -> float:
    t0 = time.perf_counter()
    fn()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  {label:<38} {elapsed_ms:>9.1f} ms")
    return elapsed_ms


def main() -> int:
    db = SessionLocal()
    try:
        upcoming = load_next_upcoming_round(db)
        if not upcoming:
            print("No upcoming round found — nothing to benchmark against.")
            return 0
        match = db.get(Match, upcoming[0].match_id)

        try:
            context = build_model_context(db)
        except ModelsUnavailableError as exc:
            print(f"Team models unavailable: {exc}")
            return 1
        now = datetime.now(timezone.utc)
        cutoff = latest_completed_match_timestamp(db) or now

        print(f"Single-match pricing (one team market computation, {N_REPS} reps):")
        _time_reps("price_team_market (warm context)", lambda: price_team_market(match, context, now, cutoff))

        print("\nFull-round pricing (single-shot, not repeated — a cache-hit rep would misrepresent the cache-miss cost):")
        _time("price_current_round (no_cache=True)", lambda: price_current_round(db, use_cache=False))
        _time("price_current_round (cache miss, first use_cache=True)", lambda: price_current_round(db, use_cache=True))
        _time("price_current_round (cache hit)", lambda: price_current_round(db, use_cache=True))

        print(f"\nArbitrary player-threshold query ({N_REPS} reps):")
        disposal_row = db.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match.id))
        if disposal_row is not None:
            _time_reps("price_disposals (preset thresholds)", lambda: price_disposals(db, disposal_row))
            _time_reps("price_disposals (+1 arbitrary threshold)", lambda: price_disposals(db, disposal_row, extra_thresholds=[27.5]))
        else:
            print("  (no persisted disposal projection for this match to benchmark against)")

        print(f"\nSame Game Multi (Monte Carlo) pricing ({N_REPS} reps):")
        goal_row = db.scalar(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match.id))
        if disposal_row is not None:
            home_leg = SgmLegRequest(leg_type="h2h", team_id=match.home_team_id)
            disposal_leg = SgmLegRequest(leg_type="disposals", player_id=disposal_row.player_id, threshold=round(disposal_row.predicted_mean) - 0.5)
            try:
                _time_reps(
                    "price_same_game_multi (2 legs, default 100k sims)",
                    lambda: price_same_game_multi(db, match.id, [home_leg, disposal_leg]),
                )
                if goal_row is not None:
                    goal_leg = SgmLegRequest(leg_type="goals", player_id=goal_row.player_id, threshold=0.5)
                    _time_reps(
                        "price_same_game_multi (3 legs, default 100k sims)",
                        lambda: price_same_game_multi(db, match.id, [home_leg, disposal_leg, goal_leg]),
                    )
                _time_reps(
                    "price_same_game_multi (2 legs, 200k sims - the API's max)",
                    lambda: price_same_game_multi(db, match.id, [home_leg, disposal_leg], n_simulations=200_000),
                )
            except SgmValidationError as exc:
                print(f"  (could not construct a valid SGM combo against this match's real data: {exc})")
        else:
            print("  (no persisted disposal projection for this match to benchmark an SGM leg against)")

        print("\nNote: these figures isolate in-process computation only — see docs/API_USAGE.md")
        print("for the distinction between this and observed HTTP round-trip time (dev-server overhead).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
