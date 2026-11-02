"""The pre-registered Stat Model V1 experiment (docs/NBA_STAT_MODEL_V1_PLAN.md).

Two stages, run separately:

    python -m app.nba.stats.experiment selection
    python -m app.nba.stats.experiment test

The selection stage drops every test-fold outcome before anything is computed.
Local database only: no registry rows and no saved models, one JSON report in
docs/nba_stat_model_v1/. V1's minutes take a while to fit, so --minutes-cache
keeps them in a file between stages; they hold no stat outcomes.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.nba.minutes import evaluation as mev
from app.nba.minutes.data import load_games, load_logs
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON, build_historical_dataset, history_logs
from app.nba.minutes.metrics import point_metrics
from app.nba.minutes.registry import code_version
from app.nba.minutes_v2.upper_bound import paired_bootstrap
from app.nba.stats import models as sm
from app.nba.stats.features import STATS, add_stat_history, load_stat_logs

REPORT_DIR = Path(__file__).resolve().parents[4] / "docs" / "nba_stat_model_v1"
DISTRIBUTION_FIRST_FOLD = 2021  # needs an earlier fold's out-of-sample predictions
THRESHOLDS = {"points": (10, 15, 20, 25, 30), "rebounds": (4, 6, 8, 10), "assists": (2, 4, 6, 8)}
COVERAGE_RANGE = (0.77, 0.83)
NEAR_LINE_TOLERANCE = 0.02
LIMITED_HISTORY_GAMES = 10


def seal_test_outcomes(frame: pd.DataFrame, stage: str) -> pd.DataFrame:
    """In the selection stage, test-fold stat outcomes do not exist."""
    if stage == "test":
        return frame
    keep = frame["season_start_year"] <= max(mev.SELECTION_FOLDS)
    return frame[keep].copy()


def stat_v1_predictions(rows: pd.DataFrame, stat: str, folds, log=print) -> tuple[pd.Series, pd.Series, dict]:
    """Walk-forward mean and rate. For fold S: half-life and shrinkage chosen on
    season S-1 by MAE, using priors from seasons before S-1; final priors from
    seasons before S."""
    mean = pd.Series(np.nan, index=rows.index)
    rate = pd.Series(np.nan, index=rows.index)
    meta: dict = {}
    for season in folds:
        val = rows[rows["season_start_year"] == season - 1]
        val_prior = sm.fit_prior_rates(rows[rows["season_start_year"] < season - 1], stat)
        # V1 has no out-of-sample minutes before its first fold, so the first
        # fold's validation season uses recent minutes instead.
        val_minutes = val["v1_minutes"].fillna(val[f"min_ewm{sm.RECENT_MINUTES_HALFLIFE}"])
        scores = []
        for h in sm.HALFLIFE_GRID:
            for k in sm.SHRINK_GRID:
                pred = sm.shrunk_rate(val, stat, val_prior, h, k) * val_minutes
                scores.append({"halflife": h, "shrink": k, "val_mae": float((pred - val[f"actual_{stat}"]).abs().mean())})
        best = min(scores, key=lambda s: s["val_mae"])
        prior = sm.fit_prior_rates(rows[rows["season_start_year"] < season], stat)
        target = rows["season_start_year"] == season
        rate.loc[target] = sm.shrunk_rate(rows[target], stat, prior, best["halflife"], best["shrink"])
        mean.loc[target] = rate[target] * rows.loc[target, "v1_minutes"]
        meta[season] = {"halflife": best["halflife"], "shrink": best["shrink"], "prior_rates": prior, "grid": scores}
        log(f"  {stat} fold {season}: h={best['halflife']} k={best['shrink']}")
    return mean, rate, meta


def _v1_minutes(data: pd.DataFrame, folds, cache: Path | None, log) -> pd.Series:
    """V1's walk-forward HGB, on the played eligible rows. Never reads a stat."""
    if cache is not None and cache.exists():
        saved = pd.read_pickle(cache)
    else:
        wf, _ = mev.walk_forward(data, ["hgb"], folds=folds, log=log)
        saved = wf[["player_id", "game_id", "hgb"]]
        if cache is not None:
            saved.to_pickle(cache)
    merged = data[["player_id", "game_id"]].merge(saved, on=["player_id", "game_id"], how="left", validate="one_to_one")
    return pd.Series(merged["hgb"].to_numpy(), index=data.index)


