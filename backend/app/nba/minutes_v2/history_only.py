"""Minutes V2, step 1b: the history-only V2 (docs/NBA_MINUTES_V2_PLAN.md,
amendment 1, whose rule was fixed and pushed before this was run).

No oracle and no evidence: each teammate's probability of dressing q_j is
P(dress | history), a roster-universe classifier (target = listed), walk-
forward out of sample. So everything here is reconstructable before tip-off
and is a floor for what injury evidence can add on top. Scored on the same
roster-universe rows and folds as step 1:

- A        V1.5: direct P(play); V1 minutes.
- C        step 1's oracle model (reference only: served with who dressed).
- E-hist   the C model, served with EXPECTED teammate features under q.
- F-hist   the same model form trained on the expected features (train =
           serve). Training needs out-of-sample q, which exists from 2020-21,
           so F-hist is fitted from 2020-21 and evaluated from 2021-22, with
           C's tuned hyperparameters per fold (no separate tuning).
- P(play) factorised = q x P(play | dressed), against V1.5's direct P(play).

Offline only: no registry rows, no saved models; one JSON report in
docs/nba_minutes_v2/.

    python -m app.nba.minutes_v2.history_only
"""

import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.nba.minutes import evaluation as mev
from app.nba.minutes.data import load_games, load_logs
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON, history_logs
from app.nba.minutes.features import ALL_FEATURES
from app.nba.minutes.metrics import point_metrics
from app.nba.minutes.models import CANDIDATES as MINUTES_CANDIDATES
from app.nba.minutes.models import fit_predict as minutes_fit_predict
from app.nba.minutes.registry import code_version
from app.nba.minutes_v2.features import TEAMMATE_FEATURES, expected_teammate_features, oracle_teammate_features
from app.nba.minutes_v2.upper_bound import REPORT_DIR, paired_bootstrap
from app.nba.rotation import evaluation as rev
from app.nba.rotation.dataset import UNIVERSE_LISTED, UNIVERSE_ROSTER, build_rotation_dataset
from app.nba.rotation.experiment import BETTABLE_EDGES, BETTABLE_LABELS, _v1_predictions
from app.nba.rotation.features import PARTICIPATION_FEATURES
from app.nba.rotation.metrics import prob_metrics
from app.nba.rotation.models import CLASSIFIERS, fit_predict_proba

ALL_FOLDS = mev.SELECTION_FOLDS + mev.TEST_FOLDS
F_FIRST_TRAIN_SEASON = 2020  # first season with out-of-sample q
F_FOLDS = tuple(s for s in ALL_FOLDS if s > F_FIRST_TRAIN_SEASON)
LOGLOSS_TIE = 0.002


def _p_play_given_dressed(listed: pd.DataFrame, roster: pd.DataFrame, log) -> pd.Series:
    """P(play | dressed) for EVERY roster candidate (dressed or not): the
    listed-scenario classifier, walk-forward with its tuned params."""
    _, meta = rev.walk_forward_clf(listed, "y_play", ["hgb"], log=log)
    out = pd.Series(np.nan, index=roster.index)
    for s in ALL_FOLDS:
        test = roster[roster["season_start_year"] == s]
        train = listed[listed["season_start_year"] < s]
        _, p = fit_predict_proba(CLASSIFIERS["hgb"], meta[s]["models"]["hgb"]["params"], train, test, PARTICIPATION_FEATURES, "y_play")
        out.loc[test.index] = p
    return out


