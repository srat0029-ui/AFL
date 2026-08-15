import type { BoostingComparisonOverview } from "../api/client";

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function num(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

function GradientBoostingComparison({ overview }: { overview: BoostingComparisonOverview }) {
  const best = overview.best;
  const sortedCandidates = [...overview.feature_set_candidates].sort((a, b) => a.brier_score - b.brier_score);
  const sortedImportance = Object.entries(best.permutation_importance).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const maxImportance = Math.max(0.0001, ...sortedImportance.map(([, v]) => Math.abs(v)));

  return (
    <section className="backtest-panel">
      <h2>Gradient Boosting</h2>
      <p className="hint">
        Can XGBoost/LightGBM extract signal from the same leakage-safe feature pipeline that a plain logistic
        regression couldn't? Both libraries were tuned (shallow trees, modest search space, tuning-window only) and
        evaluated across five controlled feature sets (A: Elo only, B: recent form/scoring + Elo, C: + inside 50s,
        D: all advanced stats + Elo, E: all advanced stats without Elo) on the identical {overview.n_eval}-match
        evaluation set ({overview.evaluation_start_year}–{overview.evaluation_end_year}) as every other model here.
      </p>

      <h3>All feature-set × library candidates (raw, uncalibrated Brier)</h3>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Feature set</th>
              <th>Library</th>
              <th>N</th>
              <th>Brier (raw)</th>
              <th>Log loss</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            {sortedCandidates.map((c) => (
              <tr
                key={`${c.library}-${c.label}`}
                className={c.library === best.library && c.label === best.label ? "segment-table__highlight" : undefined}
              >
                <td>{c.label}</td>
                <td>{c.library}</td>
                <td>{c.n_eval}</td>
                <td>{num(c.brier_score)}</td>
                <td>{num(c.log_loss)}</td>
                <td>{pct(c.accuracy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Best candidate: {best.library} / {best.label}</h3>
      <p className="hint">
        Calibration method selected on tuning-window-only inner-validation predictions:{" "}
        <strong>{best.calibration_method}</strong>. Hyperparameters:{" "}
        {Object.entries(best.hyperparameters)
          .map(([k, v]) => `${k}=${v}`)
          .join(", ")}
        .
      </p>
      <div className="backtest-overall">
        <div className="backtest-stat">
          <span className="backtest-stat__label">Brier (calibrated)</span>
          <span className="backtest-stat__value">{num(best.brier_score)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Log loss</span>
          <span className="backtest-stat__value">{num(best.log_loss)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Accuracy</span>
          <span className="backtest-stat__value">{pct(best.accuracy)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">ECE</span>
          <span className="backtest-stat__value">{num(best.calibration_ece)}</span>
        </div>
      </div>

      <p className="hint" style={{ marginTop: "0.9rem" }}>
        Brier improvement vs Elo: <strong>{best.bootstrap_vs_elo.point_estimate >= 0 ? "+" : ""}{num(best.bootstrap_vs_elo.point_estimate)}</strong>
        {" "}— 95% bootstrap interval [{num(best.bootstrap_vs_elo.ci_low)}, {num(best.bootstrap_vs_elo.ci_high)}]
        {best.bootstrap_vs_elo.excludes_zero ? " (distinguishable from noise)" : " (not distinguishable from noise)"}
      </p>

      <div className={`promotion-badge ${best.promotion.promote ? "promotion-badge--promote" : "promotion-badge--keep"}`}>
        {best.promotion.promote ? "Meets promotion bar" : "Does not meet promotion bar — Elo remains primary"}
      </div>
      <ul className="promotion-reasons">
        {best.promotion.reasons.map((r) => (
          <li key={r} className={r.startsWith("PASS") ? "promotion-reason--pass" : "promotion-reason--fail"}>
            {r}
          </li>
        ))}
      </ul>

      <h4>Feature-group ablation (Elo alone vs Elo + group)</h4>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Group</th>
              <th>Brier</th>
              <th>vs Elo alone</th>
            </tr>
          </thead>
          <tbody>
            {best.feature_group_ablation.map((a) => (
              <tr key={a.label}>
                <td>{a.label}</td>
                <td>{num(a.brier_score)}</td>
                <td className={a.brier_vs_elo_alone !== null && a.brier_vs_elo_alone < 0 ? "delta-positive" : "delta-negative"}>
                  {a.brier_vs_elo_alone === null ? "—" : `${a.brier_vs_elo_alone >= 0 ? "+" : ""}${num(a.brier_vs_elo_alone)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4>Permutation importance</h4>
      <div className="coef-bars">
        {sortedImportance.map(([name, value]) => (
          <div className="coef-bar-row" key={name}>
            <span className="coef-bar-label">{name}</span>
            <div className="coef-bar-track">
              <div className="coef-bar-zero" />
              <div
                className="coef-bar-fill coef-bar-fill--positive"
                style={{ width: `${(Math.abs(value) / maxImportance) * 50}%`, left: "50%" }}
              />
            </div>
            <span className="coef-bar-value">{value.toFixed(4)}</span>
          </div>
        ))}
      </div>

      {best.shap_importance && (
        <>
          <h4>SHAP importance (native XGBoost pred_contribs)</h4>
          <div className="segment-table-scroll">
            <table className="segment-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Mean |SHAP|</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(best.shap_importance)
                  .sort((a, b) => b[1] - a[1])
                  .map(([name, value]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>{value.toFixed(4)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {best.interactions.length > 0 && (
        <>
          <h4>Feature interactions (SHAP interaction values)</h4>
          <p className="hint">
            Only shown for XGBoost — LightGBM has no equivalent native API. Main-effect magnitudes are included for
            context: an interaction only matters if it's a non-trivial fraction of either feature's own effect.
          </p>
          <div className="segment-table-scroll">
            <table className="segment-table">
              <thead>
                <tr>
                  <th>Pair</th>
                  <th>Mean |interaction|</th>
                  <th>Main effect A</th>
                  <th>Main effect B</th>
                </tr>
              </thead>
              <tbody>
                {best.interactions.map((i) => (
                  <tr key={i.label}>
                    <td>{i.label}</td>
                    <td>{i.mean_abs_interaction.toFixed(5)}</td>
                    <td>{i.mean_abs_main_effect_a.toFixed(5)}</td>
                    <td>{i.mean_abs_main_effect_b.toFixed(5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h4>By season</h4>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Season</th>
              <th>N</th>
              <th>Brier</th>
              <th>Log loss</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            {best.by_season.map((s) => (
              <tr key={s.label}>
                <td>{s.label}</td>
                <td>{s.n}</td>
                <td>{num(s.metrics.brier_score)}</td>
                <td>{num(s.metrics.log_loss)}</td>
                <td>{pct(s.metrics.accuracy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4>Disagreement vs Elo</h4>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Disagreement</th>
              <th>N</th>
              <th>Elo Brier</th>
              <th>Boosting Brier</th>
              <th>Home win rate</th>
            </tr>
          </thead>
          <tbody>
            {best.disagreement_vs_elo.disagreement_buckets.map((bucket) => (
              <tr key={bucket.label}>
                <td>{bucket.label}</td>
                <td>{bucket.n}</td>
                <td>{num(bucket.elo_metrics.brier_score)}</td>
                <td>{num(bucket.logistic_metrics.brier_score)}</td>
                <td>{pct(bucket.actual_home_win_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Ensemble (Elo + boosting)</h3>
      <p className="hint">
        A simple weighted average of Elo's and boosting's probabilities, with the weight selected on tuning-window
        inner-validation only — never fit against evaluation-set outcomes. Selected weight on boosting:{" "}
        <strong>{overview.ensemble.boosting_weight.toFixed(2)}</strong>.
      </p>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Model</th>
              <th>N</th>
              <th>Brier</th>
              <th>Log loss</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Elo</td>
              <td>{overview.ensemble.elo.n}</td>
              <td>{num(overview.ensemble.elo.brier_score)}</td>
              <td>{num(overview.ensemble.elo.log_loss)}</td>
              <td>{pct(overview.ensemble.elo.accuracy)}</td>
            </tr>
            <tr>
              <td>Boosting</td>
              <td>{overview.ensemble.boosting.n}</td>
              <td>{num(overview.ensemble.boosting.brier_score)}</td>
              <td>{num(overview.ensemble.boosting.log_loss)}</td>
              <td>{pct(overview.ensemble.boosting.accuracy)}</td>
            </tr>
            <tr className={overview.ensemble.use_ensemble ? "segment-table__highlight" : undefined}>
              <td>Elo + boosting ensemble</td>
              <td>{overview.ensemble.ensemble.n}</td>
              <td>{num(overview.ensemble.ensemble.brier_score)}</td>
              <td>{num(overview.ensemble.ensemble.log_loss)}</td>
              <td>{pct(overview.ensemble.ensemble.accuracy)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className={`promotion-badge ${overview.ensemble.use_ensemble ? "promotion-badge--promote" : "promotion-badge--keep"}`}>
        {overview.ensemble.use_ensemble
          ? "Ensemble beats both Elo and boosting alone — worth using"
          : "Ensemble does not clearly beat Elo alone — not used"}
      </div>
    </section>
  );
}

export default GradientBoostingComparison;