def _fold_alphas(y: pd.Series, mu: pd.Series, seasons: pd.Series, folds) -> dict:
    """For each fold, alphas from earlier folds' out-of-sample predictions."""
    out = {}
    for season in folds:
        prior = (seasons < season) & mu.notna()
        out[season] = {"single": sm.fit_alpha_single(y[prior], mu[prior]), "bucket": sm.fit_alpha_buckets(y[prior], mu[prior])}
    return out


def _alpha_columns(mu: pd.Series, seasons: pd.Series, alphas: dict) -> tuple[pd.Series, pd.Series]:
    single = pd.Series(np.nan, index=mu.index)
    bucket = pd.Series(np.nan, index=mu.index)
    for season, a in alphas.items():
        m = seasons == season
        single.loc[m] = a["single"]
        bucket.loc[m] = sm.alpha_for(mu[m], a["bucket"])
    return single, bucket


def calibration(prob, outcome, bins: int = 10) -> dict:
    prob = np.asarray(prob, dtype=float)
    outcome = np.asarray(outcome, dtype=float)
    edges = np.minimum((prob * bins).astype(int), bins - 1)
    ece = sum(abs(prob[edges == b].mean() - outcome[edges == b].mean()) * (edges == b).mean() for b in range(bins) if (edges == b).any())
    return {"n": int(len(prob)), "mean_predicted": float(prob.mean()), "observed": float(outcome.mean()), "brier": float(((prob - outcome) ** 2).mean()), "ece": float(ece)}


def near_lines(mu) -> dict[str, np.ndarray]:
    floor = np.floor(np.clip(np.asarray(mu, dtype=float), sm.MIN_MEAN, None))
    return {"below": floor - 0.5, "above": floor + 0.5}


def distribution_block(y, mu, alpha) -> dict:
    y = np.asarray(y, dtype=float)
    pit = sm.randomised_pit(y, mu, alpha)
    out = {
        "n": int(len(y)),
        "log_score": float(sm.log_pmf(y, mu, alpha).mean()),
        "crps": float(sm.crps(y, mu, alpha).mean()),
        "coverage_80": float(((pit >= 0.1) & (pit <= 0.9)).mean()),
        "near_mean_lines": {},
    }
    for side, line in near_lines(mu).items():
        # A negative line ("below" for a small mean) can't be priced or bet.
        ok = line > 0
        prob = 1.0 - sm.cdf(np.floor(line[ok]), np.asarray(mu)[ok], None if alpha is None else np.asarray(alpha)[ok])
        out["near_mean_lines"][side] = calibration(prob, y[ok] > line[ok])
    return out


