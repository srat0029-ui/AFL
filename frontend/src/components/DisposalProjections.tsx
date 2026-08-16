import type {
  DisposalBacktestSummary,
  DisposalCalibrationReport,
  PlayerModelRunList,
} from "../api/client";

function num(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

const MODEL_DISPLAY_NAMES: Record<string, string> = {
  disposals_baseline_last5: "Baseline: last 5",
  disposals_baseline_last10: "Baseline: last 10",
  disposals_baseline_ewma: "Baseline: EWMA",
  disposals_baseline_season_avg: "Baseline: season avg",
  disposals_ridge: "Ridge regression",
  disposals_poisson_regression: "Poisson regression",
  disposals_negative_binomial: "Negative Binomial regression",
  disposals_gbm_xgboost: "XGBoost",
  disposals_gbm_lightgbm: "LightGBM",
};

function displayName(modelName: string): string {
  return MODEL_DISPLAY_NAMES[modelName] ?? modelName;
}

interface Props {
  runList: PlayerModelRunList;
  summary: DisposalBacktestSummary;
  calibration: DisposalCalibrationReport | null;
}

function DisposalProjections({ runList, summary, calibration }: Props) {
  const promoted = summary.promoted_model;
  const allComparisons = [...summary.baselines, ...summary.candidates].sort((a, b) => a.mae - b.mae);

  return (
    <section className="backtest-panel">
      <h2>Player Models — Disposal Projections</h2>
      <div className="backtest-callout backtest-callout--info">
        <strong>Historical model research only.</strong> This section evaluates whether disposal counts can be
        predicted from information available before each match — it is not live betting advice and no bookmaker
        player-prop odds are integrated here. All {runList.runs.length} model runs shown below were persisted by{" "}
        <code>python -m app.player_modelling.disposal_cli</code>.
      </div>

      <div className="backtest-callout backtest-callout--warning">
        <strong>Selection-eligibility limitation.</strong> Every prediction here answers "given this player is
        selected and plays, how many disposals will they record?" — it does NOT predict whether a player will be
        selected at all. Team-selection prediction is a separate, later stage.
      </div>

      <h3>Summary</h3>
      <p className="hint">
        Promoted model: <strong>{displayName(promoted.model_name)}</strong> — evaluated on{" "}
        {promoted.evaluation_n?.toLocaleString()} player-games, seasons {promoted.evaluation_start_year}–
        {promoted.evaluation_end_year} (tuned on {promoted.tune_start_year}–{promoted.tune_end_year}). Distribution
        method: <strong>{promoted.distribution_method === "nb" ? "Negative Binomial" : "empirical residual"}</strong>.
      </p>
      <div className="backtest-overall">
        <div className="backtest-stat">
          <span className="backtest-stat__label">MAE</span>
          <span className="backtest-stat__value">{num(promoted.overall_mae)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">RMSE</span>
          <span className="backtest-stat__value">{num(promoted.overall_rmse)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Bias</span>
          <span className="backtest-stat__value">{num(promoted.overall_bias)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">±2 disposals</span>
          <span className="backtest-stat__value">{pct(summary.within_2)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">±5 disposals</span>
          <span className="backtest-stat__value">{pct(summary.within_5)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">±10 disposals</span>
          <span className="backtest-stat__value">{pct(summary.within_10)}</span>
        </div>
      </div>

      <h3>Baselines vs. candidate models</h3>
      <p className="hint">
        All models evaluated on the exact same {promoted.evaluation_n?.toLocaleString()} player-game rows. A
        complex model is only worth using if it clearly beats the strongest simple rolling-average baseline — the
        promoted model is highlighted, even when a baseline wins.
      </p>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Model</th>
              <th>MAE</th>
              <th>RMSE</th>
              <th>Bias</th>
            </tr>
          </thead>
          <tbody>
            {allComparisons.map((row) => (
              <tr
                key={row.model_name}
                className={row.model_name === promoted.model_name ? "segment-table__highlight" : undefined}
              >
                <td>{displayName(row.model_name)}</td>
                <td>{num(row.mae)}</td>
                <td>{num(row.rmse)}</td>
                <td>{num(row.bias)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {calibration && (
        <>
          <h3>Threshold probability calibration</h3>
          <p className="hint">
            If the model says "48% chance of 30+ disposals" across many similar predictions, roughly 48% of those
            games should actually reach 30+. Brier score and log loss are lower-is-better; ECE (Expected Calibration
            Error) is the average gap between predicted probability and actual outcome rate — 0 is perfect.
          </p>
          <div className="segment-table-scroll">
            <table className="segment-table">
              <thead>
                <tr>
                  <th>Threshold</th>
                  <th>N</th>
                  <th>Brier</th>
                  <th>Log loss</th>
                  <th>ECE</th>
                </tr>
              </thead>
              <tbody>
                {calibration.thresholds.map((t) => (
                  <tr key={t.threshold}>
                    <td>{t.threshold}+</td>
                    <td>{t.n.toLocaleString()}</td>
                    <td>{num(t.brier, 4)}</td>
                    <td>{num(t.log_loss, 4)}</td>
                    <td>{num(t.ece, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3>Prediction interval coverage</h3>
          <p className="hint">
            If an 80% interval is well calibrated, about 80% of actual disposal counts should fall inside it. Width
            matters too — a trivially huge interval isn't useful even if well calibrated.
          </p>
          <div className="segment-table-scroll">
            <table className="segment-table">
              <thead>
                <tr>
                  <th>Target coverage</th>
                  <th>N</th>
                  <th>Empirical coverage</th>
                  <th>Mean width (disposals)</th>
                </tr>
              </thead>
              <tbody>
                {calibration.intervals.map((iv) => (
                  <tr key={iv.coverage_target}>
                    <td>{Math.round(iv.coverage_target * 100)}%</td>
                    <td>{iv.n.toLocaleString()}</td>
                    <td>{pct(iv.empirical_coverage)}</td>
                    <td>{num(iv.mean_width, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h3>Season stability</h3>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Season</th>
              <th>N</th>
              <th>MAE</th>
              <th>RMSE</th>
              <th>Bias</th>
            </tr>
          </thead>
          <tbody>
            {summary.season_breakdown.map((row) => (
              <tr key={row.season_year}>
                <td>{row.season_year}</td>
                <td>{row.n.toLocaleString()}</td>
                <td>{num(row.mae)}</td>
                <td>{num(row.rmse)}</td>
                <td>{num(row.bias)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default DisposalProjections;
