from app.modelling.bootstrap import BootstrapResult
from app.modelling.promotion import evaluate_promotion_rule


def _bootstrap(point_estimate, ci_low, ci_high) -> BootstrapResult:
    return BootstrapResult(point_estimate=point_estimate, ci_low=ci_low, ci_high=ci_high, n_resamples=2000)


def test_promotes_when_every_criterion_passes():
    decision = evaluate_promotion_rule(
        candidate_brier=0.19,
        elo_brier=0.21,
        candidate_log_loss=0.58,
        elo_log_loss=0.60,
        candidate_ece=0.03,
        brier_improvement_bootstrap=_bootstrap(0.02, 0.005, 0.035),
        season_brier_improvements=[True, True, True, False],
    )
    assert decision.promote is True


def test_rejects_when_brier_does_not_beat_elo():
    decision = evaluate_promotion_rule(
        candidate_brier=0.22,
        elo_brier=0.21,
        candidate_log_loss=0.58,
        elo_log_loss=0.60,
        candidate_ece=0.03,
        brier_improvement_bootstrap=_bootstrap(-0.01, -0.02, 0.0),
        season_brier_improvements=[True, False, True, False],
    )
    assert decision.promote is False
    assert any("FAIL" in r and "Brier" in r for r in decision.reasons)


def test_rejects_when_improvement_is_within_noise():
    # a positive point estimate, but the interval still straddles zero
    decision = evaluate_promotion_rule(
        candidate_brier=0.209,
        elo_brier=0.210,
        candidate_log_loss=0.58,
        elo_log_loss=0.60,
        candidate_ece=0.03,
        brier_improvement_bootstrap=_bootstrap(0.001, -0.005, 0.007),
        season_brier_improvements=[True, True, True, True],
    )
    assert decision.promote is False
    assert any("FAIL" in r and "noise" in r for r in decision.reasons)


def test_rejects_when_only_one_season_improved():
    decision = evaluate_promotion_rule(
        candidate_brier=0.19,
        elo_brier=0.21,
        candidate_log_loss=0.58,
        elo_log_loss=0.60,
        candidate_ece=0.03,
        brier_improvement_bootstrap=_bootstrap(0.02, 0.005, 0.035),
        season_brier_improvements=[True, False, False, False, False],
    )
    assert decision.promote is False
    assert any("FAIL" in r and "season" in r for r in decision.reasons)


def test_rejects_when_poorly_calibrated():
    decision = evaluate_promotion_rule(
        candidate_brier=0.19,
        elo_brier=0.21,
        candidate_log_loss=0.58,
        elo_log_loss=0.60,
        candidate_ece=0.15,
        brier_improvement_bootstrap=_bootstrap(0.02, 0.005, 0.035),
        season_brier_improvements=[True, True, True],
    )
    assert decision.promote is False
    assert any("FAIL" in r and "calibrat" in r for r in decision.reasons)


def test_rejects_when_log_loss_materially_worse():
    decision = evaluate_promotion_rule(
        candidate_brier=0.19,
        elo_brier=0.21,
        candidate_log_loss=0.70,  # much worse than Elo's 0.60
        elo_log_loss=0.60,
        candidate_ece=0.03,
        brier_improvement_bootstrap=_bootstrap(0.02, 0.005, 0.035),
        season_brier_improvements=[True, True, True],
    )
    assert decision.promote is False
    assert any("FAIL" in r and "log loss" in r for r in decision.reasons)


def test_all_reasons_reported_even_on_failure():
    decision = evaluate_promotion_rule(
        candidate_brier=0.22, elo_brier=0.21, candidate_log_loss=0.65, elo_log_loss=0.60,
        candidate_ece=None, brier_improvement_bootstrap=_bootstrap(-0.01, -0.02, 0.0),
        season_brier_improvements=[False, False],
    )
    assert len(decision.reasons) == 5  # every check reported, not just the first failure