def add_segments(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["seg_minutes_band"] = pd.cut(f["v1_minutes"], [-0.1, 15, 25, 32, 100], labels=["<15", "15-25", "25-32", "32+"]).astype(str)
    f["seg_season_type"] = f["season_type"]
    f["seg_early_season"] = np.where(f["season_games"] < 5, "first_5_games", "later")
    changed = (f["games_with_team"] < 10) & (f["career_games"] > f["games_with_team"])
    f["seg_team_change"] = np.where(f["traded"] == 1.0, "first_game_new_team", np.where(changed, "new_team_<10_games", "settled"))
    f["seg_history"] = np.where(f["career_games"] < LIMITED_HISTORY_GAMES, f"career_<{LIMITED_HISTORY_GAMES}", "established")
    f["seg_back_to_back"] = np.where(f["back_to_back"] == 1.0, "back_to_back", "rested")
    return f


SEGMENTS = ["seg_minutes_band", "seg_season_type", "seg_early_season", "seg_team_change", "seg_history", "seg_back_to_back"]


def evaluate_stat(rows: pd.DataFrame, stat: str, folds, selection_folds, test_folds, log=print) -> dict:
    y = rows[f"actual_{stat}"]
    seasons = rows["season_start_year"]
    means = {name: sm.baseline_mean(rows, stat, name) for name in sm.BASELINES}
    v1_mean, v1_rate, fold_meta = stat_v1_predictions(rows, stat, folds, log=log)
    means["stat_v1"] = v1_mean
    in_folds = seasons.isin(folds)
    if means["stat_v1"][in_folds].isna().any():
        raise ValueError(f"stat_v1 is undefined for an eligible {stat} row")
    hindsight = v1_rate * rows["minutes"]

    def split(mask, keys=tuple(means)) -> dict:
        out = {k: point_metrics(means[k][mask], y[mask]) for k in keys}
        out["hindsight_rate_x_actual_minutes"] = point_metrics(hindsight[mask], y[mask])
        return out

    selection_mask = seasons.isin(selection_folds)
    test_mask = seasons.isin(test_folds)
    report: dict = {
        "fold_parameters": {str(k): v for k, v in fold_meta.items()},
        "selection_means": split(selection_mask),
        "selection_by_fold": {str(s): point_metrics(means["stat_v1"][seasons == s], y[seasons == s]) for s in selection_folds},
    }
    clusters = pd.to_datetime(rows["scheduled_start"], utc=True).dt.strftime("%Y-%m-%d")

    def compare(mask, err_a, err_b):
        return paired_bootstrap(err_a[mask], err_b[mask], clusters[mask])

    report["selection_mean_vs_baselines"] = {b: compare(selection_mask, means[b] - y, v1_mean - y) for b in sm.BASELINES}

    # Distributions: alphas for each model from its own earlier predictions.
    dist_folds = [s for s in folds if s >= DISTRIBUTION_FIRST_FOLD]
    columns = {}
    for name, mu in means.items():
        scored = in_folds & mu.notna()
        columns[name] = _alpha_columns(mu.where(scored), seasons, _fold_alphas(y, mu.where(scored), seasons, dist_folds))

    def dist_blocks(mask) -> dict:
        single, bucket = columns["stat_v1"]
        out = {
            "stat_v1__poisson": distribution_block(y[mask], v1_mean[mask], None),
            "stat_v1__nb_single": distribution_block(y[mask], v1_mean[mask], single[mask]),
            "stat_v1__nb_bucket": distribution_block(y[mask], v1_mean[mask], bucket[mask]),
        }
        for b in sm.BASELINES:
            out[f"{b}__nb_bucket"] = distribution_block(y[mask], means[b][mask], columns[b][1][mask])
        return out

    selection_dist = seasons.isin([s for s in selection_folds if s >= DISTRIBUTION_FIRST_FOLD])
    report["selection_distributions"] = dist_blocks(selection_dist)

    v1_single, v1_bucket = columns["stat_v1"]

    def threshold_block(mask) -> dict:
        return {f"P(>={t})": calibration(sm.prob_at_least(t, v1_mean[mask], v1_bucket[mask]), y[mask] >= t) for t in THRESHOLDS[stat]}

    report["selection_thresholds"] = threshold_block(selection_dist)
    report["alphas_latest_fold"] = {"single": float(v1_single[seasons == dist_folds[-1]].iloc[0]), "bucket": {str(k): v for k, v in _fold_alphas(y, v1_mean.where(in_folds), seasons, [dist_folds[-1]])[dist_folds[-1]]["bucket"].items()}}

    if not test_folds:
        return report

    # --- test stage only --------------------------------------------------
    report["test_means"] = split(test_mask)
    report["test_by_fold"] = {str(s): point_metrics(v1_mean[seasons == s], y[seasons == s]) for s in test_folds}
    report["test_mean_vs_baselines"] = {b: compare(test_mask, means[b] - y, v1_mean - y) for b in sm.BASELINES}
    test_dist = dist_blocks(test_mask)
    report["test_distributions"] = test_dist
    report["test_thresholds"] = threshold_block(test_mask)

    nb = -sm.log_pmf(y[test_mask], v1_mean[test_mask], v1_bucket[test_mask])
    poisson = -sm.log_pmf(y[test_mask], v1_mean[test_mask], None)
    single = -sm.log_pmf(y[test_mask], v1_mean[test_mask], v1_single[test_mask])
    # paired_bootstrap compares absolute values, so shift each pair to be positive.
    shift = max(0.0, -float(min(nb.min(), poisson.min(), single.min()))) + 1.0
    t_clusters = clusters[test_mask]
    report["test_log_score_comparisons"] = {
        "nb_bucket_vs_poisson": paired_bootstrap(poisson + shift, nb + shift, t_clusters),
        "nb_bucket_vs_nb_single": paired_bootstrap(single + shift, nb + shift, t_clusters),
    }

    frame = add_segments(rows[test_mask].assign(_v1=v1_mean[test_mask], _best=means["ewma_10"][test_mask], _y=y[test_mask], _h=hindsight[test_mask]))
    report["test_segments"] = {
        seg: {str(k): {"stat_v1": point_metrics(g["_v1"], g["_y"]), "ewma_10": point_metrics(g["_best"], g["_y"]), "hindsight": point_metrics(g["_h"], g["_y"])} for k, g in frame.groupby(seg, observed=True)}
        for seg in SEGMENTS
    }

    report["decision"] = decide(report, test_dist)
    return report


def decide(report: dict, dist: dict) -> dict:
    """The plan's decision rules, per stat. Thresholds fixed before any test run."""
    mean_checks = {
        b: {
            "test_improvement": c["mean_improvement"],
            "ci95": c["ci95"],
            "selection_improvement": report["selection_mean_vs_baselines"][b]["mean_improvement"],
            "passes": c["ci95"][0] > 0 and report["selection_mean_vs_baselines"][b]["mean_improvement"] > 0,
        }
        for b, c in report["test_mean_vs_baselines"].items()
    }
    v1 = dist["stat_v1__nb_bucket"]
    logs = report["test_log_score_comparisons"]
    crps_ok = {b: v1["crps"] <= dist[f"{b}__nb_bucket"]["crps"] for b in sm.BASELINES}
    lines = {side: abs(v["mean_predicted"] - v["observed"]) for side, v in v1["near_mean_lines"].items()}
    checks = {
        "log_score_beats_poisson": logs["nb_bucket_vs_poisson"]["ci95"][0] > 0,
        "log_score_beats_nb_single": logs["nb_bucket_vs_nb_single"]["ci95"][0] > 0,
        "crps_no_worse_than_every_baseline": all(crps_ok.values()),
        "coverage_in_range": COVERAGE_RANGE[0] <= v1["coverage_80"] <= COVERAGE_RANGE[1],
        "near_mean_lines_within_tolerance": all(v <= NEAR_LINE_TOLERANCE for v in lines.values()),
    }
    return {
        "mean_adopted": all(c["passes"] for c in mean_checks.values()),
        "mean_checks": mean_checks,
        "distribution_adopted": all(checks.values()),
        "distribution_checks": checks,
        "crps_by_baseline": crps_ok,
        "near_mean_line_gaps": lines,
        "coverage_80": v1["coverage_80"],
    }


def build_rows(db, stage: str, cache: Path | None, log=print) -> pd.DataFrame:
    games = load_games(db)
    logs = history_logs(load_logs(db, min_season=DEFAULT_FIRST_SEASON), games, DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON)
    data = build_historical_dataset(games, logs)
    # Minutes only, on every fold: V1 reads no points, rebounds or assists.
    data["v1_minutes"] = _v1_minutes(data, mev.SELECTION_FOLDS + mev.TEST_FOLDS, cache, log)
    stat_logs = load_stat_logs(db, min_season=DEFAULT_FIRST_SEASON, max_season=DEFAULT_LAST_SEASON)
    if stage != "test":
        # Sealed before any stat reaches a frame: no test-fold stat is loaded.
        seasons = games.set_index("game_id")["season_start_year"]
        stat_logs = stat_logs[stat_logs["game_id"].map(seasons) <= max(mev.SELECTION_FOLDS)]
        data = seal_test_outcomes(data, stage)
    data = add_stat_history(data, stat_logs, games)
    rows = mev.model_rows(data).copy()
    if rows[[f"actual_{s}" for s in STATS]].isna().any().any():
        raise ValueError("a played row has no stat outcome")
    return rows


def run(db, stage: str, *, cache: Path | None = None, log=print) -> dict:
    if db.get_bind().dialect.name != "sqlite":
        raise RuntimeError("the stat model experiment is local-database research; refusing to run against a hosted database")
    started = datetime.now(timezone.utc)
    rows = build_rows(db, stage, cache, log)
    test_folds = mev.TEST_FOLDS if stage == "test" else ()
    folds = list(mev.SELECTION_FOLDS) + list(test_folds)
    log(f"{stage}: {len(rows)} played eligible rows")
    report: dict = {
        "experiment": f"nba stat model v1: {stage} stage",
        "plan": "docs/NBA_STAT_MODEL_V1_PLAN.md",
        "run_started": started.isoformat(),
        "code_version": code_version(),
        "folds": {"selection": list(mev.SELECTION_FOLDS), "test": list(test_folds)},
        "rows": {str(s): int((rows["season_start_year"] == s).sum()) for s in folds},
        "zero_minute_played_rows": int((rows["minutes"] == 0).sum()),
        "stats": {},
    }
    for stat in STATS:
        report["stats"][stat] = evaluate_stat(rows, stat, folds, mev.SELECTION_FOLDS, test_folds, log=log)
    report["finished"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{stage}-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    log(f"report: {path}")
    report["report_path"] = str(path)
    return report


def main(argv: list[str] | None = None) -> int:
    from app.database import SessionLocal

    parser = argparse.ArgumentParser(prog="python -m app.nba.stats.experiment")
    parser.add_argument("stage", choices=["selection", "test"])
    parser.add_argument("--minutes-cache", type=Path, default=None, help="file to keep V1's walk-forward minutes in between stages")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        run(db, args.stage, cache=args.minutes_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
