"""Model vs Market Disagreements diagnostic (Market Integrity + Final
Weekly Picks stage, Section 18) — NOT an opportunity list, and never
called "Best Bets" anywhere in this module or its API/UI. Best
Opportunities and the Final Weekly Shortlist only ever show markets where
the MODEL exceeds the market (a positive difference_pp) — for players,
that's enforced by prop_insights_normalized.load_normalized_prop_insights's
`opportunities_only` filter. That's the right behaviour for "what should I
bet on", but it means the far more interesting failure mode — the market
being FAR more confident than the model (e.g. the brief's own example:
"Nick Daicos 29.5+, Model 26.8%, Bookmaker implied 93.5%") — never
surfaces anywhere in this app today. This module exists purely to make
those large disagreements, IN EITHER DIRECTION, visible as a model-quality
diagnostic: a persistent large disagreement against the model is a signal
worth investigating (is the model missing something? mispriced feature?),
not a betting signal.
"""


from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.player_recent_form import STAT_FIELD_BY_MARKET, load_recent_form
from app.player_modelling.prop_insights_normalized import load_normalized_prop_insights
from app.player_modelling.upcoming_features import load_next_upcoming_round

# Deliberately large — Section 18's own example is a ~67pp gap. This is a
# diagnostic threshold, not a statistical significance test.
DEFAULT_DISAGREEMENT_THRESHOLD_PP = 0.15

DIRECTION_MODEL_ABOVE_MARKET = "model_above_market"
DIRECTION_MARKET_ABOVE_MODEL = "market_above_model"


def _direction(difference_pp: float) -> str:
    return DIRECTION_MODEL_ABOVE_MARKET if difference_pp > 0 else DIRECTION_MARKET_ABOVE_MODEL


def _player_baselines(input_features: dict, prefix: str) -> dict[str, float | None]:
    return {
        "last5_avg": input_features.get(f"{prefix}_last5_avg"),
        "last10_avg": input_features.get(f"{prefix}_last10_avg"),
        "ewma": input_features.get(f"{prefix}_ewma"),
    }


def load_model_market_disagreements(
    db: Session, *, threshold_pp: float = DEFAULT_DISAGREEMENT_THRESHOLD_PP, limit: int | None = 20
) -> list[dict]:
    upcoming = load_next_upcoming_round(db)
    match_ids = {m.match_id for m in upcoming}
    if not match_ids:
        return []

    entries: list[dict] = []

    # --- team markets: load_best_opportunities already includes BOTH
    # directions for teams (no opportunities_only-style filter exists on
    # that path), so this just re-filters/re-labels rather than
    # re-deriving anything. ---
    team_rows = load_best_opportunities(
        db, market_scope="team", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None
    )
    for r in team_rows:
        if abs(r["difference_pp"]) < threshold_pp:
            continue
        entries.append(
            {
                "opportunity_type": "team",
                "match_id": r["match_id"],
                "label": r["label"],
                "market_type": r["market_type"],
                "player_id": None,
                "player_name": None,
                "threshold": None,
                "line_type": None,
                "model_probability": r["model_probability"],
                "model_predicted_mean": None,
                "market_probability": r["devigged_probability"] if r["overround_removed"] else r["market_implied_probability"],
                "overround_removed": r["overround_removed"],
                "difference_pp": r["difference_pp"],
                "direction": _direction(r["difference_pp"]),
                "confidence_tier": r["confidence_tier"],
                "best_price": r["best_price"],
                "best_bookmaker": r["best_bookmaker"],
                "bookmakers": r["bookmakers"],
                "n_bookmakers": r["n_bookmakers"],
                "recent_form": None,
                "calibration": r["calibration"],
                "odds_freshness": r["odds_freshness"],
                "warnings": r["warnings"],
            }
        )

    # --- player markets: bypass opportunities_only so a model-below
    # -market disagreement isn't invisible. ---
    player_rows = load_normalized_prop_insights(db, include_uncertain=True, opportunities_only=False)
    flagged_player_rows = [r for r in player_rows if r["match_id"] in match_ids and abs(r["difference_pp"]) >= threshold_pp]
    matches = (
        {m.id: m for m in db.scalars(select(Match).where(Match.id.in_({r["match_id"] for r in flagged_player_rows}))).all()}
        if flagged_player_rows
        else {}
    )
    for r in flagged_player_rows:
        match = matches.get(r["match_id"])
        recent = None
        stat_field = STAT_FIELD_BY_MARKET.get(r["market_type"])
        if match is not None and stat_field is not None:
            form = load_recent_form(db, r["player_id"], match.scheduled_start, stat_field)
            recent = {"last5": form.last5, "last10": form.last10, "last5_avg": form.last5_avg, "last10_avg": form.last10_avg}

        if r["market_type"] == "player_disposals":
            proj = db.scalar(
                select(PlayerDisposalProjection).where(
                    PlayerDisposalProjection.match_id == r["match_id"], PlayerDisposalProjection.player_id == r["player_id"]
                )
            )
            prefix, predicted_mean = "disposals", proj.predicted_mean if proj else None
        else:
            proj = db.scalar(
                select(PlayerGoalProjection).where(
                    PlayerGoalProjection.match_id == r["match_id"], PlayerGoalProjection.player_id == r["player_id"]
                )
            )
            prefix, predicted_mean = "goals", proj.predicted_mean if proj else None
        if recent is not None:
            recent["ewma"] = _player_baselines(proj.input_features, prefix)["ewma"] if proj is not None else None
        model_predicted_mean = predicted_mean

        entries.append(
            {
                "opportunity_type": "player",
                "match_id": r["match_id"],
                "label": f"{r['player_name']} {r['threshold']:g}+ {'Disposals' if r['market_type'] == 'player_disposals' else 'Goals'}",
                "market_type": r["market_type"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "threshold": r["threshold"],
                "line_type": r["line_type"],
                "model_probability": r["model_probability"],
                "model_predicted_mean": model_predicted_mean,
                "market_probability": r["devigged_probability"] if r["overround_removed"] else r["raw_implied_probability"],
                "overround_removed": r["overround_removed"],
                "difference_pp": r["difference_pp"],
                "direction": _direction(r["difference_pp"]),
                "confidence_tier": r["confidence_tier"],
                "best_price": r["best_price"],
                "best_bookmaker": r["best_bookmaker"],
                "bookmakers": r["bookmakers"],
                "n_bookmakers": r["n_bookmakers"],
                "recent_form": recent,
                "calibration": r["calibration"],
                "odds_freshness": r["odds_freshness"],
                "warnings": r["warnings"],
            }
        )

    entries.sort(key=lambda e: -abs(e["difference_pp"]))
    if limit is not None:
        entries = entries[:limit]
    return entries
