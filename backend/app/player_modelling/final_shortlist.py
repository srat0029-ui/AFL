"""Final Weekly Shortlist (Market Integrity + Final Weekly Picks stage,
Sections 7-11, 22) — deliberately MORE selective than Best Opportunities
(diversified_opportunities.py). Best Opportunities stays a broad,
diversified "here's everything reasonable" view that can still contain
correlated markets (clearly labelled). This module answers a narrower
question: "which of those represent genuinely DISTINCT model opinions I
could act on right now?"

Differences from Best Opportunities' diversify():
- Player opportunities require a CONFIRMED selection by default (Section
  10) — a toggle, never a silent default loosening.
- Team markets stay available pre-confirmation (Section 11), flagged
  "Teams not confirmed yet" rather than excluded.
- Only quality_tiers.TIER_STRONG_CANDIDATE / TIER_WORTH_REVIEWING may
  headline — TIER_SPECULATIVE and TIER_DO_NOT_HEADLINE never do, matching
  Section 9's readiness tiers.
- At most ONE representative per strong_correlation_group_key (Section
  7's "max one strongly-correlated team-market opinion per match" — e.g.
  Collingwood H2H and Collingwood +24.5 collapse to whichever is the
  stronger representative, not both).
- Top N is a MAXIMUM, never a target (Section 8) — there is no backfill
  pass here (unlike diversify()'s soft-cap backfill): if fewer than N
  opportunities genuinely qualify, fewer than N are returned.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedLineup, Match
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.market_correlation import compute_market_correlations, market_correlation_labels, strong_correlation_group_key
from app.player_modelling.opportunity_families import (
    MAX_HEADLINES_PER_PLAYER,
    group_into_families,
    representative_score,
)
from app.player_modelling.opportunity_reason_codes import compute_reason_codes, reason_labels
from app.player_modelling.opportunity_sanity import filter_sane
from app.player_modelling.player_recent_form import STAT_FIELD_BY_MARKET, form_disagreement_label, hit_rate, load_recent_form
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, FreshnessThresholds
from app.player_modelling.quality_tiers import TIER_STRONG_CANDIDATE, TIER_WORTH_REVIEWING
from app.player_modelling.upcoming_features import load_next_upcoming_round

DEFAULT_SHORTLIST_LIMIT = 10
_HEADLINE_TIERS = {TIER_STRONG_CANDIDATE, TIER_WORTH_REVIEWING}


def _select_shortlist(families: list, *, limit: int | None) -> list:
    """Never backfills (Section 8) — strictly the best-ranked families
    that clear both the per-player cap and the strong-correlation
    collapse, up to `limit`."""
    ranked = sorted(families, key=lambda f: representative_score(f.representative), reverse=True)
    selected = []
    per_player: dict[int, int] = {}
    used_strong_keys: set = set()

    for fam in ranked:
        rep = fam.representative
        player_id = rep.get("player_id")
        if player_id is not None and per_player.get(player_id, 0) >= MAX_HEADLINES_PER_PLAYER:
            continue

        strong_key = strong_correlation_group_key(rep)
        if strong_key is not None and strong_key in used_strong_keys:
            continue

        selected.append(fam)
        if player_id is not None:
            per_player[player_id] = per_player.get(player_id, 0) + 1
        if strong_key is not None:
            used_strong_keys.add(strong_key)

        if limit is not None and len(selected) >= limit:
            break

    return selected


@dataclass(frozen=True)
class ExcludedOpportunity:
    label: str
    opportunity_type: str
    reason: str


@dataclass(frozen=True)
class FinalShortlistResult:
    opportunities: list[dict]
    excluded: list[ExcludedOpportunity]  # transparency: what qualified-but-close was left out and why
    empty_state_reason: str | None  # Section 22 — set only when opportunities is empty
    any_confirmed_player_lineups: bool


def load_final_shortlist(
    db: Session,
    *,
    limit: int | None = DEFAULT_SHORTLIST_LIMIT,
    include_unconfirmed_players: bool = False,
    freshness_thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS,
) -> FinalShortlistResult:
    # Pull the full universe with every optional gate OPEN — the
    # Shortlist applies its OWN, generally stricter gates below (quality
    # tier already folds in staleness/insufficient-history/confirmed-out;
    # is_confirmed is handled explicitly so the toggle is meaningful).
    raw = load_best_opportunities(
        db,
        market_scope="all",
        include_uncertain=True,
        include_stale=True,
        include_insufficient_history=True,
        limit=None,
        freshness_thresholds=freshness_thresholds,
    )
    passed, _sanity_rejected = filter_sane(raw)

    upcoming = load_next_upcoming_round(db)
    match_ids = [m.match_id for m in upcoming]
    any_confirmed_player_lineups = bool(
        match_ids
        and db.scalar(
            select(ExpectedLineup.id).where(ExpectedLineup.match_id.in_(match_ids), ExpectedLineup.is_confirmed.is_(True)).limit(1)
        )
        is not None
    )

    headline_eligible: list[dict] = []
    excluded: list[ExcludedOpportunity] = []
    for o in passed:
        tier = o["quality_tier"]["tier"]
        if tier not in _HEADLINE_TIERS:
            excluded.append(ExcludedOpportunity(o["label"], o["opportunity_type"], f"quality tier: {o['quality_tier']['label']}"))
            continue
        if o["opportunity_type"] == "player" and not o.get("is_confirmed") and not include_unconfirmed_players:
            excluded.append(ExcludedOpportunity(o["label"], o["opportunity_type"], "player's selection is not yet confirmed"))
            continue
        headline_eligible.append(o)

    match_objs = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_({o["match_id"] for o in headline_eligible}))).all()} if headline_eligible else {}
    match_labels = {mid: f"{m.home_team.name} v {m.away_team.name}" for mid, m in match_objs.items()}

    families = group_into_families(headline_eligible, match_labels)
    selected = _select_shortlist(families, limit=limit)
    market_correlations = compute_market_correlations([f.representative for f in selected])

    enriched: list[dict] = []
    for fam in selected:
        rep = fam.representative
        match = match_objs.get(rep["match_id"])

        form_disagreement = False
        if rep["opportunity_type"] == "player" and match is not None:
            stat_field = STAT_FIELD_BY_MARKET.get(rep["market_type"])
            if stat_field is not None:
                form = load_recent_form(db, rep["player_id"], match.scheduled_start, stat_field)
                recent_hit_rate = hit_rate(form.last10, rep["threshold"], rep["line_type"])
                label = form_disagreement_label(rep["model_probability"], recent_hit_rate)
                form_disagreement = bool(label)

        entry = dict(rep)
        entry["family_label"] = fam.label
        entry["alternate_lines"] = [
            {
                "threshold": a["threshold"],
                "line_type": a["line_type"],
                "label": a["label"],
                "model_probability": a["model_probability"],
                "best_price": a["best_price"],
                "best_bookmaker": a["best_bookmaker"],
                "difference_pp": a["difference_pp"],
                "expected_value": a["expected_value"],
                "n_bookmakers": a["n_bookmakers"],
            }
            for a in fam.alternates
        ]
        entry["correlation_labels"] = market_correlation_labels(rep, market_correlations)
        codes = compute_reason_codes(rep, form_disagreement=form_disagreement)
        entry["reason_codes"] = codes
        entry["why_it_ranks_here"] = reason_labels(codes)
        entry["caveats"] = rep["quality_tier"]["caveats"]
        if rep["opportunity_type"] == "team" and not any_confirmed_player_lineups:
            entry.setdefault("caveats", [])
            if "Teams not confirmed yet" not in entry["caveats"]:
                entry["caveats"] = entry["caveats"] + ["Teams not confirmed yet"]
        enriched.append(entry)

    # Section 22: distinguish "there's genuinely nothing to show yet
    # because teams aren't confirmed" from "there IS market data, it's
    # just not currently good enough to headline" — the two need
    # different messages, and the lineup gate must never be silently
    # loosened just to avoid the empty state.
    empty_state_reason = None
    if not enriched:
        if not passed:
            empty_state_reason = "No opportunities are currently available this round."
        elif not any_confirmed_player_lineups and not any(o["opportunity_type"] == "team" for o in passed):
            empty_state_reason = "Player opportunities are waiting for confirmed teams."
        else:
            empty_state_reason = "No opportunities currently clear the Final Shortlist's readiness bar this round — see 'excluded' for why each was left out."

    return FinalShortlistResult(
        opportunities=enriched,
        excluded=excluded,
        empty_state_reason=empty_state_reason,
        any_confirmed_player_lineups=any_confirmed_player_lineups,
    )
