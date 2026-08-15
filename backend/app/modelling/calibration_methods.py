"""Post-hoc probability calibration for the logistic model's raw output.

Fit entirely on tune-window data, never the final evaluation outcomes.
Specifically: the calibration *function* (Platt scaling or isotonic
regression) is learned from an inner model's out-of-sample predictions —
fit on the earlier part of the tune window, evaluated on the later part —
then applied unchanged to the final model's evaluation-period predictions.
The calibration curve is a property of how this class of model tends to be
over/under-confident, which is reasonably stable between two models trained
on overlapping seasons of the same competition; refitting a calibrator
directly on the final model's own training predictions would be in-sample
(a static model scored on the data used to produce it) and was avoided for
the same reason logistic_tuning.py uses an inner split for regularisation
strength.

Only adopted if it improves the inner out-of-sample Brier score by more
than `min_improvement` — see select_calibration_method(). A marginal
improvement isn't worth the extra moving part.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from app.modelling.metrics import brier_score, log_loss

CalibrationMethod = Literal["none", "platt", "isotonic"]

# Isotonic regression fits a step function that can plateau at exactly 0 or
# 1 when the (typically small) validation sample it's fit on happens to
# have a pure run of one outcome at the tails. Applied to a larger, later
# evaluation set, a single wrong prediction at that extreme is catastrophic
# for log loss (-log(~0) is enormous) even though it barely moves Brier
# score — exactly the gap between the two metrics the Stage brief's "do not
# rely on Brier alone" guidance warns about. Clipping away from the exact
# boundary is a standard, defensive safeguard for isotonic calibration in
# production use, not a workaround for a modelling mistake.
_ISOTONIC_CLIP = (0.02, 0.98)


@dataclass(frozen=True)
class FittedCalibrator:
    method: CalibrationMethod
    model: object | None = None  # opaque fitted sklearn object; None if method == "none"

    def apply(self, raw_probs: list[float]) -> list[float]:
        if self.method == "none" or self.model is None:
            return list(raw_probs)
        if self.method == "platt":
            class_index = list(self.model.classes_).index(1.0)
            return list(self.model.predict_proba(np.array(raw_probs).reshape(-1, 1))[:, class_index])
        if self.method == "isotonic":
            raw = self.model.predict(np.array(raw_probs))
            return [float(min(max(v, _ISOTONIC_CLIP[0]), _ISOTONIC_CLIP[1])) for v in raw]
        raise ValueError(f"unknown calibration method {self.method!r}")


def _fit_platt(raw_probs: list[float], outcomes: list[float]) -> LogisticRegression:
    model = LogisticRegression()
    model.fit(np.array(raw_probs).reshape(-1, 1), np.array(outcomes))
    return model


def _fit_isotonic(raw_probs: list[float], outcomes: list[float]) -> IsotonicRegression:
    model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    model.fit(np.array(raw_probs), np.array(outcomes))
    return model


def select_calibration_method(
    raw_probs: list[float],
    outcomes: list[float],
    min_improvement: float = 0.0005,
    max_log_loss_regression: float = 0.02,
) -> tuple[FittedCalibrator, dict[str, dict[str, float]]]:
    """raw_probs/outcomes must be an inner model's genuinely out-of-sample
    predictions (never the final evaluation set — see module docstring).
    Fits Platt and isotonic calibrators on this data and compares each
    against the uncalibrated baseline on *both* Brier score and log loss:
    a method must improve Brier by more than `min_improvement` AND not
    worsen log loss by more than `max_log_loss_regression`, since Brier
    alone can look better even when a calibrator has pushed a few
    predictions dangerously close to 0/1 (see the isotonic clipping note
    above) — checking log loss too is what catches that failure mode.
    Returns (chosen_calibrator, {"none"|"platt"|"isotonic": {"brier": .., "log_loss": ..}})
    for transparency about what was compared. "none" if nothing clears both bars.
    """
    if not raw_probs:
        return FittedCalibrator(method="none"), {"none": {"brier": float("nan"), "log_loss": float("nan")}}

    non_draw_probs = [p for p, o in zip(raw_probs, outcomes) if o != 0.5]
    non_draw_outcomes = [o for o in outcomes if o != 0.5]

    results = {"none": {"brier": brier_score(raw_probs, outcomes), "log_loss": log_loss(raw_probs, outcomes)}}

    fitted: dict[str, object] = {}
    if len(set(non_draw_outcomes)) >= 2:  # need both classes present to fit either calibrator
        for method, fit_fn in (("platt", _fit_platt), ("isotonic", _fit_isotonic)):
            model = fit_fn(non_draw_probs, non_draw_outcomes)
            calibrator = FittedCalibrator(method=method, model=model)
            calibrated = calibrator.apply(raw_probs)
            results[method] = {"brier": brier_score(calibrated, outcomes), "log_loss": log_loss(calibrated, outcomes)}
            fitted[method] = model

    baseline = results["none"]
    candidates = {
        m: r for m, r in results.items()
        if m != "none"
        and (baseline["brier"] - r["brier"]) > min_improvement
        and (r["log_loss"] - baseline["log_loss"]) <= max_log_loss_regression
    }
    if not candidates:
        return FittedCalibrator(method="none"), results

    best_method = min(candidates, key=lambda m: candidates[m]["brier"])
    return FittedCalibrator(method=best_method, model=fitted[best_method]), results
