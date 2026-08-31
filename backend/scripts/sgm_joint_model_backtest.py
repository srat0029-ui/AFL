"""Same Game Multi (SGM) joint pricing — go/no-go validation (Phase 1 of
the SGM joint-pricing plan). Answers one question empirically: does the
conditional-Monte-Carlo joint model (app/player_modelling/sgm_dependence.py)
beat today's naive independence product on real, held-out data?

Method, matching this project's existing leakage/promotion discipline
(app/modelling/promotion.py, app/modelling/bootstrap.py):

1. FIT the dependence coefficients (disposals, goals) on seasons up to
   FIT_CUTOFF_YEAR, using already-persisted, already-walk-forward-correct
   eval-period predictions (PlayerDisposalPrediction/PlayerGoalPrediction —
   see those models' docstrings; no re-fitting of the underlying disposal/
   goal/Poisson models happens here, only the new dependence layer on top
   of them).
2. VALIDATE on seasons strictly after FIT_CUTOFF_YEAR — genuinely
   out-of-sample for the dependence coefficients themselves (the underlying
   models were already evaluated out-of-sample elsewhere; this is an
   additional holdout split on top of that, so the dependence layer isn't
   scored on the same rows it was fit from).
3. Reconstruct "pseudo-multis": for every validation-period player-match
   row, pair a realistic player-prop leg with that player's own team's H2H
   leg in the same match, and score both the joint model's probability and
   the naive independence product against what actually happened.
4. Run bootstrap_metric_difference (2,000 resamples) on Brier and log-loss.
   If the 95% CI excludes zero in the joint model's favour on BOTH metrics,
   the fitted coefficients are persisted to sgm_dependence_coefficients
   (upserted by market — same convention as PlayerModelRun's "recompute and
   replace" championship, not append-only) and a ModelPromotionEvent is
   logged (market="same_game_multi") so this evidence shows up in the
   existing Model Registry alongside every other promotion decision. If the
   CI does not exclude zero (or excludes it in naive's favour), nothing is
   persisted — the live pricing engine (app/pricing/same_game_pricing.py)
   has nothing to read and falls back to independence, exactly as it did
   before this script existed. Either outcome is a legitimate result of
   running this script; it does not force a win.
"""

import math
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.modelling.bootstrap import BootstrapResult, bootstrap_metric_difference
from app.modelling.metrics import brier_score, log_loss
from app.modelling.poisson_model import PoissonConfig, score_distribution
from app.models import (
    GoalModelRun,
    Match,
    PlayerDisposalPrediction,
    PlayerGoalPrediction,
    PlayerModelRun,
    PoissonMatchPrediction,
    SgmDependenceCoefficient,
)
from app.player_modelling.model_registry import record_promotion_event
from app.player_modelling.sgm_dependence import (
    DependenceCoeff,
    PlayerLegSpec,
    TeamLegSpec,
    fit_dependence,
    simulate_joint_probability,
)

SGM_MODEL_NAME = "sgm_joint_conditional_mc_v1"

FIT_CUTOFF_YEAR = 2023  # fit dependence coefficients on seasons <= this
BACKTEST_N_SIMULATIONS = 5_000  # per-row MC draws; aggregate precision comes from averaging over thousands of rows, not from any single row's estimate
MAX_DISPOSALS_BEHIND_LINE = 0.5  # disposals leg threshold = round(predicted_mean) - this, i.e. a market-realistic "X.5" line near the model's own mean


def _load_champion_run_id(db, run_cls, market: str) -> int | None:
    run = db.scalar(select(run_cls).where(run_cls.market == market, run_cls.is_promoted.is_(True)))
    return run.id if run is not None else None


def _load_poisson_predictions(db) -> dict[int, PoissonMatchPrediction]:
    rows = db.scalars(select(PoissonMatchPrediction)).all()
    return {row.match_id: row for row in rows}


def _load_matches(db) -> dict[int, Match]:
    rows = db.scalars(select(Match)).all()
    return {row.id: row for row in rows}


def _own_team_expected_and_actual_margin(poisson_row: PoissonMatchPrediction, is_home: bool) -> tuple[float, float]:
    sign = 1.0 if is_home else -1.0
    return sign * poisson_row.expected_margin, sign * poisson_row.actual_margin


