"""Read-only diagnostics for the Multi Builder: what actually happened to the
multis (and legs) this application has generated, frozen, or had placed.

Three kinds of evidence are deliberately kept SEPARATE and never blended into
one number (see scripts/multi_builder_audit.py for the report that uses them):

  * "placed"  - PlacedBet rows sharing a multi_group_id: multis a person
    actually recorded. Prospective. Tiny sample.
  * "sgm"     - SgmPriceSnapshot rows: combos the Multi Builder's own search
    surfaced and the pricing engine froze before kickoff. Prospective. Small.
  * "replay"  - the CURRENT selection code re-run over prospectively frozen
    PropMarketObservation rows for already-completed matches. The multis are
    hypothetical (the builder's live output is not persisted), the leg
    outcomes are real. This is a diagnostic for comparing selection logic on
    identical markets - it is NOT out-of-sample validation of that logic,
    because the design was informed by looking at this same data.

Nothing here writes to the database.

Near-miss information is research context only: a leg that lost by one
disposal is a LOSS. Nothing here reclassifies a near miss as a win.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bookmaker, Match, PlacedBet, Player, PlayerDisposalPrediction, PlayerMatchStat, PlayerModelRun,
    PropMarketObservation, SgmPriceSnapshot,
)
from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.models.sgm_price_snapshot import SNAPSHOT_HORIZONS

OUTCOME_WON = "won"
OUTCOME_LOST = "lost"
DECIDED_OUTCOMES = (OUTCOME_WON, OUTCOME_LOST)

PATTERN_ALL_HIT = "all_hit"
PATTERN_ONE_MISS = "one_miss"
PATTERN_TWO_MISS = "two_miss"
PATTERN_THREE_PLUS_MISS = "three_plus_miss"
PATTERN_NOT_DECIDED = "not_decided"
PATTERN_ORDER = (PATTERN_ALL_HIT, PATTERN_ONE_MISS, PATTERN_TWO_MISS, PATTERN_THREE_PLUS_MISS)

PROB_BUCKET_EDGES = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0001)


@dataclass(frozen=True)
class LegResult:
    label: str
    market_type: str  # "player_disposals" | "player_goals" | "h2h" | "total" | ...
    outcome: str  # won | lost | void | push | pending
    player_id: int | None = None
    threshold: float | None = None
    line_type: str | None = None
    model_probability: float | None = None
    price: float | None = None
    actual_value: float | None = None
    confidence_tier: str | None = None
    lineup_status: str | None = None
    n_bookmakers: int | None = None
    coverage_share: float | None = None
    predicted_mean: float | None = None
    model_risk_flagged: bool | None = None

    @property
    def is_player_stat_leg(self) -> bool:
        return self.market_type in ("player_disposals", "player_goals", "disposals", "goals")

    @property
    def needed_value(self) -> int | None:
        return needed_value(self.threshold, self.line_type)

    @property
    def shortfall(self) -> float | None:
        return leg_shortfall(self)


@dataclass(frozen=True)
class MultiResult:
    source: str  # "placed" | "sgm" | "replay"
    multi_id: str
    legs: tuple[LegResult, ...]
    match_id: int | None = None
    tier: str | None = None
    mode: str | None = None
    indicative_odds: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def n_legs(self) -> int:
        return len(self.legs)


# --------------------------------------------------------------------------
# Near-miss primitives
# --------------------------------------------------------------------------

def needed_value(threshold: float | None, line_type: str | None) -> int | None:
    """The smallest whole-number stat that satisfies the leg: "9.5+" needs 10,
    "1+ goals" (multi_plus, threshold 1.0) needs 1."""
    if threshold is None:
        return None
    if line_type == "multi_plus":
        return math.ceil(threshold)
    return math.floor(threshold) + 1


def leg_shortfall(leg: LegResult) -> float | None:
    """How far below the requirement a LOST player-stat leg finished (always
    >= 1). None for anything that isn't a decided loss with a known actual."""
    if leg.outcome != OUTCOME_LOST or leg.actual_value is None:
        return None
    needed = leg.needed_value
    if needed is None:
        return None
    return float(needed) - float(leg.actual_value)


