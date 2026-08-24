"""One-off verification / report-data script for the Prospective Alert
Validation + Root-Cause Intelligence stage. Read-only except for the
freeze/settle writes case_snapshot_service.py itself owns. Reuses
build_trader_inbox exactly as the API does — no separate case-construction
path."""

from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.market_monitor.case_audit import audit_case
from app.market_monitor.case_snapshot_service import freeze_or_refresh_case_snapshots, settle_case_snapshots
from app.market_monitor.detector import matches_with_projections
from app.market_monitor.divergence_analysis import analyze_divergence_clusters
from app.market_monitor.inbox import build_trader_inbox
from app.market_monitor.priority import TIER_CRITICAL, TIER_HIGH_PRIORITY
from app.market_monitor.common import aware
from app.models import AnomalyCaseSnapshot, Match

db = SessionLocal()
try:
    match_ids = matches_with_projections(db)
    print(f"match_ids with projections: {len(match_ids)}")
    ranked = build_trader_inbox(db, match_ids, track_persistence=True, full_scan=True)
    print(f"ranked cases: {len(ranked)}")
    tier_counts = Counter(r.priority.tier for r in ranked)
    print("tier counts:", dict(tier_counts))

    n_new, n_refreshed = freeze_or_refresh_case_snapshots(db, ranked)
    print(f"freeze/refresh: new={n_new} refreshed={n_refreshed}")
    n_settled = settle_case_snapshots(db)
    print(f"settled this pass: {n_settled}")

    snaps = db.scalars(select(AnomalyCaseSnapshot)).all()
    print(f"total snapshots: {len(snaps)}  resolved: {sum(1 for s in snaps if s.resolved_at is not None)}")
    outcome_counter = Counter()
    for s in snaps:
        for code in s.outcome_codes or []:
            outcome_counter[code] += 1
    print("outcome code counts:", dict(outcome_counter))

    # future-info leakage sanity check: no snapshot should show a latest_quote timestamp after resolved_at
    bad = [s.case_id for s in snaps if s.resolved_at and s.latest_quote_at_freeze and aware(s.latest_quote_at_freeze) > aware(s.resolved_at)]
    print("leakage-check violations (should be empty):", bad)

    print("\n--- divergence clusters (item 6) ---")
    kickoff_by_match = {m.id: aware(m.scheduled_start) for m in db.scalars(select(Match)).all()}
    clusters = analyze_divergence_clusters([r.case for r in ranked], kickoff_by_match=kickoff_by_match)
    by_dim = defaultdict(list)
    for b in clusters:
        by_dim[b.dimension].append(b)
    for dim, buckets in by_dim.items():
        print(f"  {dim}:")
        for b in buckets:
            print(f"    {b.key}: n={b.n} mean_magnitude_pp={b.mean_magnitude_pp:.4f}")

    print("\n--- top 8 cases by priority ---")
    for r in ranked[:8]:
        c = r.case
        print(f"  [{r.priority.tier}] score={r.priority.total_score:.1f} {c.case_id} {c.player_name} {c.market_type} {c.threshold} model={c.primary_alert.model_probability} consensus={c.primary_alert.market_consensus_probability}")

    print("\n--- Matthew Kennedy audit ---")
    kennedy_case = next((r.case for r in ranked if r.case.player_name and "Kennedy" in r.case.player_name and r.case.threshold == 25.5), None)
    if kennedy_case is None:
        print("  NOT FOUND in current ranked cases (may have resolved/dropped tier since last check)")
    else:
        report = audit_case(db, kennedy_case, n_snapshots=1)
        print(f"  case_id={report.case_id}")
        print(f"  current_projection_expected={report.current_projection_expected}")
        print(f"  recent_form={report.recent_form}")
        print(f"  usage_regime={report.usage_regime} usage_change_score={report.usage_change_score} lineup_status={report.lineup_status}")
        print(f"  n_bookmakers={report.n_bookmakers} freshness={report.freshness}")
        print(f"  bookmaker_prices={report.bookmaker_prices}")
        print(f"  consensus_methodology={report.consensus_methodology}")
        print(f"  neighbouring_thresholds={report.neighbouring_thresholds}")
        print(f"  curve_is_monotonic={report.curve_is_monotonic} curve_has_jumps={report.curve_has_jumps}")
        print(f"  root_cause.most_plausible={report.root_cause.most_plausible}")
        print(f"  root_cause.plausible_causes={report.root_cause.plausible_causes}")
        print(f"  root_cause.evidence={report.root_cause.evidence}")
        print(f"  notes={report.notes}")

    print("\n--- other top-3 case audits (root cause only) ---")
    others = [r for r in ranked if r.priority.tier in (TIER_HIGH_PRIORITY, TIER_CRITICAL)][:4]
    for r in others:
        c = r.case
        if kennedy_case is not None and c.case_id == kennedy_case.case_id:
            continue
        rep = audit_case(db, c, n_snapshots=1)
        print(f"  {c.case_id} {c.player_name or c.team_id} {c.market_type} thr={c.threshold} model={c.primary_alert.model_probability:.3f} consensus={(c.primary_alert.market_consensus_probability or 0):.3f}")
        print(f"    root_cause={rep.root_cause.most_plausible} evidence={rep.root_cause.evidence}")
finally:
    db.close()