def _own_team_expected_and_actual_score(poisson_row: PoissonMatchPrediction, match: Match, is_home: bool) -> tuple[float, float]:
    if is_home:
        expected = 6 * poisson_row.home_expected_goals + poisson_row.home_expected_behinds
        actual = float(match.home_score)
    else:
        expected = 6 * poisson_row.away_expected_goals + poisson_row.away_expected_behinds
        actual = float(match.away_score)
    return expected, actual


def build_disposal_rows(db, poisson_by_match: dict, matches_by_id: dict) -> list[dict]:
    run_id = _load_champion_run_id(db, PlayerModelRun, "player_disposals")
    if run_id is None:
        print("No promoted disposal model found - skipping disposals.")
        return []

    preds = db.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == run_id)).all()
    rows = []
    for p in preds:
        poisson_row = poisson_by_match.get(p.match_id)
        match = matches_by_id.get(p.match_id)
        if poisson_row is None or match is None:
            continue
        is_home = p.team_id == match.home_team_id
        expected_margin, actual_margin = _own_team_expected_and_actual_margin(poisson_row, is_home)
        rows.append({
            "season_year": p.season_year,
            "match_id": p.match_id,
            "team_id": p.team_id,
            "is_home": is_home,
            "predicted_mean": p.predicted_mean,
            "nb_alpha": p.nb_alpha,
            "actual_disposals": p.actual_disposals,
            "surprise": actual_margin - expected_margin,
            "residual": p.actual_disposals - p.predicted_mean,
        })
    return rows


def build_goal_rows(db, poisson_by_match: dict, matches_by_id: dict) -> list[dict]:
    run_id = _load_champion_run_id(db, GoalModelRun, "player_goals")
    if run_id is None:
        print("No promoted goal model found - skipping goals.")
        return []

    preds = db.scalars(select(PlayerGoalPrediction).where(PlayerGoalPrediction.model_run_id == run_id)).all()
    rows = []
    for p in preds:
        if p.distribution_kind != "hurdle" or p.p_score is None:
            continue  # dependence layer's "starting complexity" only shifts the hurdle's scoring probability - see sgm_dependence.py
        poisson_row = poisson_by_match.get(p.match_id)
        match = matches_by_id.get(p.match_id)
        if poisson_row is None or match is None:
            continue
        is_home = p.team_id == match.home_team_id
        expected_score, actual_score = _own_team_expected_and_actual_score(poisson_row, match, is_home)
        scored_indicator = 1.0 if p.actual_goals >= 1 else 0.0
        rows.append({
            "season_year": p.season_year,
            "match_id": p.match_id,
            "team_id": p.team_id,
            "is_home": is_home,
            "p_score": p.p_score,
            "mu_scored": p.mu_scored,
            "alpha_scored": p.alpha_scored,
            "actual_goals": p.actual_goals,
            "surprise": actual_score - expected_score,
            "residual": scored_indicator - p.p_score,
        })
    return rows


def _split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    fit_rows = [r for r in rows if r["season_year"] <= FIT_CUTOFF_YEAR]
    eval_rows = [r for r in rows if r["season_year"] > FIT_CUTOFF_YEAR]
    return fit_rows, eval_rows


def _report_metric(name: str, probs_joint: list[float], probs_naive: list[float], outcomes: list[float]) -> tuple[BootstrapResult, float, float]:
    metric_fn = brier_score if name == "brier" else log_loss
    joint_val = metric_fn(probs_joint, outcomes)
    naive_val = metric_fn(probs_naive, outcomes)
    result = bootstrap_metric_difference(probs_joint, probs_naive, outcomes, metric_fn)
    verdict = "JOINT MODEL WINS" if (result.excludes_zero and result.point_estimate < 0) else (
        "NAIVE WINS" if (result.excludes_zero and result.point_estimate > 0) else "NO SIGNIFICANT DIFFERENCE"
    )
    print(f"  {name}: joint={joint_val:.4f}  naive={naive_val:.4f}  diff={result.point_estimate:+.4f}  "
          f"95% CI=[{result.ci_low:+.4f}, {result.ci_high:+.4f}]  -> {verdict}")
    return result, joint_val, naive_val