@dataclass(frozen=True)
class MultiClassification:
    pattern: str
    n_decided: int
    n_won: int
    n_lost: int
    n_void: int
    failed_legs: tuple[LegResult, ...]
    closest_miss: float | None  # smallest shortfall among failed player-stat legs


def classify_multi(multi: MultiResult) -> MultiClassification:
    """Void/push legs are ignored (a bookmaker settles them as if removed).
    A multi with any pending leg and no lost leg is not yet decided; a multi
    with a lost leg is already lost regardless of what is still pending."""
    won = [lg for lg in multi.legs if lg.outcome == OUTCOME_WON]
    lost = [lg for lg in multi.legs if lg.outcome == OUTCOME_LOST]
    void = [lg for lg in multi.legs if lg.outcome in ("void", "push")]
    pending = [lg for lg in multi.legs if lg.outcome not in (OUTCOME_WON, OUTCOME_LOST, "void", "push")]
    shortfalls = [s for s in (leg_shortfall(lg) for lg in lost) if s is not None]
    closest = min(shortfalls) if shortfalls else None
    if pending and not lost:
        pattern = PATTERN_NOT_DECIDED
    elif not won and not lost:
        pattern = PATTERN_NOT_DECIDED
    elif not lost:
        pattern = PATTERN_ALL_HIT
    elif len(lost) == 1:
        pattern = PATTERN_ONE_MISS
    elif len(lost) == 2:
        pattern = PATTERN_TWO_MISS
    else:
        pattern = PATTERN_THREE_PLUS_MISS
    return MultiClassification(
        pattern=pattern, n_decided=len(won) + len(lost), n_won=len(won), n_lost=len(lost), n_void=len(void),
        failed_legs=tuple(lost), closest_miss=closest,
    )


def near_miss_summary(multi: MultiResult) -> dict | None:
    """Post-settlement review line for one multi, e.g. "4 of 5 legs hit" and
    the closest miss. None while the multi isn't decided."""
    c = classify_multi(multi)
    if c.pattern == PATTERN_NOT_DECIDED:
        return None
    return {
        "n_legs_decided": c.n_decided,
        "n_legs_hit": c.n_won,
        "n_legs_missed": c.n_lost,
        "headline": f"{c.n_won} of {c.n_decided} legs hit",
        "pattern": c.pattern,
        "closest_miss": c.closest_miss,
        "failed_legs": [
            {"label": lg.label, "threshold": lg.threshold, "actual": lg.actual_value, "needed": lg.needed_value, "shortfall": lg.shortfall}
            for lg in c.failed_legs
        ],
    }


# --------------------------------------------------------------------------
# Aggregation helpers (pure)
# --------------------------------------------------------------------------

def prob_bucket_label(p: float | None) -> str:
    if p is None:
        return "unknown"
    for lo, hi in zip(PROB_BUCKET_EDGES, PROB_BUCKET_EDGES[1:]):
        if lo <= p < hi:
            return f"{lo:.2f}-{min(hi, 1.0):.2f}"
    return "unknown"


def summarise_multis(multis: Iterable[MultiResult], key: Callable[[MultiResult], object]) -> dict:
    """Outcome-pattern table grouped by `key`. Only decided multis count."""
    groups: dict[object, dict] = defaultdict(lambda: {"n": 0, **{p: 0 for p in PATTERN_ORDER}, "legs": 0})
    for m in multis:
        c = classify_multi(m)
        if c.pattern == PATTERN_NOT_DECIDED:
            continue
        g = groups[key(m)]
        g["n"] += 1
        g[c.pattern] += 1
        g["legs"] += c.n_decided
    out = {}
    for k, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
        n = g["n"]
        out[k] = {**g, "hit_rate": g[PATTERN_ALL_HIT] / n if n else None, "avg_legs": g["legs"] / n if n else None}
    return out


