"""Research script (B2B Pricing Engine, item 9): rigorous historical study
of whether the disposal model's known positive bias for high-volume/elite
players survives after controlling for confounders.

READ-ONLY: never touches the promoted model. Reads the already-persisted
PlayerDisposalPrediction rows from the original 2016/2019-2025 evaluation
backtest (the same population elite_disposal_diagnostic.py's bucket
analysis already uses), joins in TOG from PlayerMatchStat, and fits a
plain OLS of the model's residual (predicted - actual) on:
  - player_historical_avg (this player's own mean ACTUAL disposals across
    their whole eval-period sample - ground truth, not model output)
  - games_of_history (this row's own sample-size feature)
  - season_year (fixed effect, via season dummies)
  - time_on_ground_pct (opponent/context proxy actually available on
    PlayerMatchStat; a genuine opponent-strength feature was not built in
    the time available for this pass - see report's stated limitation)

If player_historical_avg keeps a significant POSITIVE coefficient after
controlling for games_of_history and season, that's evidence the bias is
NOT simply the already-known/handled low-sample regression-to-mean effect
(which should be absorbed by games_of_history) - i.e. a genuine elite-
player-specific pattern, not (only) a sampling artefact.

Run: python -m scripts.elite_disposal_bias_study
"""

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.database import SessionLocal
from app.models import PlayerDisposalPrediction, PlayerMatchStat, PlayerModelRun


def load_data() -> pd.DataFrame:
    db = SessionLocal()
    try:
        run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True)))
        if run is None:
            raise SystemExit("No promoted disposal model run found.")
        preds = db.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == run.id)).all()
        rows = [
            {
                "player_id": p.player_id, "match_id": p.match_id, "season_year": p.season_year,
                "games_of_history": p.games_of_history, "predicted_mean": p.predicted_mean, "actual": p.actual_disposals,
            }
            for p in preds
        ]
        df = pd.DataFrame(rows)

        tog_rows = db.execute(
            select(PlayerMatchStat.player_id, PlayerMatchStat.match_id, PlayerMatchStat.time_on_ground_pct)
        ).all()
        tog = pd.DataFrame(tog_rows, columns=["player_id", "match_id", "tog"])
        df = df.merge(tog, on=["player_id", "match_id"], how="left")
        return df
    finally:
        db.close()


def main() -> None:
    df = load_data()
    df["residual"] = df["predicted_mean"] - df["actual"]  # positive = over-prediction, negative = under-prediction (conservative)
    player_avg = df.groupby("player_id")["actual"].transform("mean")
    df["player_historical_avg"] = player_avg
    df = df.dropna(subset=["tog"])

    print(f"n rows (with TOG available): {len(df):,} across {df['player_id'].nunique():,} players, seasons {sorted(df['season_year'].unique())}")
    print(f"Overall bias (predicted - actual): {df['residual'].mean():+.3f}")

    # --- raw bucket bias, for direct comparison with elite_disposal_diagnostic.py's already-known numbers ---
    bins = [0, 15, 22, 28, 100]
    labels = ["low_<15", "mid_15-22", "high_22-28", "elite_28+"]
    df["bucket"] = pd.cut(df["player_historical_avg"], bins=bins, labels=labels, right=False)
    print("\nRaw bucket bias (matches elite_disposal_diagnostic.py's methodology):")
    print(df.groupby("bucket", observed=True)["residual"].agg(["mean", "count"]))

    # --- controlled regression ---
    import statsmodels.formula.api as smf

    model_df = df.rename(columns={"time_on_ground_pct": "tog"}) if "time_on_ground_pct" in df.columns else df
    model_df["season_year"] = model_df["season_year"].astype(str)
    formula = "residual ~ player_historical_avg + games_of_history + tog + C(season_year)"
    result = smf.ols(formula, data=model_df).fit(cov_type="HC1")  # heteroskedasticity-robust SEs - residual variance plausibly scales with disposal volume
    print("\nOLS: residual ~ player_historical_avg + games_of_history + tog + season fixed effects")
    print(result.summary().tables[1])

    coef = result.params.get("player_historical_avg")
    pval = result.pvalues.get("player_historical_avg")
    print(f"\nplayer_historical_avg coefficient: {coef:+.4f} (p={pval:.4g})")
    if pval < 0.01 and coef > 0:
        print("-> Elite-player bias REMAINS statistically significant and positive after controlling for sample size, season, and TOG.")
    elif pval >= 0.05:
        print("-> Elite-player coefficient is not statistically distinguishable from zero once controls are added.")
    else:
        print("-> Coefficient is statistically significant but weaker/different sign than the raw bucket bias — controls explain some of it.")

    # --- games_of_history coefficient: is this really regression-to-mean (low-sample)? ---
    goh_coef = result.params.get("games_of_history")
    goh_p = result.pvalues.get("games_of_history")
    print(f"games_of_history coefficient: {goh_coef:+.5f} (p={goh_p:.4g}) — a negative, significant coefficient here would support a sample-size/regression-to-mean component distinct from the elite-average effect.")


if __name__ == "__main__":
    main()
