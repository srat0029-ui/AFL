"""The full participation / rotation / reconciliation experiment ("V1.5").

Two candidate universes (see dataset.py):
- "roster" - PRIMARY. The pregame roster reconstructed from history alone.
  Every model choice and the reconciliation adoption decision are made here.
- "listed" - SCENARIO. The box-score listing, which is in practice the
  official active list (published ~30 min before tip-off). Reported as a
  labelled "active list known" upper bound; never used for V1.5 serving.

Pre-registered protocol (same folds as V1):
- Selection folds 2020-21..2023-24; test folds 2024-25 and 2025-26 report only.
- Classifier: lowest pooled selection-fold LOG LOSS; within 0.002 of the best
  is a tie and goes to the simpler model (logistic < hgb < lgbm).
- Rotation target: >= 10 minutes, for its basketball meaning (the trough
  between the cameo and rotation modes of the minutes distribution; ~9
  players per team reach it - a normal rotation); >= 5 is reported alongside.
- Reconciliation: folds 2021-22..2025-26 (it needs V1 residual spreads from
  EARLIER walk-forward folds); configuration chosen on pooled selection-fold
  (2021-22..2023-24) MAE of conditional minutes on played rows; ADOPTED only if
  it beats raw V1 there by more than 0.01 minutes without worsening RMSE.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.nba.minutes import evaluation as mev
from app.nba.minutes.data import load_games, load_logs
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON, history_logs
from app.nba.minutes.features import ALL_FEATURES, build_features, lookahead_violations, truncate_to_cutoff
from app.nba.minutes.metrics import point_metrics
from app.nba.minutes.models import CANDIDATES as MINUTES_CANDIDATES
from app.nba.minutes.models import fit_predict as minutes_fit_predict
from app.nba.minutes.registry import code_version, record_run, save_model
from app.nba.minutes.residuals import BAND_LABELS, pred_band
from app.nba.rotation import evaluation as rev
from app.nba.rotation.allocation import band_sigma, reconcile_frame, team_totals
from app.nba.rotation.dataset import UNIVERSE_LISTED, UNIVERSE_ROSTER, build_rotation_dataset
from app.nba.rotation.features import EXTRA_FEATURES, PARTICIPATION_FEATURES, add_participation_features
from app.nba.rotation.models import BASELINES, CLASSIFIERS, DropEmptyColumnsClassifier
from app.nba.rotation.overtime import OvertimeModel, overtime_periods
from app.nba.rotation.universe import reconstructed_roster

MODEL_VERSION = "rotation-v1.0"
REPORT_DIR = Path(__file__).resolve().parents[4] / "docs" / "nba_rotation_v1"
SIMPLICITY = ("logistic", "hgb", "lgbm")
LOGLOSS_TIE = 0.002
ADOPT_MAE_MARGIN = 0.01
RECON_SEL = (2021, 2022, 2023)
RECON_TEST = mev.TEST_FOLDS
ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
BETTABLE_EDGES = [-0.1, 10, 20, 25, 30, 34, 61]
BETTABLE_LABELS = ["<10", "10-20", "20-25", "25-30", "30-34", "34+"]
ALL_FOLDS = mev.SELECTION_FOLDS + mev.TEST_FOLDS


def _select_clf(summary: dict) -> tuple[str, list]:
    ranked = sorted(SIMPLICITY, key=lambda m: summary[m]["log_loss"])
    best = summary[ranked[0]]["log_loss"]
    tied = [m for m in ranked if summary[m]["log_loss"] - best <= LOGLOSS_TIE]
    return min(tied, key=SIMPLICITY.index), [{"model": m, "pooled_selection_log_loss": summary[m]["log_loss"]} for m in ranked]


def _classification_block(data, target, log):
    names = list(CLASSIFIERS)
    preds, meta = rev.walk_forward_clf(data, target, names, log=log)
    cols = list(BASELINES) + names
    chosen, ranking = _select_clf(rev.summarize_clf(preds, cols, target, mev.SELECTION_FOLDS))
    recent = preds[preds["team_games_unlisted"] <= 2]
    block = {
        "per_fold": {int(s): rev.summarize_clf(preds, cols, target, [s]) for s in ALL_FOLDS},
        "pooled_selection": rev.summarize_clf(preds, cols, target, mev.SELECTION_FOLDS),
        "pooled_test": rev.summarize_clf(preds, cols, target, mev.TEST_FOLDS),
        "pooled_test_recently_listed": rev.summarize_clf(recent, cols, target, mev.TEST_FOLDS),
        "chosen": chosen,
        "ranking": ranking,
        "calibration_test": rev.calibration_report(preds, chosen, target, mev.TEST_FOLDS),
        "calibration_test_recently_listed": rev.calibration_report(recent, chosen, target, mev.TEST_FOLDS),
        "segments_test": rev.segment_clf(preds, ["recent_rate_calibrated", chosen], target, mev.TEST_FOLDS),
        "fold_params": {int(s): {n: m["params"] for n, m in v["models"].items()} for s, v in meta.items()},
    }
    return preds, block


def _v1_predictions(listed: pd.DataFrame, frame: pd.DataFrame, log) -> tuple[pd.Series, pd.DataFrame]:
    """Exact V1 walk-forward (on the listed data, as V1 was built), then the
    same per-fold models applied to every row of `frame`."""
    wf, meta = mev.walk_forward(listed, ["hgb"], log=log)
    rows = mev.model_rows(listed)
    out = pd.Series(np.nan, index=frame.index)
    for s in ALL_FOLDS:
        test = frame[(frame["season_start_year"] == s) & frame["eligible"].astype(bool)]
        _, pred = minutes_fit_predict(MINUTES_CANDIDATES["hgb"], meta[s]["models"]["hgb"]["params"], rows[rows["season_start_year"] < s], test, ALL_FEATURES)
        out.loc[test.index] = pred
    return out, wf


def _sigma_tables(wf: pd.DataFrame) -> dict:
    played = wf[wf["played"]]
    tables = {}
    for s in RECON_SEL + RECON_TEST:
        prior = played[played["season_start_year"] < s]
        r = prior["minutes"] - prior["hgb"]
        bands = pred_band(prior["hgb"]).to_numpy()
        tables[s] = {b: float(r[bands == b].std(ddof=1)) for b in BAND_LABELS}
    return tables


def _reconciliation(frame: pd.DataFrame, sig: dict, log, label: str) -> tuple[pd.DataFrame, dict]:
    recon = frame[frame["season_start_year"].isin(RECON_SEL + RECON_TEST)].copy()
    recon["sigma"] = np.nan
    for s in RECON_SEL + RECON_TEST:
        m = recon["season_start_year"] == s
        recon.loc[m, "sigma"] = band_sigma(recon.loc[m, "m_v1"], sig[s])
    grid = []
    played = recon["played"]
    sel = played & recon["season_start_year"].isin(RECON_SEL)
    tst = played & recon["season_start_year"].isin(RECON_TEST)
    for method in ("variance", "proportional"):
        for budget in ("strict_240", "ot_constant", "ot_matchup"):
            for alpha in ALPHAS:
                if alpha == 0.0 and (method, budget) != ("variance", "strict_240"):
                    continue
                adj = reconcile_frame(recon, m_col="m_v1", p_col="p_play", sigma_col="sigma", budget_col=f"budget_{budget}", alpha=alpha, method=method)
                recon[f"adj_{method}_{budget}_{alpha}"] = adj
                grid.append({"method": method, "budget": budget, "alpha": alpha, "selection": point_metrics(adj[sel], recon.loc[sel, "minutes"]), "test": point_metrics(adj[tst], recon.loc[tst, "minutes"])})
    raw = grid[0]
    best = min(grid, key=lambda g: g["selection"]["mae"])
    adopted = best["alpha"] > 0 and raw["selection"]["mae"] - best["selection"]["mae"] > ADOPT_MAE_MARGIN and best["selection"]["rmse"] <= raw["selection"]["rmse"]
    recon["m_recon"] = recon[f"adj_{best['method']}_{best['budget']}_{best['alpha']}"]
    log(f"[{label}] reconciliation best {best['method']}/{best['budget']}/a={best['alpha']}: sel MAE {best['selection']['mae']:.4f} vs raw {raw['selection']['mae']:.4f} -> adopted={adopted}")

    def totals(col):
        t = team_totals(recon[recon["season_start_year"].isin(RECON_TEST)], col, "p_play")
        d = t["expected"] - t["actual"]
        return {"mean_expected": float(t["expected"].mean()), "mean_actual": float(t["actual"].mean()), "mean_error": float(d.mean()), "mae": float(d.abs().mean()),
                "p05_expected": float(t["expected"].quantile(0.05)), "p95_expected": float(t["expected"].quantile(0.95)), "candidates_per_team_game": float(t["listed"].mean())}

    return recon, {"grid": grid, "raw": raw, "best_on_selection": best, "adopted": bool(adopted),
                   "rule": f"adopt only if selection MAE improves by > {ADOPT_MAE_MARGIN} and RMSE does not worsen",
                   "team_totals_test": {"raw": totals("m_v1"), "reconciled": totals("m_recon")}}


def run_experiment(db, *, log=print, check_rows: bool = True) -> dict:
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    games = load_games(db)
    logs = history_logs(load_logs(db, min_season=DEFAULT_FIRST_SEASON), games, DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON)
    roster = build_rotation_dataset(games, logs, universe=UNIVERSE_ROSTER)
    listed = build_rotation_dataset(games, logs, universe=UNIVERSE_LISTED)
    dataset_cutoff = games.loc[games["game_id"].isin(logs["game_id"]), "scheduled_start"].max()

    # Listings the reconstructed roster could not foresee (first listing for a new team).
    final_ids = games.loc[(games["status"] == "final"), "game_id"]
    lst = logs[logs["game_id"].isin(final_ids)]
    key = pd.MultiIndex.from_frame(roster[["player_id", "game_id"]])
    unforeseen = lst[~pd.MultiIndex.from_frame(lst[["player_id", "game_id"]]).isin(key)]

    def describe(d):
        return {
            "rows": int(len(d)), "play_rate": float(d["y_play"].mean()), "rot10_rate": float(d["y_rot10"].mean()), "rot5_rate": float(d["y_rot5"].mean()),
            "listed_share": float(d["listed"].mean()), "candidates_per_team_game": float(d.groupby(["game_id", "team_id"]).size().mean()),
            "no_history_rows": int((d["no_history"] == 1).sum()), "rot_label_missing": int(d["y_rot10"].isna().sum()),
            "per_season": {int(s): {"rows": int(len(g)), "play_rate": float(g["y_play"].mean()), "rot10_rate": float(g["y_rot10"].mean())} for s, g in d.groupby("season_start_year")},
        }

    report: dict = {
        "model_version": MODEL_VERSION, "run_started": started.isoformat(), "code_version": code_version(), "dataset_cutoff": str(dataset_cutoff),
        "features": PARTICIPATION_FEATURES,
        "datasets": {"roster": describe(roster), "listed": describe(listed)},
        "unforeseen_listings": {"rows": int(len(unforeseen)), "share_of_listings": float(len(unforeseen) / len(lst)), "played_share": float((~unforeseen["did_not_play"]).mean()),
                                "share_of_all_minutes": float(unforeseen["minutes"].sum() / lst["minutes"].sum())},
    }
    log(f"roster universe {len(roster)} rows (play {report['datasets']['roster']['play_rate']:.3f}); listed {len(listed)}; unforeseen listings {len(unforeseen)}")

    if check_rows:
        rng = np.random.default_rng(5)
        pick = np.concatenate([
            rng.choice(roster.index.to_numpy(), 15, replace=False),
            rng.choice(roster.index[~roster["listed"].astype(bool)].to_numpy(), 8, replace=False),
            rng.choice(roster.index[roster["no_history"] == 1].to_numpy(), 6, replace=False),
            rng.choice(roster.index[roster["traded"] == 1].to_numpy(), 6, replace=False),
            rng.choice(roster.index[roster["team_games_unlisted"] >= 5].to_numpy(), 5, replace=False),
        ])
        sample = roster.loc[pick, ["player_id", "game_id", "team_id"]].reset_index(drop=True)
        v1_viol = lookahead_violations(games, logs, sample)
        extra_viol, roster_viol = [], []
        full = add_participation_features(build_features(games, logs, sample), games, logs)
        for i, row in full.iterrows():
            g_t, l_t = truncate_to_cutoff(games, logs, row["t_cut"])
            one = add_participation_features(build_features(g_t, l_t, sample.iloc[[i]]), g_t, l_t).iloc[0]
            for c in EXTRA_FEATURES:
                a, b = row[c], one[c]
                if not (pd.isna(a) and pd.isna(b)) and (pd.isna(a) != pd.isna(b) or abs(float(a) - float(b)) > 1e-9):
                    extra_viol.append({"row": int(i), "feature": c, "full": a, "truncated": b})
            # roster membership must also be decidable from the truncated world
            season = int(games.loc[games["game_id"] == row["game_id"], "season_start_year"].iloc[0])
            g_t2 = g_t.copy()
            g_t2.loc[g_t2["game_id"] == row["game_id"], "status"] = "final"  # the target game itself must count as a team-game
            r_t = reconstructed_roster(g_t2, l_t, first_season=season, last_season=season)
            if not ((r_t["player_id"] == row["player_id"]) & (r_t["game_id"] == row["game_id"]) & (r_t["team_id"] == row["team_id"])).any():
                roster_viol.append({"row": int(i), "player_id": int(row["player_id"]), "game_id": int(row["game_id"])})
        report["lookahead_check"] = {"rows_checked": int(len(sample)), "v1_feature_violations": v1_viol, "participation_feature_violations": extra_viol, "roster_membership_violations": roster_viol}
        log(f"lookahead: {len(sample)} rows; violations v1={len(v1_viol)} participation={len(extra_viol)} roster={len(roster_viol)}")
        if v1_viol or extra_viol or roster_viol:
            raise AssertionError("lookahead violations found")

    # Classifiers.
    roster_play, report["participation_roster"] = _classification_block(roster, "y_play", log)
    roster_rot, report["rotation10_roster"] = _classification_block(roster, "y_rot10", log)
    _, report["rotation5_roster"] = _classification_block(roster, "y_rot5", log)
    listed_play, report["participation_listed_scenario"] = _classification_block(listed, "y_play", log)
    p_model, r_model = report["participation_roster"]["chosen"], report["rotation10_roster"]["chosen"]

    # V1 conditional minutes for both universes.
    roster_frame = roster[roster["season_start_year"].isin(ALL_FOLDS)].copy()
    roster_frame["m_v1"], wf = _v1_predictions(listed, roster_frame, log)
    listed_frame = listed[listed["season_start_year"].isin(ALL_FOLDS)].copy()
    listed_frame["m_v1"] = wf["hgb"].reindex(listed_frame.index)
    for frame in (roster_frame, listed_frame):
        for s in ALL_FOLDS:
            prior = listed[(listed["season_start_year"] < s) & (listed["no_history"] == 1) & listed["played"]]
            m = (frame["season_start_year"] == s) & frame["m_v1"].isna()
            frame.loc[m, "m_v1"] = float(prior["minutes"].median())
    roster_frame["p_play"] = roster_play[p_model].reindex(roster_frame.index)
    roster_frame["p_rot10"] = roster_rot[r_model].reindex(roster_frame.index)
    listed_frame["p_play"] = listed_play[report["participation_listed_scenario"]["chosen"]].reindex(listed_frame.index)

    # Overtime and budgets.
    ot = overtime_periods(listed[listed["played"]])
    all_games = listed.groupby("game_id").agg(season_start_year=("season_start_year", "first"), abs_net_diff=("abs_net_diff", "first")).join(ot.rename("ot_periods"), how="inner")
    ot_report = {"overall_rate": float((all_games["ot_periods"] > 0).mean()), "mean_periods_if_ot": float(all_games.loc[all_games["ot_periods"] > 0, "ot_periods"].mean()), "per_fold": {}}
    budgets = {"strict_240": pd.Series(240.0, index=all_games.index), "ot_constant": pd.Series(np.nan, index=all_games.index), "ot_matchup": pd.Series(np.nan, index=all_games.index)}
    for s in ALL_FOLDS:
        tr, te = all_games[all_games["season_start_year"] < s], all_games[all_games["season_start_year"] == s]
        fold = {}
        for name, use in (("ot_constant", False), ("ot_matchup", True)):
            m = OvertimeModel(use).fit(tr)
            budgets[name].loc[te.index] = m.budget(te)
            fold[name] = {"brier": float(np.mean((m.p_overtime(te) - (te["ot_periods"] > 0)) ** 2)), "mean_p": float(m.p_overtime(te).mean()), "observed": float((te["ot_periods"] > 0).mean())}
        ot_report["per_fold"][int(s)] = fold
    for frame in (roster_frame, listed_frame):
        for k, b in budgets.items():
            frame[f"budget_{k}"] = frame["game_id"].map(b).fillna(240.0)
    test_games = all_games[all_games["season_start_year"].isin(mev.TEST_FOLDS)]
    ot_report["test_ot_rate"] = float((test_games["ot_periods"] > 0).mean())
    ot_report["test_mean_team_minutes_implied"] = float(240 + 25 * test_games["ot_periods"].mean())
    report["overtime"] = ot_report

    sig = _sigma_tables(wf)
    recon_roster, report["reconciliation_roster"] = _reconciliation(roster_frame, sig, log, "roster")
    _, report["reconciliation_listed_scenario"] = _reconciliation(listed_frame, sig, log, "listed scenario")
    adopted = report["reconciliation_roster"]["adopted"]
    best = report["reconciliation_roster"]["best_on_selection"]

    # Unforeseen played listings: raw V1 only (they were never candidates).
    uf_rows = listed_frame.merge(unforeseen[["player_id", "game_id"]], on=["player_id", "game_id"])
    uf_rows = uf_rows[uf_rows["played"] & uf_rows["season_start_year"].isin(RECON_TEST)]
    report["unforeseen_listings"]["test_played_rows"] = int(len(uf_rows))
    report["unforeseen_listings"]["test_raw_v1"] = point_metrics(uf_rows["m_v1"], uf_rows["minutes"]) if len(uf_rows) else None

    # Rotation filter, bands, likely rotation - roster universe, test folds.
    test_listed = recon_roster[recon_roster["season_start_year"].isin(RECON_TEST)].copy()
    test_played = test_listed[test_listed["played"]].copy()
    filt = {}
    for tau in (0.5, 0.7, 0.9):
        inside = test_listed["p_rot10"] >= tau
        actual = test_listed["y_rot10"] == 1.0
        tp_in = test_played["p_rot10"] >= tau
        filt[str(tau)] = {
            "candidates_per_team_game_inside": float(inside.groupby([test_listed["game_id"], test_listed["team_id"]]).sum().mean()),
            "precision_actual_10plus": float(actual[inside].mean()), "recall_actual_10plus": float(inside[actual].mean()),
            "v1_inside": point_metrics(test_played.loc[tp_in, "m_v1"], test_played.loc[tp_in, "minutes"]),
            "v1_outside": point_metrics(test_played.loc[~tp_in, "m_v1"], test_played.loc[~tp_in, "minutes"]) if (~tp_in).any() else None,
        }
    report["rotation_filter_test"] = filt
    test_played["band"] = pd.cut(test_played["m_v1"], BETTABLE_EDGES, labels=BETTABLE_LABELS)
    report["bands_test"] = {
        str(b): {"raw_v1": point_metrics(g["m_v1"], g["minutes"]), "reconciled": point_metrics(g["m_recon"], g["minutes"]),
                 "ewma_baseline": point_metrics(g["min_ewm"].fillna(g["m_v1"]), g["minutes"]),
                 "mean_p_play": float(g["p_play"].mean()), "mean_p_rot10": float(g["p_rot10"].mean())}
        for b, g in test_played.groupby("band", observed=True)
    }
    for tau in (0.8,):
        likely = test_played[test_played["p_rot10"] >= tau]
        report["likely_rotation_test"] = {"rule": f"p_rot10 >= {tau}", "n": int(len(likely)), "raw_v1": point_metrics(likely["m_v1"], likely["minutes"]), "reconciled": point_metrics(likely["m_recon"], likely["minutes"])}
    seg = mev.add_segments(test_played)
    report["reconciliation_segments_test"] = {s: {k: {"raw_v1": point_metrics(g["m_v1"], g["minutes"]), "reconciled": point_metrics(g["m_recon"], g["minutes"])} for k, g in seg.groupby(s, observed=True)} for s in ("seg_role", "seg_stability", "seg_team_change", "seg_absence")}
    pp = test_listed
    report["failure_cases_test"] = {
        "p_play_ge_0.9_but_did_not_play": int(((pp["p_play"] >= 0.9) & (pp["y_play"] == 0)).sum()),
        "of_which_not_dressed": int(((pp["p_play"] >= 0.9) & (pp["y_play"] == 0) & ~pp["listed"].astype(bool)).sum()),
        "p_play_le_0.2_but_played": int(((pp["p_play"] <= 0.2) & (pp["y_play"] == 1)).sum()),
        "p_rot10_ge_0.9_but_under_10": int(((pp["p_rot10"] >= 0.9) & (pp["y_rot10"] == 0)).sum()),
        "candidates": int(len(pp)),
    }

    # Serving: roster-universe classifiers (all modelled seasons; tuned on the last season) + reconciliation config.
    serving = {}
    for key, target, name in (("participation", "y_play", p_model), ("rotation_10", "y_rot10", r_model)):
        rows = roster[roster[target].notna()]
        params, _ = rev.tune_clf(name, rows, list(range(DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON)), DEFAULT_LAST_SEASON, PARTICIPATION_FEATURES, target)
        model = DropEmptyColumnsClassifier(CLASSIFIERS[name].build(params)).fit(rows[PARTICIPATION_FEATURES].to_numpy(dtype=float), rows[target].to_numpy(dtype=int))
        path, sha = save_model(model, f"{MODEL_VERSION}-{key}-{name}-{stamp}")
        block = report["participation_roster" if key == "participation" else "rotation10_roster"]
        run = record_run(
            db, run_key=f"{MODEL_VERSION}:{key}:{name}:serving:{stamp}", model_name=f"{key}_{name}", model_version=MODEL_VERSION, purpose="serving",
            dataset_cutoff=dataset_cutoff.to_pydatetime().replace(tzinfo=timezone.utc), training_seasons=list(range(DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON + 1)),
            evaluation_seasons=list(ALL_FOLDS), features=PARTICIPATION_FEATURES, hyperparameters={"target": target, "universe": UNIVERSE_ROSTER, **params},
            metrics={"pooled_test": block["pooled_test"][name], "pooled_selection": block["pooled_selection"][name]},
            artifact_path=path, artifact_sha256=sha, notes=f"{key} classifier ({target}) on the reconstructed-roster universe; historically reconstructable features only",
        )
        serving[key] = {"model": name, "params": params, "model_run_id": run.id, "artifact_sha256": sha}
    played_wf = wf[wf["played"]]
    r_all = played_wf["minutes"] - played_wf["hgb"]
    bands_all = pred_band(played_wf["hgb"]).to_numpy()
    full_sigma = {b: float(r_all[bands_all == b].std(ddof=1)) for b in BAND_LABELS}
    ot_all = OvertimeModel(best["budget"] == "ot_matchup").fit(all_games) if best["budget"] != "strict_240" else None
    recon_run = record_run(
        db, run_key=f"{MODEL_VERSION}:reconciliation:serving:{stamp}", model_name="reconciliation", model_version=MODEL_VERSION, purpose="serving",
        dataset_cutoff=dataset_cutoff.to_pydatetime().replace(tzinfo=timezone.utc), training_seasons=list(range(DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON + 1)),
        evaluation_seasons=list(RECON_SEL + RECON_TEST), features=[],
        hyperparameters={"method": best["method"], "budget": best["budget"], "alpha": best["alpha"], "adopted": bool(adopted), "universe": UNIVERSE_ROSTER,
                         "sigma_by_band": full_sigma, "overtime": None if ot_all is None else {"rate": ot_all.rate_, "periods_if_ot": ot_all.periods_if_ot_}},
        metrics={"selection": best["selection"], "test": best["test"], "raw_selection": report["reconciliation_roster"]["raw"]["selection"], "raw_test": report["reconciliation_roster"]["raw"]["test"]},
        notes="Team-minutes reconciliation of V1 conditional minutes over the reconstructed roster. Applied to predictions only if adopted=true.",
    )
    serving["reconciliation"] = {"model_run_id": recon_run.id, "adopted": bool(adopted)}
    db.commit()
    report["serving"] = serving
    report["finished"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"report-{stamp}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    log(f"report: {path}")
    report["report_path"] = str(path)
    return report