def summarise_legs(legs: Iterable[LegResult], key: Callable[[LegResult], object]) -> dict:
    """Leg-level calibration table grouped by `key`: predicted vs realised."""
    groups: dict[object, dict] = defaultdict(lambda: {"n": 0, "won": 0, "p_sum": 0.0, "p_n": 0, "short_sum": 0.0, "short_n": 0, "short_max": 0.0})
    for lg in legs:
        if lg.outcome not in DECIDED_OUTCOMES:
            continue
        g = groups[key(lg)]
        g["n"] += 1
        g["won"] += 1 if lg.outcome == OUTCOME_WON else 0
        if lg.model_probability is not None:
            g["p_sum"] += lg.model_probability
            g["p_n"] += 1
        s = lg.shortfall
        if s is not None:
            g["short_sum"] += s
            g["short_n"] += 1
            g["short_max"] = max(g["short_max"], s)
    out = {}
    for k, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
        out[k] = {
            "n": g["n"],
            "hit_rate": g["won"] / g["n"] if g["n"] else None,
            "mean_model_probability": g["p_sum"] / g["p_n"] if g["p_n"] else None,
            "mean_shortfall_when_missed": g["short_sum"] / g["short_n"] if g["short_n"] else None,
            "max_shortfall": g["short_max"] if g["short_n"] else None,
        }
    return out


def shortfall_distribution(legs: Iterable[LegResult]) -> dict[int, int]:
    """{shortfall: count} for lost player-stat legs (1 = missed by one)."""
    dist: dict[int, int] = defaultdict(int)
    for lg in legs:
        s = lg.shortfall
        if s is not None:
            dist[int(round(s))] += 1
    return dict(sorted(dist.items()))


def naive_joint_reference(probabilities: Iterable[float]) -> float:
    """Independence arithmetic used ONLY to explain why many individually
    likely legs are collectively unlikely. It is not a joint-probability
    model and must never be presented as one where legs can be correlated."""
    p = 1.0
    for x in probabilities:
        p *= x
    return p


# --------------------------------------------------------------------------
# Loaders: placed multis and frozen SGM combos (prospective, realised)
# --------------------------------------------------------------------------

def _label_for_bet(bet: PlacedBet) -> str:
    return bet.label


def load_placed_multis(db: Session) -> list[MultiResult]:
    bets = db.scalars(select(PlacedBet).where(PlacedBet.multi_group_id.is_not(None)).order_by(PlacedBet.multi_group_id, PlacedBet.id)).all()
    groups: dict[str, list[PlacedBet]] = defaultdict(list)
    for b in bets:
        groups[b.multi_group_id].append(b)
    multis = []
    for gid, legs in groups.items():
        multis.append(
            MultiResult(
                source="placed", multi_id=gid, match_id=legs[0].match_id, tier=legs[0].multi_tier, mode=legs[0].source_mode,
                indicative_odds=legs[0].multi_indicative_odds,
                legs=tuple(
                    LegResult(
                        label=_label_for_bet(b), market_type=b.market_type, outcome=b.status, player_id=b.player_id, threshold=b.threshold,
                        line_type=b.line_type, model_probability=b.model_probability, price=b.odds_taken, actual_value=b.actual_stat_value,
                        confidence_tier=b.confidence_tier, lineup_status=b.lineup_status,
                    )
                    for b in legs
                ),
            )
        )
    return multis


_HORIZON_RANK = {h: i for i, h in enumerate(SNAPSHOT_HORIZONS)}  # 24h_plus (0) ... under_1h (3): higher = closer to kickoff