def _joint_model_wins(result: BootstrapResult) -> bool:
    return result.excludes_zero and result.point_estimate < 0


def _upsert_coefficient(db, coeff: DependenceCoeff, fitted_at: datetime, model_version: str) -> None:
    existing = db.scalar(select(SgmDependenceCoefficient).where(SgmDependenceCoefficient.market == coeff.market))
    if existing is None:
        existing = SgmDependenceCoefficient(market=coeff.market)
        db.add(existing)
    existing.slope = coeff.slope
    existing.intercept = coeff.intercept
    existing.n_observations = coeff.n_observations
    existing.fit_cutoff_year = FIT_CUTOFF_YEAR
    existing.model_version = model_version
    existing.fitted_at = fitted_at


def main() -> None:
    db = SessionLocal()
    try:
        poisson_by_match = _load_poisson_predictions(db)
        matches_by_id = _load_matches(db)

        disposal_rows = build_disposal_rows(db, poisson_by_match, matches_by_id)
        goal_rows = build_goal_rows(db, poisson_by_match, matches_by_id)

        disp_fit, disp_eval = _split(disposal_rows)
        goal_fit, goal_eval = _split(goal_rows)

        disposal_coeff = fit_dependence([r["surprise"] for r in disp_fit], [r["residual"] for r in disp_fit], market="disposals")
        goal_coeff = fit_dependence([r["surprise"] for r in goal_fit], [r["residual"] for r in goal_fit], market="goals")

        print(f"Fit period: seasons <= {FIT_CUTOFF_YEAR}  |  Eval period: seasons > {FIT_CUTOFF_YEAR}\n")
        print(f"Disposal dependence: slope={disposal_coeff.slope:+.4f}  intercept={disposal_coeff.intercept:+.4f}  n={disposal_coeff.n_observations:,}")
        print(f"Goal dependence:     slope={goal_coeff.slope:+.4f}  intercept={goal_coeff.intercept:+.4f}  n={goal_coeff.n_observations:,}\n")

        probs_joint: list[float] = []
        probs_naive: list[float] = []
        outcomes: list[float] = []

        for r in disp_eval:
            poisson_row = poisson_by_match[r["match_id"]]
            match = matches_by_id[r["match_id"]]
            home_pmf, away_pmf = _score_pmfs(poisson_row)
            threshold = round(r["predicted_mean"]) - MAX_DISPOSALS_BEHIND_LINE
            team_leg = TeamLegSpec(market_type="h2h", is_home_team=r["is_home"])
            player_leg = PlayerLegSpec(market="disposals", is_home_team=r["is_home"], threshold=threshold, base_mu=r["predicted_mean"], nb_alpha=r["nb_alpha"], label="disposals")

            result = simulate_joint_probability(
                home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=poisson_row.expected_margin,
                home_expected_score=6 * poisson_row.home_expected_goals + poisson_row.home_expected_behinds,
                away_expected_score=6 * poisson_row.away_expected_goals + poisson_row.away_expected_behinds,
                team_leg=team_leg, player_legs=[player_leg],
                disposal_coeff=disposal_coeff, goal_coeff=goal_coeff,
                n_simulations=BACKTEST_N_SIMULATIONS, seed=42,
            )

            team_hit = (match.home_score > match.away_score) if r["is_home"] else (match.away_score > match.home_score)
            player_hit = r["actual_disposals"] >= math.ceil(threshold)
            probs_joint.append(result.model_probability)
            probs_naive.append(result.naive_independence_probability)
            outcomes.append(1.0 if (team_hit and player_hit) else 0.0)

        for r in goal_eval:
            poisson_row = poisson_by_match[r["match_id"]]
            match = matches_by_id[r["match_id"]]
            home_pmf, away_pmf = _score_pmfs(poisson_row)
            threshold = 0.5  # standard "anytime goalscorer" (1+ goals) line
            team_leg = TeamLegSpec(market_type="h2h", is_home_team=r["is_home"])
            player_leg = PlayerLegSpec(market="goals", is_home_team=r["is_home"], threshold=threshold, p_score=r["p_score"], mu_scored=r["mu_scored"], alpha_scored=r["alpha_scored"], label="goals")

            result = simulate_joint_probability(
                home_pmf=home_pmf, away_pmf=away_pmf, expected_margin=poisson_row.expected_margin,
                home_expected_score=6 * poisson_row.home_expected_goals + poisson_row.home_expected_behinds,
                away_expected_score=6 * poisson_row.away_expected_goals + poisson_row.away_expected_behinds,
                team_leg=team_leg, player_legs=[player_leg],
                disposal_coeff=disposal_coeff, goal_coeff=goal_coeff,
                n_simulations=BACKTEST_N_SIMULATIONS, seed=42,
            )

            team_hit = (match.home_score > match.away_score) if r["is_home"] else (match.away_score > match.home_score)
            player_hit = r["actual_goals"] >= 1
            probs_joint.append(result.model_probability)
            probs_naive.append(result.naive_independence_probability)
            outcomes.append(1.0 if (team_hit and player_hit) else 0.0)

        n = len(outcomes)
        print(f"Pseudo-multis scored: {n:,} ({len(disp_eval):,} disposals-combo, {len(goal_eval):,} goals-combo)\n")
        if n == 0:
            print("No eval-period rows available - cannot validate.")
            return

        brier_result, brier_joint, brier_naive = _report_metric("brier", probs_joint, probs_naive, outcomes)
        logloss_result, logloss_joint, logloss_naive = _report_metric("log_loss", probs_joint, probs_naive, outcomes)

        if _joint_model_wins(brier_result) and _joint_model_wins(logloss_result):
            fitted_at = datetime.now(timezone.utc)
            model_version = f"{SGM_MODEL_NAME}@{fitted_at.isoformat()}"
            _upsert_coefficient(db, disposal_coeff, fitted_at, model_version)
            _upsert_coefficient(db, goal_coeff, fitted_at, model_version)
            db.commit()

            evidence = (
                f"Validated on {n:,} out-of-sample pseudo-multis (seasons > {FIT_CUTOFF_YEAR}): "
                f"Brier {brier_joint:.4f} vs naive {brier_naive:.4f} (diff {brier_result.point_estimate:+.4f}, "
                f"95% CI [{brier_result.ci_low:+.4f}, {brier_result.ci_high:+.4f}]); "
                f"log-loss {logloss_joint:.4f} vs naive {logloss_naive:.4f} (diff {logloss_result.point_estimate:+.4f}, "
                f"95% CI [{logloss_result.ci_low:+.4f}, {logloss_result.ci_high:+.4f}]). "
                f"Effect size is small (disposals slope {disposal_coeff.slope:+.4f}, goals slope {goal_coeff.slope:+.4f}) "
                f"but the improvement over naive independence is statistically distinguishable on both proper scoring rules."
            )
            record_promotion_event(
                db, market="same_game_multi", previous_champion_model_name=None, previous_champion_model_version=None,
                new_champion_model_name=SGM_MODEL_NAME, new_champion_model_version=model_version,
                promoted_at=fitted_at, evidence_summary=evidence,
                evaluation_metrics={
                    "n_pseudo_multis": n, "brier_joint": brier_joint, "brier_naive": brier_naive,
                    "brier_diff": brier_result.point_estimate, "brier_ci": [brier_result.ci_low, brier_result.ci_high],
                    "log_loss_joint": logloss_joint, "log_loss_naive": logloss_naive,
                    "log_loss_diff": logloss_result.point_estimate, "log_loss_ci": [logloss_result.ci_low, logloss_result.ci_high],
                    "disposal_slope": disposal_coeff.slope, "goal_slope": goal_coeff.slope,
                },
            )
            print(f"\nPersisted dependence coefficients and logged promotion event: {model_version}")
        else:
            print("\nNot persisted - joint model did not beat naive independence on both metrics.")

    finally:
        db.close()


_POISSON_CONFIG = PoissonConfig()


def _score_pmfs(poisson_row: PoissonMatchPrediction):
    home_pmf = score_distribution(poisson_row.home_expected_goals, poisson_row.home_expected_behinds, _POISSON_CONFIG.max_goals, _POISSON_CONFIG.max_behinds)
    away_pmf = score_distribution(poisson_row.away_expected_goals, poisson_row.away_expected_behinds, _POISSON_CONFIG.max_goals, _POISSON_CONFIG.max_behinds)
    return home_pmf, away_pmf


if __name__ == "__main__":
    main()
