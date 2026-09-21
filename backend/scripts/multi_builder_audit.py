"""Multi Builder evidence audit (read-only, reproducible).

    cd backend && PYTHONPATH=. python scripts/multi_builder_audit.py [--out docs/MULTI_BUILDER_AUDIT.md]

Reads the CONFIGURED database (DATABASE_URL) without writing to it. Every
section states which kind of evidence it is, because they must not be mixed:

  A. PROSPECTIVE placed / frozen multis - what the builder actually produced
     and what happened. Tiny samples; descriptive only.
  B. PROSPECTIVE leg calibration - frozen per-quote model probabilities
     against settled results, sliced by threshold, coverage, probability.
  C. RETROSPECTIVE calibration - the promoted models' 2019+ walk-forward
     predictions at thresholds the prospective data barely covers.
  D. REPLAY - the old and the new selection logic run over the same frozen,
     settled observations. Split chronologically into a DESIGN window (the
     matches examined one by one while the refinement was being designed) and a
     LATER window. The split limits look-ahead but does not remove it (an
     all-match aggregate was also viewed during development), and both windows
     are tiny and player-legs-only: replay shows what each rule would have
     chosen, it does NOT validate the rule or estimate future hit rate.

Near-miss statistics ("missed by one disposal") are research context. A leg
that finishes one short is a loss and is never reclassified as a near-win.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from io import StringIO

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match
from app.player_modelling import multi_builder as mb
from app.player_modelling import multi_builder_diagnostics as D

DESIGN_FRACTION = 0.6  # earliest 60% of replayable matches = design window


def _fmt(x, spec=".2f"):
    return "n/a" if x is None else format(x, spec)


class Report:
    def __init__(self) -> None:
        self.buf = StringIO()

    def h(self, level: int, text: str) -> None:
        self.buf.write(f"\n{'#' * level} {text}\n\n")

    def p(self, text: str = "") -> None:
        self.buf.write(text + "\n")

    def table(self, header: list[str], rows: list[list]) -> None:
        self.p("| " + " | ".join(header) + " |")
        self.p("|" + "|".join("---" for _ in header) + "|")
        for r in rows:
            self.p("| " + " | ".join(str(c) for c in r) + " |")
        self.p()


def multi_table(rep: Report, multis, key, key_name: str) -> None:
    summary = D.summarise_multis(multis, key)
    rows = [
        [k, v["n"], v["all_hit"], v["one_miss"], v["two_miss"], v["three_plus_miss"], _fmt(v["hit_rate"]), _fmt(v["avg_legs"], ".1f")]
        for k, v in summary.items()
    ]
    rep.table([key_name, "decided multis", "all hit", "1 miss", "2 miss", "3+ miss", "all-hit rate", "avg legs"], rows or [["-", 0, 0, 0, 0, 0, "n/a", "n/a"]])


def leg_table(rep: Report, legs, key, key_name: str) -> None:
    summary = D.summarise_legs(legs, key)
    rows = [
        [k, v["n"], _fmt(v["mean_model_probability"]), _fmt(v["hit_rate"]), _fmt(v["mean_shortfall_when_missed"], ".1f"), _fmt(v["max_shortfall"], ".0f")]
        for k, v in summary.items()
    ]
    rep.table([key_name, "settled legs", "mean model p", "realised", "mean shortfall when missed", "max shortfall"], rows or [["-", 0, "n/a", "n/a", "n/a", "n/a"]])


def section_prospective_multis(rep: Report, db) -> None:
    rep.h(2, "A. Prospective multis (placed and frozen SGM snapshots)")
    rep.p("Descriptive only: these are the real multis, but the samples are far too small to tune on.")
    for name, multis in (("Placed multis", D.load_placed_multis(db)), ("Frozen SGM combinations (closing snapshot)", D.load_sgm_multis(db))):
        rep.h(3, name)
        decided = [m for m in multis if D.classify_multi(m).pattern != D.PATTERN_NOT_DECIDED]
        rep.p(f"{len(multis)} multis, {len(decided)} decided.")
        if not decided:
            continue
        multi_table(rep, decided, lambda m: "all", "all")
        multi_table(rep, decided, lambda m: m.n_legs, "legs")
        multi_table(rep, decided, lambda m: m.tier or "unknown", "tier")
        multi_table(rep, decided, lambda m: m.mode or "unknown", "mode")
        rep.p("Distribution of shortfall on missed player-stat legs (1 = missed by one):")
        rep.p(f"`{D.shortfall_distribution(l for m in decided for l in m.legs)}`")
        rep.p()
        rep.p("Near-miss detail (one row per decided multi that lost):")
        rows = []
        for m in decided:
            s = D.near_miss_summary(m)
            if s and s["pattern"] != D.PATTERN_ALL_HIT:
                rows.append([m.multi_id, s["headline"], s["closest_miss"] if s["closest_miss"] is not None else "n/a",
                             "; ".join(f"{f['label']} (needed {f['needed']}, got {f['actual']})" for f in s["failed_legs"])])
        rep.table(["multi", "result", "closest miss", "failed legs"], rows or [["-", "-", "-", "-"]])


def section_prospective_legs(rep: Report, db) -> None:
    rep.h(2, "B. Prospective leg calibration (frozen model probability vs settled result)")
    legs = [l for l in D.load_prospective_legs(db) if l.is_player_stat_leg and l.model_probability is not None]
    settled = [l for l in legs if l.outcome in D.DECIDED_OUTCOMES]
    rep.p(f"{len(legs)} distinct (match, player, market, threshold) legs, {len(settled)} settled.")
    for market in ("player_disposals", "player_goals"):
        ml = [l for l in settled if l.market_type == market]
        if not ml:
            continue
        rep.h(3, market)
        leg_table(rep, ml, lambda l: D.prob_bucket_label(l.model_probability), "model probability bucket")
        leg_table(rep, ml, lambda l: f"{l.threshold:05.1f}", "threshold")
        leg_table(rep, ml, lambda l: "n/a" if l.coverage_share is None else ("main (>=50% of bookmakers)" if l.coverage_share >= mb.MAIN_MARKET_MIN_COVERAGE_SHARE else "thin (<50%)"), "coverage proxy")
        leg_table(rep, ml, lambda l: l.confidence_tier or "unknown", "confidence tier")
        leg_table(rep, ml, lambda l: l.lineup_status or "unknown", "lineup at observation")
    rep.p("Coverage is a proxy for how widely a line is OFFERED. It is not liquidity or popularity, and it says nothing about whether the model is more accurate on such lines.")


def section_retrospective(rep: Report, db) -> None:
    rep.h(2, "C. Retrospective calibration (2019+ walk-forward predictions)")
    rep.p("Independent of the prospective observations. The prospective sample rarely reaches 10+/15+ disposal lines, so this is where low thresholds are checked.")
    for name, fn in (("Disposals", D.retrospective_disposal_calibration), ("Goals", D.retrospective_goal_calibration)):
        result = fn(db)
        rep.h(3, name)
        if not result:
            rep.p("No promoted model run with persisted predictions in this database.")
            continue
        rep.p(f"Model `{result['model']}`, {result['n_predictions']} persisted predictions.")
        rows = [[k, v["n"], _fmt(v["mean_predicted"], ".3f"), _fmt(v["realised"], ".3f"), _fmt(v["mean_predicted"] - v["realised"], "+.3f")] for k, v in result["rows"].items()]
        rep.table(["threshold / projected volume", "n", "mean predicted", "realised", "predicted - realised"], rows)


def _summarise_replay(rep: Report, label: str, multis) -> dict:
    decided = [m for m in multis if D.classify_multi(m).pattern != D.PATTERN_NOT_DECIDED]
    legs = [l for m in multis for l in m.legs]
    tot = D.summarise_multis(multis, lambda m: "all").get("all")
    return {
        "label": label, "generated": len(multis), "decided": len(decided),
        "hit": tot["hit_rate"] if tot else None, "avg_legs": tot["avg_legs"] if tot else None,
        "one_miss": tot["one_miss"] if tot else 0, "two_miss": tot["two_miss"] if tot else 0, "three_plus": tot["three_plus_miss"] if tot else 0,
        "mean_price": statistics.mean(l.price for l in legs) if legs else None,
        "mean_p": statistics.mean(l.model_probability for l in legs) if legs else None,
        "low_disposal_legs": sum(1 for l in legs if l.market_type == "player_disposals" and l.threshold is not None and l.threshold <= 10.5),
        "n_legs": sum(1 for _ in legs),
        "legs_dist": sorted(Counter(m.n_legs for m in multis).items()),
    }


def section_replay(rep: Report, db) -> None:
    rep.h(2, "D. Replay: legacy vs refined selection over the same settled matches")
    matches = db.scalars(select(Match).where(Match.status == "completed").order_by(Match.scheduled_start)).all()
    ids = [m.id for m in matches if D.reconstruct_opportunities(db, m.id)[0]]
    cut = max(1, int(len(ids) * DESIGN_FRACTION)) if ids else 0
    windows = (("DESIGN window (examined while designing)", ids[:cut]), ("LATER window (not examined match-by-match while designing)", ids[cut:]))
    rep.p(
        f"{len(ids)} settled matches with frozen observations. Player legs only; confirmed lineups only; High Probability mode. "
        "Legacy = the previous objective (weakest-leg probability first, 16-leg pool, caps 5/6/7/8), reproduced in "
        "`multi_builder_diagnostics.legacy_hp_build`. Hit rates are descriptive - small, overlapping samples (options within a match share legs), "
        "not validation and not a forecast."
    )
    for title, window in windows:
        rep.h(3, f"{title}: {len(window)} matches")
        if not window:
            rep.p("No matches in this window.")
            continue
        rows = []
        for label, build, kw in (
            ("legacy", D.legacy_hp_build, {}),
            ("refined (main markets only, default)", mb.build_match_multis, {"main_markets_only": True}),
            ("refined (all markets allowed)", mb.build_match_multis, {"main_markets_only": False}),
        ):
            multis = D.replay_multis(db, window, build, modes=[mb.MODE_HIGH_PROBABILITY], confirmed_only=True, extra_kwargs=kw)
            s = _summarise_replay(rep, label, multis)
            rows.append([
                label, s["generated"], s["decided"], _fmt(s["avg_legs"], ".2f"), _fmt(s["mean_price"]), _fmt(s["mean_p"], ".3f"),
                s["low_disposal_legs"], f"{s['one_miss']}/{s['two_miss']}/{s['three_plus']}", _fmt(s["hit"]), s["legs_dist"],
            ])
        rep.table(
            ["selection", "multis", "decided", "avg legs", "mean leg price", "mean leg p", "disposal legs <=10.5", "1/2/3+ miss", "all-hit rate", "legs distribution"],
            rows,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="also write the markdown report to this path")
    parser.add_argument("--skip-replay", action="store_true", help="skip the (slower) legacy-vs-refined replay")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rep = Report()
        rep.h(1, "Multi Builder evidence audit")
        rep.p("Read-only. Prospective, retrospective and replay evidence are kept in separate sections and are never pooled.")
        section_prospective_multis(rep, db)
        section_prospective_legs(rep, db)
        section_retrospective(rep, db)
        if not args.skip_replay:
            section_replay(rep, db)
    finally:
        db.close()
    text = rep.buf.getvalue()
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
