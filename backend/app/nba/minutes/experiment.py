"""The full Model V1 experiment, end to end, following the pre-registered
protocol in evaluation.py:

1. build the historical dataset (2018-19..2025-26) and its coverage;
2. prove on a stratified sample of real rows that features computed from
   the full data equal features computed from data truncated at each row's
   cutoff (no lookahead);
3. walk-forward evaluate every baseline and candidate (folds 2020-21..2025-26);
4. season holdout (train through 2023-24, test 2024-25 and 2025-26);
5. feature-group ablation on the selection folds;
6. select Model V1 on pooled selection-fold MAE (mean estimators only);
7. residual / uncertainty analysis of the selected model;
8. fit the serving model on all modelled seasons and record everything.

Every run is recorded as a NEW append-only NbaMinutesModelRun row and the
full report is written to a new timestamped JSON file - nothing earlier is
overwritten.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sqlalchemy.orm import Session

from app.nba.minutes import evaluation as ev
from app.nba.minutes import residuals as rs
from app.nba.minutes.data import load_games, load_logs
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON, build_historical_dataset, history_logs
from app.nba.minutes.features import ALL_FEATURES, FEATURE_GROUPS, lookahead_violations
from app.nba.minutes.metrics import point_metrics
from app.nba.minutes.models import BASELINES, CANDIDATES, DropEmptyColumns, fit_predict
from app.nba.minutes.registry import MODEL_ARTIFACT_DIR, code_version, record_run, save_model

MODEL_VERSION = "minutes-v1.0"
REPORT_DIR = Path(__file__).resolve().parents[4] / "docs" / "nba_minutes_v1"
SELECTABLE = ("ridge", "hgb", "lgbm", "random_forest")  # mean estimators; hgb_l1 (a median estimator) is reported only
SIMPLICITY_ORDER = ("ridge", "random_forest", "hgb", "lgbm")
TIE_TOLERANCE = 0.02
ABLATION_PARAMS = {"max_leaf_nodes": 31, "max_iter": 400, "learning_rate": 0.05}


def _stratified_sample(data: pd.DataFrame, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    pools = {
        "random": data,
        "traded": data[data["traded"] == 1.0],
        "playoffs": data[data["season_type"] == "playoffs"],
        "season_opener": data[data["season_games"] == 0],
        "returning": data[data["team_games_missed"] >= 5],
        "did_not_play": data[data["did_not_play"]],
        "debut_or_no_history": data[~data["eligible"]],
    }
    picks = []
    for name, pool in pools.items():
        n = 10 if name == "random" else 5
        idx = rng.choice(pool.index.to_numpy(), size=min(n, len(pool)), replace=False)
        picks.append(data.loc[idx].assign(_stratum=name))
    return pd.concat(picks)


def _coverage(data: pd.DataFrame) -> dict:
    played = data[data["played"]]
    per_season = {}
    for season, g in played.groupby("season_start_year"):
        per_season[int(season)] = {"played_rows": int(len(g)), "eligible": int(g["eligible"].sum()), "coverage": float(g["eligible"].mean())}
    listed = data
    return {
        "listed_rows": int(len(listed)),
        "did_not_play_rows": int(listed["did_not_play"].sum()),
        "played_rows": int(len(played)),
        "played_minutes_missing": int((~listed["did_not_play"] & listed["minutes"].isna() & ~listed["played"]).sum()),
        "eligible_played_rows": int(played["eligible"].sum()),
        "coverage": float(played["eligible"].mean()),
        "per_season": per_season,
    }


def _select(wf_summary_selection: dict) -> tuple[str, list]:
    ranked = sorted(SELECTABLE, key=lambda m: wf_summary_selection[m]["mae"])
    best_mae = wf_summary_selection[ranked[0]]["mae"]
    tied = [m for m in ranked if wf_summary_selection[m]["mae"] - best_mae <= TIE_TOLERANCE]
    chosen = min(tied, key=SIMPLICITY_ORDER.index)
    return chosen, [{"model": m, "pooled_selection_mae": wf_summary_selection[m]["mae"]} for m in ranked]


def run_experiment(db: Session, *, log=print, check_rows: bool = True) -> dict:
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    games = load_games(db)
    logs = history_logs(load_logs(db, min_season=DEFAULT_FIRST_SEASON), games, DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON)
    data = build_historical_dataset(games, logs)
    dataset_cutoff = games.loc[games["game_id"].isin(logs["game_id"]), "scheduled_start"].max()
    report: dict = {
        "model_version": MODEL_VERSION,
        "run_started": started.isoformat(),
        "code_version": code_version(),
        "seasons": list(range(DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON + 1)),
        "dataset_cutoff": str(dataset_cutoff),
        "features": ALL_FEATURES,
        "feature_groups": FEATURE_GROUPS,
        "coverage": _coverage(data),
        "protocol": {"selection_folds": list(ev.SELECTION_FOLDS), "test_folds": list(ev.TEST_FOLDS), "selectable": list(SELECTABLE), "tie_tolerance": TIE_TOLERANCE},
    }
    log(f"dataset: {len(data)} listed rows, {report['coverage']['played_rows']} played, coverage {report['coverage']['coverage']:.4f}")

    if check_rows:
        sample = _stratified_sample(data)
        violations = lookahead_violations(games, logs, sample[["player_id", "game_id", "team_id"]].reset_index(drop=True))
        report["lookahead_check"] = {"rows_checked": int(len(sample)), "strata": sample["_stratum"].value_counts().to_dict(), "violations": violations}
        log(f"lookahead check: {len(sample)} rows, {len(violations)} violations")
        if violations:
            raise AssertionError(f"lookahead violations found: {violations[:5]}")

    names = list(CANDIDATES)
    wf, wf_meta = ev.walk_forward(data, names, log=log)
    cols = list(BASELINES) + names
    report["walk_forward"] = {
        "per_fold": {int(s): ev.summarize(wf, cols, [s]) for s in ev.SELECTION_FOLDS + ev.TEST_FOLDS},
        "pooled_selection": ev.summarize(wf, cols, ev.SELECTION_FOLDS),
        "pooled_test": ev.summarize(wf, cols, ev.TEST_FOLDS),
        "pooled_test_unconditional_dnp_as_zero": ev.summarize(wf, cols, ev.TEST_FOLDS, target="minutes_or_zero"),
        "fold_meta": {int(k): v for k, v in wf_meta.items()},
    }
    chosen, ranking = _select(report["walk_forward"]["pooled_selection"])
    report["selection"] = {"chosen": chosen, "ranking_on_selection_folds": ranking}
    log(f"selected: {chosen}")
    report["segments_test"] = ev.segment_report(wf, cols, ev.TEST_FOLDS)

    ho, ho_meta = ev.season_holdout(data, names, log=log)
    report["season_holdout"] = {"per_season": {int(s): ev.summarize(ho, cols, [s]) for s in ev.TEST_FOLDS}, "pooled": ev.summarize(ho, cols, ev.TEST_FOLDS), "meta": ho_meta}

    # Feature-group ablation (cumulative), selection folds only.
    rows = ev.model_rows(data)
    ablation = {}
    groups = list(FEATURE_GROUPS)
    for k in range(1, len(groups) + 1):
        feats = [f for g in groups[:k] for f in FEATURE_GROUPS[g]]
        preds, actual = [], []
        for season in ev.SELECTION_FOLDS:
            tr, te = rows[rows["season_start_year"] < season], rows[rows["season_start_year"] == season]
            _, p = fit_predict(CANDIDATES["hgb"], ABLATION_PARAMS, tr, te, feats)
            preds.append(p)
            actual.append(te["minutes"].to_numpy())
        ablation[" + ".join(groups[:k])] = point_metrics(np.concatenate(preds), np.concatenate(actual))
        log(f"ablation {groups[:k]}: mae {ablation[' + '.join(groups[:k])]['mae']:.3f}")
    report["ablation_hgb_selection_folds"] = ablation

    # Residuals and uncertainty (selected model, out-of-sample walk-forward predictions).
    played_wf = wf[wf["played"]]
    sel_rows = played_wf[played_wf["season_start_year"].isin(ev.SELECTION_FOLDS)]
    test_rows = played_wf[played_wf["season_start_year"].isin(ev.TEST_FOLDS)]
    resid_all = played_wf["minutes"] - played_wf[chosen]
    table_sel = rs.fit_uncertainty_table(sel_rows[chosen], sel_rows["minutes"])
    seg = ev.add_segments(played_wf)
    report["residuals"] = {
        "definition": "actual - predicted, out-of-sample walk-forward predictions 2020-21..2025-26",
        "overall": rs.describe_residuals(resid_all),
        "by_predicted_band": rs.by_group(resid_all, rs.pred_band(played_wf[chosen]).to_numpy()),
        "by_stability": rs.by_group(resid_all, seg["seg_stability"].to_numpy()),
        "by_role": rs.by_group(resid_all, seg["seg_role"].to_numpy()),
        "uncertainty_table_from_selection_folds": table_sel,
        "interval_coverage_on_test_folds": rs.interval_coverage(test_rows[chosen], test_rows["minutes"], table_sel),
    }

    # Ineligible rows: a constant fallback, reported separately.
    no_hist = data[data["played"] & ~data["eligible"]]
    fallback_value = float(no_hist[no_hist["season_start_year"] < ev.TEST_FOLDS[0]]["minutes"].median())
    test_no_hist = no_hist[no_hist["season_start_year"].isin(ev.TEST_FOLDS)]
    report["ineligible_fallback"] = {
        "rule": "median minutes of no-history played rows in seasons before the test folds",
        "value": fallback_value,
        "test_metrics": point_metrics(np.full(len(test_no_hist), fallback_value), test_no_hist["minutes"]),
    }

    # Interpretability: permutation importance of the selected model on one selection fold.
    tr, te = rows[rows["season_start_year"] < 2023], rows[rows["season_start_year"] == 2023]
    params = wf_meta[2023]["models"][chosen]["params"]
    model, _ = fit_predict(CANDIDATES[chosen], params, tr, te, ALL_FEATURES)
    sample = te.sample(min(15000, len(te)), random_state=0)
    imp = permutation_importance(model, sample[ALL_FEATURES].to_numpy(dtype=float), sample["minutes"].to_numpy(), scoring="neg_mean_absolute_error", n_repeats=3, random_state=0)
    report["permutation_importance_2023"] = dict(sorted(((f, float(m)) for f, m in zip(ALL_FEATURES, imp.importances_mean)), key=lambda kv: -kv[1]))

    # Serving model: all modelled seasons, hyperparameters tuned on the last season.
    serve_params, serve_tuning = ev.tune(chosen, rows, list(range(DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON)), DEFAULT_LAST_SEASON, ALL_FEATURES)
    serve_model = DropEmptyColumns(CANDIDATES[chosen].build(serve_params))
    serve_model.fit(rows[ALL_FEATURES].to_numpy(dtype=float), rows["minutes"].to_numpy(dtype=float))
    table_all = rs.fit_uncertainty_table(played_wf[chosen], played_wf["minutes"])
    artifact_path, artifact_sha = save_model(serve_model, f"{MODEL_VERSION}-{chosen}-{stamp}")
    report["serving"] = {"model": chosen, "params": serve_params, "tuning": serve_tuning, "trained_rows": int(len(rows)), "artifact_sha256": artifact_sha, "uncertainty": table_all}

    # Row-level predictions/residuals (large: kept out of git, alongside the model files).
    MODEL_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    keep = ["player_id", "game_id", "team_id", "season_start_year", "season_type", "played", "minutes", "minutes_or_zero"] + cols
    wf[keep].to_csv(MODEL_ARTIFACT_DIR / f"walk_forward_predictions-{stamp}.csv.gz", index=False)

    # Append-only registry: one evaluation run per baseline/candidate, plus the serving run.
    seasons = report["seasons"]
    for name in cols:
        is_baseline = name in BASELINES
        record_run(
            db,
            run_key=f"{MODEL_VERSION}:{name}:evaluation:{stamp}",
            model_name=name,
            model_version=MODEL_VERSION,
            purpose="evaluation",
            dataset_cutoff=dataset_cutoff.to_pydatetime().replace(tzinfo=timezone.utc),
            training_seasons=seasons[:-1],
            evaluation_seasons=list(ev.SELECTION_FOLDS + ev.TEST_FOLDS),
            features=[] if is_baseline else ALL_FEATURES,
            hyperparameters={} if is_baseline else {"walk_forward": {int(s): wf_meta[s]["models"][name]["params"] for s in wf_meta}, "holdout": ho_meta["models"][name]["params"]},
            metrics={
                "walk_forward_per_fold": {int(s): report["walk_forward"]["per_fold"][s][name] for s in report["walk_forward"]["per_fold"]},
                "walk_forward_pooled_selection": report["walk_forward"]["pooled_selection"][name],
                "walk_forward_pooled_test": report["walk_forward"]["pooled_test"][name],
                "season_holdout": {int(s): report["season_holdout"]["per_season"][s][name] for s in ev.TEST_FOLDS},
            },
            notes=("baseline: " if is_baseline else "candidate: ") + (CANDIDATES[name].description if not is_baseline else name),
        )
    serving_run = record_run(
        db,
        run_key=f"{MODEL_VERSION}:{chosen}:serving:{stamp}",
        model_name=chosen,
        model_version=MODEL_VERSION,
        purpose="serving",
        dataset_cutoff=dataset_cutoff.to_pydatetime().replace(tzinfo=timezone.utc),
        training_seasons=seasons,
        evaluation_seasons=[],
        features=ALL_FEATURES,
        hyperparameters=serve_params,
        metrics={"selection": report["selection"], "walk_forward_pooled_test": report["walk_forward"]["pooled_test"][chosen]},
        uncertainty=table_all,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha,
        notes="Model V1 serving model: fitted on every eligible played row 2018-19..2025-26. Injury/availability evidence deliberately excluded.",
    )
    db.commit()
    report["serving"]["model_run_id"] = serving_run.id
    report["finished"] = datetime.now(timezone.utc).isoformat()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"report-{stamp}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["report_path"] = str(path)
    log(f"report: {path}")
    return report
