"""The explicit rule for whether a candidate model deserves to replace or
join Elo in the live prediction system. Deliberately conservative: a model
must clear every bar, not just one flattering metric, and the default when
evidence is mixed or thin is to keep Elo — see the Stage brief's "Do not
force a positive result."

This function only *decides*; it never changes what the live dashboard
uses (see app/edges/calculator.py, untouched by this stage) — promotion is
a recommendation surfaced in the Model Research page, not an automatic
switch.
"""

from dataclasses import dataclass

from app.modelling.bootstrap import BootstrapResult

# A model within this relative tolerance of Elo's log loss counts as
# "not materially worse" — allows a small give without letting a real
# regression slide through.
LOG_LOSS_TOLERANCE_PCT = 0.02
# Elo/Poisson's own real ECE values (0.030 / 0.053) are the reference point
# for "reasonably calibrated" — anything meaningfully worse than Poisson's
# is treated as poorly calibrated.
MAX_ACCEPTABLE_ECE = 0.08


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reasons: list[str]


def evaluate_promotion_rule(
    *,
    candidate_brier: float,
    elo_brier: float,
    candidate_log_loss: float,
    elo_log_loss: float,
    candidate_ece: float | None,
    brier_improvement_bootstrap: BootstrapResult,  # point_estimate = elo_brier - candidate_brier; positive = candidate better
    season_brier_improvements: list[bool],  # per evaluation season: did the candidate beat Elo on Brier that season?
) -> PromotionDecision:
    checks: list[tuple[bool, str]] = []

    beats_brier = candidate_brier < elo_brier
    checks.append((beats_brier, f"beats Elo on Brier score ({candidate_brier:.4f} vs {elo_brier:.4f})"))

    log_loss_ok = candidate_log_loss <= elo_log_loss * (1 + LOG_LOSS_TOLERANCE_PCT)
    checks.append(
        (log_loss_ok, f"does not materially worsen log loss ({candidate_log_loss:.4f} vs {elo_log_loss:.4f})")
    )

    calibration_ok = candidate_ece is not None and candidate_ece <= MAX_ACCEPTABLE_ECE
    checks.append((calibration_ok, f"is reasonably calibrated (ECE={candidate_ece})"))

    n_seasons = len(season_brier_improvements)
    n_improved = sum(season_brier_improvements)
    majority_seasons_improved = n_seasons > 0 and n_improved >= (n_seasons // 2 + 1)
    checks.append(
        (majority_seasons_improved, f"improves in a majority of seasons ({n_improved}/{n_seasons})")
    )

    not_noise = brier_improvement_bootstrap.excludes_zero and brier_improvement_bootstrap.point_estimate > 0
    checks.append(
        (
            not_noise,
            f"improvement is distinguishable from noise (95% bootstrap interval "
            f"[{brier_improvement_bootstrap.ci_low:.4f}, {brier_improvement_bootstrap.ci_high:.4f}])",
        )
    )

    promote = all(passed for passed, _ in checks)
    reasons = [f"{'PASS' if passed else 'FAIL'}: {desc}" for passed, desc in checks]
    return PromotionDecision(promote=promote, reasons=reasons)
