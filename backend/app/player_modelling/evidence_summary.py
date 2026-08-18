"""Opportunity evidence summary (Weekly Bet Review + Decision Support
stage, Sections 12-13) — "Evidence supporting" and "Reasons for caution",
built entirely from structured, named codes (this app's existing
REASON_* codes from opportunity_reason_codes.py, plus this stage's own
uncertainty flags and a handful of new evidence codes tied to the new
context this stage adds — calibration bands, market movement, consensus,
projection distance). Never generated free text.

Section 13 is explicit: "the review page should help challenge the model,
not sell the opportunity." Every caution flag computed for an opportunity
is included here regardless of how strong its headline edge looks — this
module never filters caution flags down to make an opportunity look
better, and never fabricates supporting evidence to fill out the list.
"""

from dataclasses import dataclass

from app.player_modelling.context_model_conflict import CONTEXT_REASON_LABELS
from app.player_modelling.market_movement import AWAY_FROM_MODEL, TOWARD_MODEL
from app.player_modelling.opportunity_reason_codes import REASON_LABELS, compute_reason_codes, reason_labels
from app.player_modelling.uncertainty_flags import UncertaintyFlagInputs, compute_uncertainty_flags, uncertainty_flag_labels

EVIDENCE_WELL_CALIBRATED_BAND = "WELL_CALIBRATED_PROBABILITY_BAND"
EVIDENCE_MULTIPLE_BOOKMAKERS_CONFIRM = "MULTIPLE_BOOKMAKERS_CONFIRM"
EVIDENCE_PROJECTION_CLEARS_LINE = "PROJECTION_COMFORTABLY_CLEARS_LINE"
EVIDENCE_MARKET_MOVED_TOWARD_MODEL = "MARKET_MOVED_TOWARD_MODEL"

EVIDENCE_LABELS: dict[str, str] = {
    EVIDENCE_WELL_CALIBRATED_BAND: "This probability band has historically been well-calibrated",
    EVIDENCE_MULTIPLE_BOOKMAKERS_CONFIRM: "Multiple bookmakers confirm this market",
    EVIDENCE_PROJECTION_CLEARS_LINE: "Model projection comfortably clears the line",
    EVIDENCE_MARKET_MOVED_TOWARD_MODEL: "Market has moved toward the model's view",
}

CAUTION_MARKET_MOVED_AWAY = "MARKET_MOVED_AWAY_FROM_MODEL"
CAUTION_LABELS: dict[str, str] = {
    CAUTION_MARKET_MOVED_AWAY: "Market has moved away from the model's view",
}

_MULTI_BOOKMAKER_CONFIRM_MIN = 3
# A comfortable clearance in the market's own units - deliberately
# generous (a few points/disposals/goals), since "comfortably" should
# mean more than a marginal projection edge.
_LINE_CLEARANCE_MARGIN = {"line": 3.0, "total": 3.0, "player_disposals": 1.5, "player_goals": 0.3}


@dataclass(frozen=True)
class EvidenceSummary:
    evidence_codes: list[str]
    evidence_labels: list[str]
    caution_codes: list[str]
    caution_labels: list[str]


def build_evidence_summary(
    opportunity: dict,
    *,
    any_confirmed_player_lineups: bool,
    form_disagreement: bool = False,
    calibration_band=None,
    movement=None,
    consensus=None,
    outlier_check=None,
    projection_distance=None,
    elite_disposal_bucket: str | None = None,
    tog_volatile: bool | None = None,
    context_conflict=None,  # context_model_conflict.ContextConflictResult
) -> EvidenceSummary:
    evidence_codes = list(compute_reason_codes(opportunity, form_disagreement=form_disagreement))

    if calibration_band is not None and calibration_band.meets_min_sample and calibration_band.avg_predicted is not None:
        if abs(calibration_band.avg_predicted - calibration_band.actual_rate) < 0.05:
            evidence_codes.append(EVIDENCE_WELL_CALIBRATED_BAND)

    if consensus is not None and consensus.n_bookmakers >= _MULTI_BOOKMAKER_CONFIRM_MIN:
        evidence_codes.append(EVIDENCE_MULTIPLE_BOOKMAKERS_CONFIRM)

    if projection_distance is not None:
        margin = _LINE_CLEARANCE_MARGIN.get(projection_distance.market_type, 0.0)
        if projection_distance.distance >= margin:
            evidence_codes.append(EVIDENCE_PROJECTION_CLEARS_LINE)

    caution_codes: list[str] = []
    if movement is not None:
        if movement.direction == TOWARD_MODEL:
            evidence_codes.append(EVIDENCE_MARKET_MOVED_TOWARD_MODEL)
        elif movement.direction == AWAY_FROM_MODEL:
            caution_codes.append(CAUTION_MARKET_MOVED_AWAY)

    flag_inputs = UncertaintyFlagInputs(
        any_confirmed_player_lineups=any_confirmed_player_lineups,
        calibration_band=calibration_band,
        elite_disposal_bucket=elite_disposal_bucket,
        outlier_check=outlier_check,
        consensus_spread=consensus.spread if consensus is not None else None,
        tog_volatile=tog_volatile,
    )
    caution_codes.extend(compute_uncertainty_flags(opportunity, flag_inputs))

    # Current-context flags (Sections 5, 12-14): always caution, never
    # evidence — this stage explicitly surfaces context without spinning it
    # as support for the opportunity (Section 15).
    context_codes = context_conflict.codes if context_conflict is not None else []
    caution_codes.extend(context_codes)

    evidence_labels = reason_labels([c for c in evidence_codes if c in REASON_LABELS]) + [
        EVIDENCE_LABELS[c] for c in evidence_codes if c in EVIDENCE_LABELS
    ]
    caution_labels = (
        uncertainty_flag_labels([c for c in caution_codes if c not in CAUTION_LABELS and c not in CONTEXT_REASON_LABELS])
        + [CAUTION_LABELS[c] for c in caution_codes if c in CAUTION_LABELS]
        + [CONTEXT_REASON_LABELS[c] for c in caution_codes if c in CONTEXT_REASON_LABELS]
    )

    return EvidenceSummary(evidence_codes=evidence_codes, evidence_labels=evidence_labels, caution_codes=caution_codes, caution_labels=caution_labels)


def evidence_summary_as_dict(s: EvidenceSummary) -> dict:
    return {
        "evidence_codes": s.evidence_codes,
        "evidence_labels": s.evidence_labels,
        "caution_codes": s.caution_codes,
        "caution_labels": s.caution_labels,
    }
