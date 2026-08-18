"""Diversified "Best Opportunities" views (Weekly Opportunity Discovery +
Diversification stage) — sits ON TOP of, and never replaces,
best_opportunities.load_best_opportunities's raw per-market ranking
(Section 1: "Do not delete or hide raw alternate markets"). This module
groups correlated alternate lines into families (opportunity_families.py),
picks one representative per family, runs sanity checks, applies
diversification selection rules, and enriches each selected headline with
recent-form context, reason codes, and price-shopping data.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.opportunity_families import (
    OpportunityFamily,
    compute_correlation_labels,
    diversify,
    group_into_families,
    price_advantage_pct,
    representative_score,
)
from app.player_modelling.market_correlation import compute_market_correlations, market_correlation_labels
from app.player_modelling.opportunity_reason_codes import compute_reason_codes, reason_labels
from app.player_modelling.opportunity_sanity import filter_sane
from app.player_modelling.player_recent_form import (
    STAT_FIELD_BY_MARKET,
    conservative_model_flag,
    form_disagreement_label,
    hit_rate,
    hit_rate_description,
    load_recent_form,
)
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, FreshnessThresholds

VIEW_OVERALL = "overall"
VIEW_DISPOSALS = "disposals"
VIEW_GOALS = "goals"

DEFAULT_DIVERSIFIED_LIMIT = 10


def _alternate_summary(o: dict) -> dict:
    return {
        "threshold": o["threshold"],
        "line_type": o["line_type"],
        "label": o["label"],
        "model_probability": o["model_probability"],
        "best_price": o["best_price"],
        "best_bookmaker": o["best_bookmaker"],
        "difference_pp": o["difference_pp"],
        "expected_value": o["expected_value"],
        "n_bookmakers": o["n_bookmakers"],
    }


def _projection_baselines(input_features: dict, prefix: str) -> dict[str, float | None]:
    return {
        "last5_avg": input_features.get(f"{prefix}_last5_avg"),
        "last10_avg": input_features.get(f"{prefix}_last10_avg"),
        "ewma": input_features.get(f"{prefix}_ewma"),
    }


def _recent_form_block(db: Session, opportunity: dict, match: Match) -> dict | None:
    if opportunity["opportunity_type"] != "player":
        return None
    stat_field = STAT_FIELD_BY_MARKET.get(opportunity["market_type"])
    if stat_field is None:
        return None

    form = load_recent_form(db, opportunity["player_id"], match.scheduled_start, stat_field)
    threshold = opportunity["threshold"]
    line_type = opportunity["line_type"]
    recent_hit_rate = hit_rate(form.last10, threshold, line_type)

    if opportunity["market_type"] == "player_disposals":
        proj = db.scalar(
            select(PlayerDisposalProjection).where(
                PlayerDisposalProjection.match_id == opportunity["match_id"], PlayerDisposalProjection.player_id == opportunity["player_id"]
            )
        )
        prefix = "disposals"
    else:
        proj = db.scalar(
            select(PlayerGoalProjection).where(
                PlayerGoalProjection.match_id == opportunity["match_id"], PlayerGoalProjection.player_id == opportunity["player_id"]
            )
        )
        prefix = "goals"

    conservative_flag = None
    predicted_mean = None
    if proj is not None:
        predicted_mean = proj.predicted_mean
        baselines = _projection_baselines(proj.input_features, prefix)
        conservative_flag = conservative_model_flag(proj.predicted_mean, baselines)

    return {
        "stat_field": stat_field,
        "last5": form.last5,
        "last10": form.last10,
        "last5_avg": form.last5_avg,
        "last10_avg": form.last10_avg,
        "predicted_mean": predicted_mean,
        "hit_rate_description": hit_rate_description(form.last10, threshold, line_type),
        "form_disagreement_label": form_disagreement_label(opportunity["model_probability"], recent_hit_rate),
        "conservative_model_flag": conservative_flag,
    }


@dataclass(frozen=True)
class DiversifiedResult:
    opportunities: list[dict]  # the final, diversified, limited headline list
    all_passing: list[dict]  # the FULL quality-gated + sanity-checked universe, before diversification/limit — for weekly-summary stats that describe "everything currently passing gates", not just what's shown
    rejected_count: int  # how many failed sanity checks, for internal reporting


def load_diversified_opportunities(
    db: Session,
    *,
    view: str = VIEW_OVERALL,
    market_scope: str = "all",
    include_uncertain: bool = False,
    include_stale: bool = False,
    include_insufficient_history: bool = False,
    one_per_match: bool = False,
    one_per_player: bool = False,
    limit: int | None = DEFAULT_DIVERSIFIED_LIMIT,
    freshness_thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS,
) -> DiversifiedResult:
    effective_scope = "player" if view in (VIEW_DISPOSALS, VIEW_GOALS) else market_scope
    raw = load_best_opportunities(
        db,
        market_scope=effective_scope,
        include_uncertain=include_uncertain,
        include_stale=include_stale,
        include_insufficient_history=include_insufficient_history,
        limit=None,
        freshness_thresholds=freshness_thresholds,
    )
    if view == VIEW_DISPOSALS:
        raw = [o for o in raw if o["market_type"] == "player_disposals"]
    elif view == VIEW_GOALS:
        raw = [o for o in raw if o["market_type"] == "player_goals"]

    passed, rejected = filter_sane(raw)

    match_ids = {o["match_id"] for o in passed}
    matches = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all()} if match_ids else {}
    match_labels = {mid: f"{m.home_team.name} v {m.away_team.name}" for mid, m in matches.items()}

    families = group_into_families(passed, match_labels)

    if one_per_match:
        families = _collapse_to_best_per_key(families, key_fn=lambda f: f.representative["match_id"])
    if one_per_player:
        families = _collapse_to_best_per_key(
            families, key_fn=lambda f: f.representative.get("player_id") if f.representative.get("player_id") is not None else f.key
        )

    selected = diversify(families, limit=limit)
    correlation_labels = compute_correlation_labels(selected)
    # Section 6: cross-market correlation (H2H+line for the same team,
    # team-view+match-total, same-team players) — computed relative to the
    # representatives actually being shown, same convention as
    # compute_correlation_labels above.
    market_correlations = compute_market_correlations([fam.representative for fam in selected])

    enriched = []
    for fam in selected:
        rep = fam.representative
        match = matches.get(rep["match_id"])
        recent_form = _recent_form_block(db, rep, match) if match is not None else None
        form_disagreement = bool(recent_form and recent_form["form_disagreement_label"])

        entry = dict(rep)
        entry["family_label"] = fam.label
        entry["alternate_lines"] = [_alternate_summary(a) for a in fam.alternates]
        entry["correlation_labels"] = correlation_labels[fam.key] + market_correlation_labels(rep, market_correlations)
        entry["price_advantage_pct"] = price_advantage_pct(rep)
        entry["recent_form"] = recent_form
        codes = compute_reason_codes(rep, form_disagreement=form_disagreement)
        entry["reason_codes"] = codes
        entry["reason_labels"] = reason_labels(codes)
        entry["representative_score"] = representative_score(rep)
        enriched.append(entry)

    return DiversifiedResult(opportunities=enriched, all_passing=passed, rejected_count=len(rejected))


def _collapse_to_best_per_key(families: list[OpportunityFamily], *, key_fn) -> list[OpportunityFamily]:
    best_by_key: dict = {}
    for fam in families:
        key = key_fn(fam)
        current = best_by_key.get(key)
        if current is None or representative_score(fam.representative) > representative_score(current.representative):
            best_by_key[key] = fam
    return list(best_by_key.values())