def load_sgm_multis(db: Session, *, closing_only: bool = True) -> list[MultiResult]:
    """Frozen SGM combos. `closing_only` keeps one snapshot per real combo (the
    one closest to kickoff) so the same combo frozen at several horizons is
    never counted more than once - the same rule the SGM prospective
    evaluation itself uses for its headline numbers."""
    snaps = db.scalars(select(SgmPriceSnapshot).order_by(SgmPriceSnapshot.match_id, SgmPriceSnapshot.leg_signature)).all()
    chosen: dict[tuple, SgmPriceSnapshot] = {}
    for s in snaps:
        key = (s.match_id, s.leg_signature)
        if not closing_only:
            chosen[(key, s.id)] = s
            continue
        cur = chosen.get(key)
        if cur is None or _HORIZON_RANK.get(s.snapshot_horizon, -1) > _HORIZON_RANK.get(cur.snapshot_horizon, -1):
            chosen[key] = s
    multis = []
    for snap in chosen.values():
        legs = []
        for leg in snap.legs:
            player = leg.player.display_name if leg.player is not None else None
            if leg.leg_type in ("disposals", "goals") and player:
                thr = f"{leg.threshold:g}+" if leg.threshold is not None else ""
                label = f"{player} {thr} {leg.leg_type.title()}"
                market = f"player_{leg.leg_type}"
            else:
                label = f"{leg.selection} ({leg.leg_type})"
                market = leg.leg_type
            legs.append(
                LegResult(
                    label=label, market_type=market, outcome=leg.leg_outcome or "pending", player_id=leg.player_id, threshold=leg.threshold,
                    line_type="over_under" if leg.leg_type in ("disposals", "goals") else None,
                    model_probability=leg.naive_leg_probability, actual_value=leg.actual_value,
                )
            )
        multis.append(
            MultiResult(
                source="sgm", multi_id=f"{snap.match_id}:{snap.leg_signature}", match_id=snap.match_id, tier=None, mode=None,
                legs=tuple(legs), extra={"snapshot_horizon": snap.snapshot_horizon, "joint_model_probability": snap.model_probability,
                                         "naive_independence_probability": snap.naive_independence_probability},
            )
        )
    return multis


# --------------------------------------------------------------------------
# Prospective leg-level evidence (PropMarketObservation)
# --------------------------------------------------------------------------