def run(db, *, log=print) -> dict:
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    games = load_games(db)
    logs = history_logs(load_logs(db, min_season=DEFAULT_FIRST_SEASON), games, DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON)
    roster = build_rotation_dataset(games, logs, universe=UNIVERSE_ROSTER)
    listed = build_rotation_dataset(games, logs, universe=UNIVERSE_LISTED)
    roster["y_dress"] = roster["listed"].astype(bool).astype(float)
    log(f"roster {len(roster)} rows, listed {len(listed)} rows")

    # Participation.
    dress_wf, _ = rev.walk_forward_clf(roster, "y_dress", ["hgb"], log=log)
    play_wf, _ = rev.walk_forward_clf(roster, "y_play", ["hgb"], log=log)
    roster["q"] = dress_wf["hgb"].reindex(roster.index)
    roster["p_a"] = play_wf["hgb"].reindex(roster.index)
    roster["p_pd"] = _p_play_given_dressed(listed, roster, log)
    roster["p_fact"] = roster["q"] * roster["p_pd"]

    # Teammate features: oracle (C) and expected under q (E / F), fold seasons.
    oracle = pd.concat([roster, oracle_teammate_features(roster)], axis=1)
    frame = oracle[oracle["season_start_year"].isin(ALL_FOLDS)].copy()
    expected = frame.drop(columns=TEAMMATE_FEATURES)
    expected = pd.concat([expected, expected_teammate_features(expected, expected["q"])], axis=1)
    features = ALL_FEATURES + TEAMMATE_FEATURES

    frame["m_a"], _ = _v1_predictions(listed, frame, log)
    wf_c, meta_c = mev.walk_forward(oracle, ["hgb"], features=features, log=log)
    frame["m_c"] = wf_c["hgb"].reindex(frame.index)
    oracle_rows = mev.model_rows(oracle)
    expected_rows = mev.model_rows(expected)
    frame["m_e"] = np.nan
    frame["m_f"] = np.nan
    for s in ALL_FOLDS:
        params = meta_c[s]["models"]["hgb"]["params"]
        test = expected[(expected["season_start_year"] == s) & expected["eligible"].astype(bool)]
        _, pred = minutes_fit_predict(MINUTES_CANDIDATES["hgb"], params, oracle_rows[oracle_rows["season_start_year"] < s], test, features)
        frame.loc[test.index, "m_e"] = pred
        if s in F_FOLDS:
            train = expected_rows[(expected_rows["season_start_year"] >= F_FIRST_TRAIN_SEASON) & (expected_rows["season_start_year"] < s)]
            _, pred = minutes_fit_predict(MINUTES_CANDIDATES["hgb"], params, train, test, features)
            frame.loc[test.index, "m_f"] = pred
        log(f"  fold {s}: E-hist{' and F-hist' if s in F_FOLDS else ''} done")
    for s in ALL_FOLDS:  # no-history rows: V1's documented constant fallback
        prior = listed[(listed["season_start_year"] < s) & (listed["no_history"] == 1) & listed["played"]]
        m = frame["season_start_year"] == s
        for col in ("m_a", "m_c", "m_e", "m_f"):
            if col == "m_f" and s not in F_FOLDS:
                continue
            frame.loc[m & frame[col].isna(), col] = float(prior["minutes"].median())

    # --- scoring --------------------------------------------------------------
    frame["cluster"] = pd.to_datetime(frame["scheduled_start"], utc=True).dt.strftime("%Y-%m-%d")
    played = frame[frame["played"]]
    test_p = played[played["season_start_year"].isin(mev.TEST_FOLDS)]
    sel_p = played[played["season_start_year"].isin(mev.SELECTION_FOLDS)]
    sel_f = played[played["season_start_year"].isin([s for s in mev.SELECTION_FOLDS if s in F_FOLDS])]
    cols = (("A_v1", "m_a"), ("C_oracle_reference", "m_c"), ("E_hist", "m_e"), ("F_hist", "m_f"))

    def minutes_block(rows):
        return {k: point_metrics(rows[c], rows["minutes"]) for k, c in cols if rows[c].notna().all()}

    comparisons = {}
    for name, col, sel in (("E_hist_vs_A", "m_e", sel_p), ("F_hist_vs_A", "m_f", sel_f), ("C_oracle_vs_A", "m_c", sel_p)):
        comparisons[name] = {label: paired_bootstrap(r["m_a"] - r["minutes"], r[col] - r["minutes"], r["cluster"]) for label, r in (("test", test_p), ("selection", sel))}

    def minutes_pass(name):
        c = comparisons[name]
        return c["test"]["ci95"][0] > 0 and c["selection"]["mean_improvement"] > 0

    cand_test = frame[frame["season_start_year"].isin(mev.TEST_FOLDS)]
    cand_sel = frame[frame["season_start_year"].isin(mev.SELECTION_FOLDS)]
    participation = {
        label: {"A_v15_direct": prob_metrics(c["p_a"], c["y_play"]), "factorised_q_x_p_play_given_dressed": prob_metrics(c["p_fact"], c["y_play"]),
                "p_dress_history": prob_metrics(c["q"], c["y_dress"])}
        for label, c in (("test", cand_test), ("selection", cand_sel))
    }
    fact_ok = participation["test"]["factorised_q_x_p_play_given_dressed"]["log_loss"] <= participation["test"]["A_v15_direct"]["log_loss"] + LOGLOSS_TIE

    unc = cand_test[cand_test["minutes_or_zero"].notna()]
    unconditional = {k: point_metrics(unc[p] * unc[m], unc["minutes_or_zero"]) for k, p, m in (("A", "p_a", "m_a"), ("factorised_x_E", "p_fact", "m_e"), ("factorised_x_F", "p_fact", "m_f"))}

    seg = test_p.copy()
    seg["band"] = pd.cut(seg["m_a"], BETTABLE_EDGES, labels=BETTABLE_LABELS)
    report = {
        "experiment": "minutes-v2 step 1b: history-only V2 (no oracle, no evidence)",
        "plan": "docs/NBA_MINUTES_V2_PLAN.md, amendment 1",
        "run_started": started.isoformat(),
        "code_version": code_version(),
        "teammate_features": TEAMMATE_FEATURES,
        "f_hist": {"first_train_season": F_FIRST_TRAIN_SEASON, "folds": list(F_FOLDS), "hyperparameters": "C's tuned params per fold"},
        "minutes_conditional": {"test": minutes_block(test_p), "selection": minutes_block(sel_p), "selection_f_folds": minutes_block(sel_f)},
        "minutes_comparisons": comparisons,
        "participation": participation,
        "unconditional_test": unconditional,
        "bands_test": {str(b): minutes_block(g) for b, g in seg.groupby("band", observed=True)},
        "decision": {
            "minutes_rule": "E-hist or F-hist beats A on test conditional MAE, 95% CI excluding 0, selection agrees in sign",
            "E_hist_passes": bool(minutes_pass("E_hist_vs_A")), "F_hist_passes": bool(minutes_pass("F_hist_vs_A")),
            "participation_rule": f"keep the factorised P(play) only if its test log loss is within {LOGLOSS_TIE} of V1.5's",
            "keep_factorised_p_play": bool(fact_ok),
        },
    }
    report["finished"] = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"history-only-{stamp}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    log(f"report: {path}")
    log(json.dumps({"decision": report["decision"], "test": report["minutes_conditional"]["test"], "comparisons": comparisons,
                    "participation_test": participation["test"], "unconditional_test": unconditional}, indent=1, default=str))
    report["report_path"] = str(path)
    return report


def main() -> int:
    from app.database import SessionLocal

    with SessionLocal() as db:
        run(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
