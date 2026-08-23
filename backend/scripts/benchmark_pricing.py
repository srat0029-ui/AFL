"""Benchmark script (B2B Demo + Integration Readiness stage, item 6):
times single-match pricing, full-round pricing, and an arbitrary
player-threshold query — all in-process, against the real current DB
state, using exactly the same functions the API routes call (no HTTP
round-trip, so this isolates computation cost from transport/dev-server
overhead — see docs/API_USAGE.md's "expected response times" section for
why that distinction matters).

Read-only. Run: python -m scripts.benchmark_pricing
"""

import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.edges.calculator import ModelsUnavailableError, build_model_context
from app.models import Match, PlayerDisposalProjection
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.pricing.player_pricing import price_disposals
from app.pricing.round_pricing import price_current_round
from app.pricing.team_pricing import latest_completed_match_timestamp, price_team_market


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

        print("Single-match pricing (one team market computation):")
        _time("price_team_market (cold context)", lambda: price_team_market(match, context, now, cutoff))
        _time("price_team_market (warm context)", lambda: price_team_market(match, context, now, cutoff))

        print("\nFull-round pricing:")
        _time("price_current_round (no_cache=True)", lambda: price_current_round(db, use_cache=False))
        _time("price_current_round (cache miss, first use_cache=True)", lambda: price_current_round(db, use_cache=True))
        _time("price_current_round (cache hit)", lambda: price_current_round(db, use_cache=True))

        print("\nArbitrary player-threshold query:")
        row = db.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match.id))
        if row is not None:
            _time("price_disposals (preset thresholds)", lambda: price_disposals(db, row))
            _time("price_disposals (+1 arbitrary threshold)", lambda: price_disposals(db, row, extra_thresholds=[27.5]))
        else:
            print("  (no persisted disposal projection for this match to benchmark against)")

        print("\nNote: these figures isolate in-process computation only — see docs/API_USAGE.md")
        print("for the distinction between this and observed HTTP round-trip time (dev-server overhead).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