def _naive(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo is not None else dt


def _latest_pre_kickoff_quotes(db: Session, match: Match) -> list[PropMarketObservation]:
    kickoff = _naive(match.scheduled_start)
    rows = db.scalars(select(PropMarketObservation).where(PropMarketObservation.match_id == match.id)).all()
    latest: dict[tuple, PropMarketObservation] = {}
    for r in rows:
        if _naive(r.observed_at) >= kickoff:
            continue
        key = (r.player_id, r.market_type, r.threshold, r.bookmaker_id)
        cur = latest.get(key)
        if cur is None or _naive(r.observed_at) > _naive(cur.observed_at):
            latest[key] = r
    return list(latest.values())


def _outcome(result: str | None) -> str:
    return result if result in ("won", "lost", "void", "push") else "pending"


def load_prospective_legs(db: Session, *, since: datetime | None = None, until: datetime | None = None) -> list[LegResult]:
    """One LegResult per real (match, player, market, threshold): the latest
    pre-kickoff observation, with the number of distinct bookmakers that
    quoted that exact market and that count as a share of every bookmaker
    quoting ANY player prop for the match (a proxy for how widely offered the
    market is - NOT liquidity or popularity)."""
    q = select(Match).where(Match.status == "completed")
    matches = db.scalars(q).all()
    legs: list[LegResult] = []
    for match in matches:
        start = _naive(match.scheduled_start)
        if since is not None and start < _naive(since):
            continue
        if until is not None and start >= _naive(until):
            continue
        quotes = _latest_pre_kickoff_quotes(db, match)
        if not quotes:
            continue
        universe = len({r.bookmaker_id for r in quotes})
        by_leg: dict[tuple, list[PropMarketObservation]] = defaultdict(list)
        for r in quotes:
            by_leg[(r.player_id, r.market_type, r.threshold)].append(r)
        players = {p.id: p.display_name for p in db.scalars(select(Player).where(Player.id.in_({k[0] for k in by_leg}))).all()}
        for (player_id, market_type, threshold), rows in by_leg.items():
            latest = max(rows, key=lambda r: _naive(r.observed_at))
            n_books = len({r.bookmaker_id for r in rows})
            best = max(rows, key=lambda r: r.offered_odds)
            legs.append(
                LegResult(
                    label=f"{players.get(player_id, player_id)} {threshold:g}+ {market_type.replace('player_', '').title()}",
                    market_type=market_type, outcome=_outcome(latest.market_result), player_id=player_id, threshold=threshold,
                    line_type=latest.line_type, model_probability=latest.model_probability, price=best.offered_odds,
                    actual_value=latest.actual_stat_value, confidence_tier=latest.confidence_tier,
                    lineup_status=latest.selection_status_at_observation, n_bookmakers=n_books,
                    coverage_share=n_books / universe if universe else None, predicted_mean=latest.predicted_mean,
                )
            )
    return legs


# --------------------------------------------------------------------------
# Replay: rebuild opportunity dicts from frozen observations and run the
# builder's own selection code over them.
# --------------------------------------------------------------------------

def reconstruct_opportunities(db: Session, match_id: int) -> tuple[list[dict], dict[tuple, LegResult]]:
    """Opportunity dicts shaped like best_opportunities' player opportunities,
    rebuilt from the latest pre-kickoff observation of each (player, market,
    threshold, bookmaker). Returns (opportunities, outcome_by_leg_key).

    Fidelity limits (stated, not hidden): only PLAYER legs are reconstructed
    (team odds are not observation-frozen); model-risk flags, warnings and the
    price-integrity diagnostic are not stored on observations and are left
    empty; every leg is treated as fresh. The selection code that consumes
    these dicts is the real, unmodified builder."""
    from app.player_modelling.best_opportunities import OPPORTUNITY_TYPE_PLAYER, _player_label
    from app.player_modelling.prop_math import categorize_edge
    from app.player_modelling.prop_opportunity_ranking import compute_opportunity_score
    from app.player_modelling.quality_tiers import compute_quality_tier, quality_tier_as_dict

    match = db.get(Match, match_id)
    quotes = _latest_pre_kickoff_quotes(db, match)
    if not quotes:
        return [], {}
    books = {b.id: b for b in db.scalars(select(Bookmaker)).all()}
    players = {p.id: p for p in db.scalars(select(Player).where(Player.id.in_({q.player_id for q in quotes}))).all()}
    team_of = {
        s.player_id: s.team_id
        for s in db.scalars(select(PlayerMatchStat).where(PlayerMatchStat.match_id == match_id)).all()
    }
    by_leg: dict[tuple, list[PropMarketObservation]] = defaultdict(list)
    for r in quotes:
        by_leg[(r.player_id, r.market_type, r.threshold, r.line_type)].append(r)

    opportunities: list[dict] = []
    outcomes: dict[tuple, LegResult] = {}
    for (player_id, market_type, threshold, line_type), rows in by_leg.items():
        entries = [
            {
                "bookmaker_id": r.bookmaker_id, "bookmaker_name": books[r.bookmaker_id].name, "price_decimal": r.offered_odds,
                "is_exchange": books[r.bookmaker_id].is_exchange, "eligibility": books[r.bookmaker_id].eligibility, "freshness": "fresh",
            }
            for r in rows
        ]
        eligible = [e for e in entries if e["eligibility"] == ELIGIBILITY_INCLUDED]
        best_entry = max(eligible or entries, key=lambda e: e["price_decimal"])
        best_row = next(r for r in rows if r.bookmaker_id == best_entry["bookmaker_id"])
        latest = max(rows, key=lambda r: _naive(r.observed_at))
        if best_row.difference_pp <= 0:  # mirrors load_normalized_prop_insights(opportunities_only=True)
            continue
        is_confirmed = bool(latest.is_confirmed_at_observation)
        score = compute_opportunity_score(
            difference_pp=best_row.difference_pp, expected_value=best_row.expected_value, confidence_tier=latest.confidence_tier,
            is_uncertain_participation=not is_confirmed, freshness="fresh", has_calibration_data=True,
        )
        opp = {
            "opportunity_type": OPPORTUNITY_TYPE_PLAYER, "match_id": match_id, "round_number": None, "season_year": None,
            "label": _player_label(players[player_id].display_name, threshold, market_type), "market_type": market_type,
            "player_id": player_id, "player_name": players[player_id].display_name, "team_id": team_of.get(player_id),
            "line_type": line_type, "threshold": threshold, "selection": None, "line_value": None,
            "model_probability": latest.model_probability, "model_fair_odds": latest.model_fair_odds,
            "model_name": latest.model_name, "model_version": latest.model_version,
            "best_price": best_entry["price_decimal"], "best_bookmaker": best_entry["bookmaker_name"],
            "eligible_price_available": bool(eligible), "difference_pp": best_row.difference_pp, "expected_value": best_row.expected_value,
            "edge_category": categorize_edge(best_row.difference_pp, latest.confidence_tier), "confidence_tier": latest.confidence_tier,
            "selection_status": latest.selection_status_at_observation, "is_confirmed": is_confirmed, "warnings": [],
            "usage_regime": None, "model_risk_flags": [], "n_bookmakers": len(entries), "bookmakers": entries, "odds_freshness": "fresh",
            "calibration": None, "opportunity_score": score.total, "predicted_mean": latest.predicted_mean,
            "opportunity_components": {
                "difference": score.difference_component, "expected_value": score.ev_component, "confidence": score.confidence_component,
                "freshness": score.freshness_component, "lineup": score.lineup_component, "calibration": score.calibration_component,
                "penalty_multiplier": score.penalty_multiplier, "penalty_reasons": score.penalty_reasons,
            },
        }
        opp["quality_tier"] = quality_tier_as_dict(compute_quality_tier(opp))
        opportunities.append(opp)
        outcomes[(player_id, market_type, threshold)] = LegResult(
            label=opp["label"], market_type=market_type, outcome=_outcome(latest.market_result), player_id=player_id, threshold=threshold,
            line_type=line_type, model_probability=latest.model_probability, actual_value=latest.actual_stat_value,
            confidence_tier=latest.confidence_tier, lineup_status=latest.selection_status_at_observation,
            n_bookmakers=len(entries), predicted_mean=latest.predicted_mean,
        )
    return opportunities, outcomes


def replay_multis(
    db: Session, match_ids: Iterable[int], build: Callable[..., object], *, modes: Iterable[str], confirmed_only: bool = True,
    extra_kwargs: dict | None = None,
) -> list[MultiResult]:
    """Runs `build(db, match_id, confirmed_only=..., mode=..., raw_opportunities=...)`
    (the builder's own entry point) over reconstructed opportunities and
    settles every generated option against the frozen real outcomes."""
    results: list[MultiResult] = []
    for match_id in match_ids:
        opps, outcomes = reconstruct_opportunities(db, match_id)
        if not opps:
            continue
        for mode in modes:
            built = build(db, match_id, confirmed_only=confirmed_only, mode=mode, raw_opportunities=opps, **(extra_kwargs or {}))
            for tier_key, tier in built.tiers.items():
                for i, opt in enumerate(tier.options):
                    legs = []
                    for leg in opt["legs"]:
                        base = outcomes[(leg["player_id"], leg["market_type"], leg["threshold"])]
                        legs.append(
                            LegResult(
                                label=base.label, market_type=base.market_type, outcome=base.outcome, player_id=base.player_id,
                                threshold=base.threshold, line_type=base.line_type, model_probability=base.model_probability,
                                price=leg["bookmaker_price"], actual_value=base.actual_value, confidence_tier=base.confidence_tier,
                                lineup_status=base.lineup_status, n_bookmakers=base.n_bookmakers, predicted_mean=base.predicted_mean,
                            )
                        )
                    results.append(
                        MultiResult(
                            source="replay", multi_id=f"{match_id}:{mode}:{tier_key}:{i}", match_id=match_id, tier=tier_key, mode=mode,
                            indicative_odds=opt["indicative_combined_odds"], legs=tuple(legs), extra={"bookmaker": opt["bookmaker"]},
                        )
                    )
    return results


# --------------------------------------------------------------------------
# Retrospective calibration (2019+ walk-forward predictions, persisted)
# --------------------------------------------------------------------------

def retrospective_disposal_calibration(
    db: Session, thresholds: Iterable[int] = (10, 15, 20, 25, 30), volume_edges: Iterable[float] = (0, 13, 17, 21, 25, 100),
) -> dict:
    """Calibration of the PROMOTED disposal model's persisted evaluation-period
    predictions at arbitrary thresholds, split by projected-volume bucket.
    Sourced from PlayerDisposalPrediction (the 2019+ chronological holdout the
    promotion gate already used), never from the prospective observations, so
    it is independent of them."""
    from app.player_modelling.disposal_distribution import NegativeBinomialDistribution

    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True)))
    if run is None:
        return {}
    preds = db.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == run.id)).all()
    edges = list(volume_edges)
    table: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "p_sum": 0.0, "hit": 0})
    for p in preds:
        dist = NegativeBinomialDistribution(mu=p.predicted_mean, alpha=p.nb_alpha)
        vol = next((f"{lo:g}-{hi:g}" for lo, hi in zip(edges, edges[1:]) if lo <= p.predicted_mean < hi), "other")
        for t in thresholds:
            prob = dist.prob_at_least(t)
            g = table[(t, vol)]
            g["n"] += 1
            g["p_sum"] += prob
            g["hit"] += 1 if p.actual_disposals >= t else 0
    return {
        "model": run.model_name, "n_predictions": len(preds),
        "rows": {
            f"{t}+ | projected {vol}": {"n": g["n"], "mean_predicted": g["p_sum"] / g["n"], "realised": g["hit"] / g["n"]}
            for (t, vol), g in sorted(table.items(), key=lambda kv: (kv[0][0], edges.index(float(kv[0][1].split("-")[0])) if kv[0][1] != "other" else 99))
        },
    }


