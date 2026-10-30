"""Minutes V2, step 1: the offline upper bound (docs/NBA_MINUTES_V2_PLAN.md).

If the active list (who dressed for game Y) were known perfectly, how much
would participation and minutes accuracy improve? Scenarios, scored on the
same roster-universe rows (V1.5's primary universe):

- A   V1.5 as served: roster-universe P(play); V1 conditional minutes (trained
      on listed rows, exactly as V1 was built).
- A'  Control: the same minutes model retrained on roster-universe played rows
      with V1's features only. Separates the population change from C's
      feature gain.
- B   Active list known: P(play) = 0 if he did not dress, otherwise the
      listed-scenario P(play | dressed). Minutes as A.
- C   B + a minutes model retrained with TEAMMATE_FEATURES, computed from who
      on the roster did and did not dress for game Y.
- D   C + variance-weighted reconciliation to 240 over the dressed set
      (alpha chosen on selection folds only).

The oracle (game Y's box-score listing) is used deliberately and labelled -
it is the upper bound. Every other input is V1's as-of feature set, read
4 h before tip-off. Protocol as V1: season walk-forward, nested tuning,
selection folds 2020-21..2023-24, test folds 2024-25 and 2025-26.

Decision rule (fixed in the plan before this was run): build V2's minutes
part only if C or D beats A on test-season conditional MAE by >= 0.10 minutes
with a 95% paired-bootstrap CI (clustered by game date, 2,000 resamples)
excluding 0, and the selection folds agree in sign.

Offline only: no registry rows, no saved models; one JSON report in
docs/nba_minutes_v2/.

    python -m app.nba.minutes_v2.upper_bound
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.nba.minutes import evaluation as mev
from app.nba.minutes.data import load_games, load_logs
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON, history_logs
from app.nba.minutes.features import ALL_FEATURES
from app.nba.minutes.metrics import point_metrics
from app.nba.minutes.registry import code_version
from app.nba.minutes.residuals import BAND_LABELS, pred_band
from app.nba.rotation import evaluation as rev
from app.nba.rotation.allocation import band_sigma, reconcile_frame
from app.nba.rotation.dataset import UNIVERSE_LISTED, UNIVERSE_ROSTER, build_rotation_dataset
from app.nba.rotation.experiment import ALPHAS, BETTABLE_EDGES, BETTABLE_LABELS, RECON_SEL, _v1_predictions
from app.nba.rotation.metrics import prob_metrics

REPORT_DIR = Path(__file__).resolve().parents[4] / "docs" / "nba_minutes_v2"
ALL_FOLDS = mev.SELECTION_FOLDS + mev.TEST_FOLDS
REGULAR_STARTER_RATE = 0.6  # start_rate5 at or above this: a regular starter
ABSENT_SEGMENT_MINUTES = 15.0
DECISION_MARGIN = 0.10
BOOTSTRAP_RESAMPLES = 2000

TEAMMATE_FEATURES = [
    "dressed_others",  # roster teammates who dressed
    "absent_new_min",  # recent (EWMA) minutes of teammates who dressed for the team's last game but not this one
    "absent_new_n",
    "absent_new_starters",  # ... of whom regular starters
    "absent_recent_min",  # EWMA minutes of non-dressed teammates last listed within the team's last 5 games
    "returning_min",  # EWMA minutes of dressed teammates who missed the team's last game
    "ewm_rank_dressed",  # his EWMA-minutes rank among the dressed teammates and himself (1 = most)
]


def teammate_absence_features(frame: pd.DataFrame) -> pd.DataFrame:
    """TEAMMATE_FEATURES for each row of a roster-universe frame (one row per
    candidate per team-game). Uses the candidates' `listed` (dressed) flag for
    game Y - the oracle - and their as-of history features. Every sum excludes
    the row's own player."""
    key = [frame["game_id"].to_numpy(), frame["team_id"].to_numpy()]
    listed = frame["listed"].astype(bool).to_numpy()
    ewm = frame["min_ewm"].fillna(0.0).to_numpy(dtype=float)
    unlisted = frame["team_games_unlisted"].fillna(20.0).to_numpy(dtype=float)
    starter = frame["start_rate5"].fillna(0.0).to_numpy(dtype=float) >= REGULAR_STARTER_RATE

    def others(values) -> np.ndarray:
        s = pd.Series(np.asarray(values, dtype=float), index=frame.index)
        return (s.groupby(key).transform("sum") - s).to_numpy()

    new_absent = ~listed & (unlisted == 0)
    recent_absent = ~listed & (unlisted <= 4)
    returning = listed & (unlisted >= 1)
    out = pd.DataFrame(index=frame.index)
    out["dressed_others"] = others(listed)
    out["absent_new_min"] = others(ewm * new_absent)
    out["absent_new_n"] = others(new_absent)
    out["absent_new_starters"] = others(new_absent & starter)
    out["absent_recent_min"] = others(ewm * recent_absent)
    out["returning_min"] = others(ewm * returning)
    # 1 + dressed teammates with more EWMA minutes than him.
    rank = np.ones(len(frame))
    pos = pd.Series(np.arange(len(frame)), index=frame.index)
    for _, idx in pos.groupby(key):
        i = idx.to_numpy()
        dressed_ewm = np.sort(ewm[i][listed[i]])
        greater = len(dressed_ewm) - np.searchsorted(dressed_ewm, ewm[i], side="right")
        rank[i] = 1 + greater
    out["ewm_rank_dressed"] = rank
    return out