def retrospective_goal_calibration(db: Session, thresholds: Iterable[int] = (1, 2, 3)) -> dict:
    """Goal-model counterpart of retrospective_disposal_calibration, split by
    the model's own projected goals per game."""
    from app.models import GoalModelRun, PlayerGoalPrediction
    from app.player_modelling.goal_distribution import HurdleDistribution, NegativeBinomialGoalDistribution

    run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    if run is None:
        return {}
    preds = db.scalars(select(PlayerGoalPrediction).where(PlayerGoalPrediction.model_run_id == run.id)).all()
    edges = (0.0, 0.3, 0.6, 1.0, 1.5, 100.0)
    table: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "p_sum": 0.0, "hit": 0})
    for p in preds:
        if p.distribution_kind == "hurdle" and p.p_score is not None and p.mu_scored is not None and p.alpha_scored is not None:
            dist = HurdleDistribution(p_score=p.p_score, mu_scored=p.mu_scored, alpha_scored=p.alpha_scored)
        elif p.nb_alpha is not None:
            dist = NegativeBinomialGoalDistribution(mu=p.predicted_mean, alpha=p.nb_alpha)
        else:
            continue
        vol = next((f"{lo:g}-{hi:g}" for lo, hi in zip(edges, edges[1:]) if lo <= p.predicted_mean < hi), "other")
        for t in thresholds:
            g = table[(t, vol)]
            g["n"] += 1
            g["p_sum"] += dist.prob_at_least(t)
            g["hit"] += 1 if p.actual_goals >= t else 0
    return {
        "model": run.model_name, "n_predictions": len(preds),
        "rows": {
            f"{t}+ | projected {vol}": {"n": g["n"], "mean_predicted": g["p_sum"] / g["n"], "realised": g["hit"] / g["n"]}
            for (t, vol), g in sorted(table.items(), key=lambda kv: (kv[0][0], float(kv[0][1].split("-")[0])))
        },
    }


# --------------------------------------------------------------------------
# Legacy High Probability selection (research comparator only)
# --------------------------------------------------------------------------

LEGACY_MAX_LEGS = {"conservative": 5, "balanced": 6, "higher_return": 7, "longer_shot": 8}
_LEGACY_POOL_SIZE = 16


def legacy_hp_build(db, match_id, *, confirmed_only=True, mode="high_probability", raw_opportunities=None, **_ignored):
    """The High Probability selection exactly as it behaved before the
    selection refinement: every alternate line as a candidate, the pool cut to
    the 16 highest-probability legs, combinations of up to 5/6/7/8 legs ranked
    by (weakest-leg probability, average probability, quality, correlation
    warnings, confirmed legs, edge, fewer legs, risk flags).

    Kept ONLY so scripts/multi_builder_audit.py can replay old and new logic
    over identical stored markets side by side. It is not used by the product.
    Reproduced from the original implementation; the audit's `--self-check`
    replays it against the legs the placed multis were built from."""
    from itertools import combinations
    from statistics import mean

    from app.player_modelling import multi_builder as mb

    legs = mb._all_alternate_legs(db, match_id, raw_opportunities=raw_opportunities)
    by_bookmaker = mb._legs_by_bookmaker(legs)
    tiers: dict[str, mb.TierResult] = {}
    player_counts: dict[int, int] = {}

    def pool_for(tier_key, book_legs):
        pool = book_legs
        if confirmed_only:
            pool = [lg for lg in pool if lg["opportunity_type"] != "player" or lg.get("is_confirmed")]
        min_prob = mb.MIN_LEG_PROBABILITY[tier_key]
        pool = [lg for lg in pool if lg["model_probability"] >= min_prob and lg["difference_pp"] >= mb.MIN_EDGE_FLOOR_HIGH_PROBABILITY and not mb._is_strict_goals_excluded(lg, tier_key)]
        return sorted(pool, key=mb._high_probability_score, reverse=True)[:_LEGACY_POOL_SIZE]

    def old_key(combo, warnings):
        probs = [lg["model_probability"] for lg in combo]
        n_conf = sum(1 for lg in combo if lg["opportunity_type"] != "player" or lg.get("is_confirmed"))
        avg_quality = mean((lg["opportunity_components"]["confidence"] + lg["opportunity_components"]["calibration"]) / 2 for lg in combo)
        return (min(probs), mean(probs), avg_quality, -len(warnings), n_conf, mean(lg["difference_pp"] for lg in combo), -len(combo), -sum(1 for lg in combo if lg.get("model_risk_flags")))

    def valid(combo, lo, hi, excl_ids, excl_fams):
        fams = [lg["_family_key"] for lg in combo]
        if len(set(fams)) != len(fams) or any(f in excl_fams for f in fams):
            return None
        pids = [lg["player_id"] for lg in combo if lg.get("player_id") is not None]
        if len(set(pids)) != len(pids) or any(player_counts.get(p, 0) >= mb.MAX_APPEARANCES_PER_PLAYER for p in pids):
            return None
        if any(mb._leg_id(lg) in excl_ids for lg in combo):
            return None
        prod = 1.0
        for lg in combo:
            prod *= lg["bookmaker_price"]
        if prod < lo or (hi is not None and prod > hi):
            return None
        warnings = []
        for a, b in combinations(combo, 2):
            cat = mb._pair_correlation(a, b)
            if cat is None:
                continue
            if mb.CORRELATION_STRENGTH[cat] == "strong":
                return None
            warnings.append(cat)
        return prod, warnings

    for tier_key in mb.TIER_ORDER:
        lo, hi = mb.TIER_RANGES[tier_key]
        pools = {n: pool_for(tier_key, lg) for n, lg in by_bookmaker.items()}
        ranked = sorted(((n, p) for n, p in pools.items() if len(p) >= mb.MIN_LEGS[tier_key]), key=lambda kv: len(kv[1]), reverse=True)[:5]
        options, excl_ids, excl_fams = [], set(), set()
        for _ in range(mb.MAX_OPTIONS_PER_TIER):
            best, best_k = None, None
            for name, pool in ranked:
                for size in range(mb.MIN_LEGS[tier_key], min(LEGACY_MAX_LEGS[tier_key], len(pool)) + 1):
                    for combo in combinations(pool, size):
                        ok = valid(combo, lo, hi, excl_ids, excl_fams)
                        if ok is None:
                            continue
                        k = old_key(combo, ok[1])
                        if best is None or k > best_k:
                            best, best_k = (combo, ok[0], name), k
            if best is None:
                break
            combo, prod, name = best
            for lg in combo:
                if lg.get("player_id") is not None:
                    player_counts[lg["player_id"]] = player_counts.get(lg["player_id"], 0) + 1
            options.append({"legs": list(combo), "indicative_combined_odds": prod, "bookmaker": name})
            excl_ids |= {mb._leg_id(lg) for lg in combo}
            excl_fams |= {lg["_family_key"] for lg in combo}
        tiers[tier_key] = mb.TierResult(options=options, unavailable_reason=None, bookmaker_comparison=[])
    return mb.MatchMultiTiers(match_id=match_id, n_eligible_legs=len(legs), bookmakers_available=[], tiers=tiers)