def paired_bootstrap(err_a, err_b, clusters, *, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = 7) -> dict:
    """Mean of (|err_a| - |err_b|) - positive means B is better - with a 95% CI
    from resampling whole clusters (game dates)."""
    diff = np.abs(np.asarray(err_a, dtype=float)) - np.abs(np.asarray(err_b, dtype=float))
    codes, _ = pd.factorize(pd.Series(clusters))
    sums = np.bincount(codes, weights=diff)
    counts = np.bincount(codes).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(sums), size=(resamples, len(sums)))
    boot = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return {"mean_improvement": float(diff.mean()), "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))], "clusters": int(len(sums)), "n": int(len(diff))}


def _minutes_walk_forward(data: pd.DataFrame, features: list[str], log) -> pd.Series:
    wf, _ = mev.walk_forward(data, ["hgb"], features=features, log=log)
    return wf["hgb"].reindex(data.index)


def _sigma_by_fold(frame: pd.DataFrame, col: str, folds) -> dict:
    """Residual SD by predicted-minutes band from EARLIER folds' out-of-sample predictions."""
    played = frame[frame["played"] & frame[col].notna()]
    tables = {}
    for s in folds:
        prior = played[played["season_start_year"] < s]
        r = prior["minutes"] - prior[col]
        bands = pred_band(prior[col]).to_numpy()
        tables[s] = {b: float(r[bands == b].std(ddof=1)) for b in BAND_LABELS}
    return tables


def run(db, *, log=print) -> dict:
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    games = load_games(db)
    logs = history_logs(load_logs(db, min_season=DEFAULT_FIRST_SEASON), games, DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON)
    roster = build_rotation_dataset(games, logs, universe=UNIVERSE_ROSTER)
    listed = build_rotation_dataset(games, logs, universe=UNIVERSE_LISTED)
    log(f"roster {len(roster)} rows, listed {len(listed)} rows")
    roster = pd.concat([roster, teammate_absence_features(roster)], axis=1)

    # P(play): A from the roster universe, B = 0 if not dressed else P(play | dressed).
    roster_play, _ = rev.walk_forward_clf(roster, "y_play", ["hgb"], log=log)
    listed_play, _ = rev.walk_forward_clf(listed, "y_play", ["hgb"], log=log)
    frame = roster[roster["season_start_year"].isin(ALL_FOLDS)].copy()
    frame["p_a"] = roster_play["hgb"].reindex(frame.index)
    dressed_p = listed_play.set_index(["player_id", "game_id"])["hgb"]
    lookup = dressed_p.reindex(pd.MultiIndex.from_frame(frame[["player_id", "game_id"]])).to_numpy()
    is_listed = frame["listed"].astype(bool).to_numpy()
    missing_dressed = int((is_listed & np.isnan(lookup)).sum())
    frame["p_b"] = np.where(is_listed, lookup, 0.0)
    frame.loc[is_listed & np.isnan(lookup), "p_b"] = frame.loc[is_listed & np.isnan(lookup), "p_a"]

    # Minutes: A (V1 exactly), A' (control), C (teammate features).
    frame["m_a"], _ = _v1_predictions(listed, frame, log)
    frame["m_a2"] = _minutes_walk_forward(roster, ALL_FEATURES, log).reindex(frame.index)
    frame["m_c"] = _minutes_walk_forward(roster, ALL_FEATURES + TEAMMATE_FEATURES, log).reindex(frame.index)
    for s in ALL_FOLDS:  # no-history rows: V1's documented constant fallback
        prior = listed[(listed["season_start_year"] < s) & (listed["no_history"] == 1) & listed["played"]]
        m = frame["season_start_year"] == s
        for col in ("m_a", "m_a2", "m_c"):
            frame.loc[m & frame[col].isna(), col] = float(prior["minutes"].median())

    # D: reconcile C over the dressed set (p_b), alpha chosen on selection folds.
    recon_folds = RECON_SEL + mev.TEST_FOLDS
    sig = _sigma_by_fold(frame, "m_c", recon_folds)
    recon = frame[frame["season_start_year"].isin(recon_folds)].copy()
    recon["sigma"] = np.nan
    for s in recon_folds:
        m = recon["season_start_year"] == s
        recon.loc[m, "sigma"] = band_sigma(recon.loc[m, "m_c"], sig[s])
    recon["budget"] = 240.0
    played_r = recon["played"]
    sel_r = played_r & recon["season_start_year"].isin(RECON_SEL)
    alpha_grid = []
    for alpha in ALPHAS:
        adj = reconcile_frame(recon, m_col="m_c", p_col="p_b", sigma_col="sigma", budget_col="budget", alpha=alpha)
        recon[f"m_d_{alpha}"] = adj
        alpha_grid.append({"alpha": alpha, "selection": point_metrics(adj[sel_r], recon.loc[sel_r, "minutes"])})
    best_alpha = min(alpha_grid, key=lambda g: g["selection"]["mae"])["alpha"]
    frame["m_d"] = recon[f"m_d_{best_alpha}"].reindex(frame.index)
    log(f"reconciliation alpha chosen on selection folds: {best_alpha}")

    # --- scoring --------------------------------------------------------------
    frame["cluster"] = pd.to_datetime(frame["scheduled_start"], utc=True).dt.strftime("%Y-%m-%d")
    played = frame[frame["played"]]
    test_p = played[played["season_start_year"].isin(mev.TEST_FOLDS)]
    sel_p = played[played["season_start_year"].isin(mev.SELECTION_FOLDS)]
    sel_p_recon = played[played["season_start_year"].isin(RECON_SEL)]

    def minutes_block(rows):
        out = {k: point_metrics(rows[c], rows["minutes"]) for k, c in (("A_v1", "m_a"), ("A2_control", "m_a2"), ("C_teammates", "m_c"))}
        if rows["m_d"].notna().all():
            out["D_reconciled"] = point_metrics(rows["m_d"], rows["minutes"])
        return out

    report: dict = {
        "experiment": "minutes-v2 step 1: offline upper bound (active list known)",
        "plan": "docs/NBA_MINUTES_V2_PLAN.md",
        "run_started": started.isoformat(),
        "code_version": code_version(),
        "teammate_features": TEAMMATE_FEATURES,
        "rows": {"roster_frame": int(len(frame)), "dressed_rows_without_listed_prediction": missing_dressed},
        "reconciliation": {"alpha_grid_selection": alpha_grid, "chosen_alpha": best_alpha, "folds": list(recon_folds)},
        # D needs residual spreads from earlier folds, so it exists from 2021-22 only.
        "minutes_conditional": {"test": minutes_block(test_p), "selection": minutes_block(sel_p), "selection_2021_2023": minutes_block(sel_p_recon)},
    }

    # Paired bootstrap vs A, test and selection.
    comparisons = {}
    for name, col in (("C_vs_A", "m_c"), ("D_vs_A", "m_d"), ("C_vs_A2_feature_effect", "m_c"), ("A2_vs_A_population_effect", "m_a2")):
        base = "m_a2" if name.endswith("feature_effect") else "m_a"
        block = {}
        for label, rows in (("test", test_p), ("selection", sel_p_recon if col == "m_d" else sel_p)):
            r = rows[rows[col].notna()]
            block[label] = paired_bootstrap(r[base] - r["minutes"], r[col] - r["minutes"], r["cluster"])
        comparisons[name] = block
    report["minutes_comparisons"] = comparisons

    def passes(name):
        c = comparisons[name]
        return c["test"]["mean_improvement"] >= DECISION_MARGIN and c["test"]["ci95"][0] > 0 and c["selection"]["mean_improvement"] > 0

    report["decision"] = {
        "rule": f"C or D beats A on test conditional MAE by >= {DECISION_MARGIN} min, 95% CI excludes 0, selection agrees in sign",
        "C_passes": bool(passes("C_vs_A")), "D_passes": bool(passes("D_vs_A")),
        "build_v2_minutes": bool(passes("C_vs_A") or passes("D_vs_A")),
    }

    # Segments (test): predicted-minutes band from A, and rows with absent teammates.
    seg = test_p.copy()
    seg["band"] = pd.cut(seg["m_a"], BETTABLE_EDGES, labels=BETTABLE_LABELS)
    report["segments_test"] = {
        "band": {str(b): minutes_block(g) for b, g in seg.groupby("band", observed=True)},
        "absent_teammates": {k: minutes_block(g) for k, g in seg.groupby(np.where(seg["absent_recent_min"] >= ABSENT_SEGMENT_MINUTES, f">= {ABSENT_SEGMENT_MINUTES:g} absent min", "fewer"))},
    }

    # Participation and unconditional expected minutes, all roster candidates.
    cand = frame[frame["season_start_year"].isin(mev.TEST_FOLDS)]
    report["participation_test"] = {"A_v15": prob_metrics(cand["p_a"], cand["y_play"]), "B_active_list_known": prob_metrics(cand["p_b"], cand["y_play"])}
    unc = cand[cand["minutes_or_zero"].notna()]
    report["unconditional_test"] = {
        k: point_metrics(unc[p] * unc[m], unc["minutes_or_zero"])
        for k, p, m in (("A", "p_a", "m_a"), ("B", "p_b", "m_a"), ("C", "p_b", "m_c"), ("D", "p_b", "m_d"))
        if unc[m].notna().all()
    }

    report["finished"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"upper-bound-{stamp}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    log(f"report: {path}")
    log(json.dumps({"decision": report["decision"], "test": report["minutes_conditional"]["test"], "comparisons": comparisons, "participation": report["participation_test"]}, indent=1, default=str))
    report["report_path"] = str(path)
    return report


def main() -> int:
    from app.database import SessionLocal

    with SessionLocal() as db:
        run(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
