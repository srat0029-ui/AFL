import type { GoalBacktestSummary, GoalCalibrationReport, GoalTeamDiagnostic } from "../api/client";

function num(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

const MODEL_DISPLAY_NAMES: Record<string, string> = {
  goals_baseline_last5: "Baseline: last 5",
  goals_baseline_last10: "Baseline: last 10",
  goals_baseline_ewma: "Baseline: EWMA",
  goals_baseline_season_avg: "Baseline: season avg",
  goals_baseline_team_adjusted_rate: "Baseline: team-adjusted rate",
  goals_poisson_regression: "Poisson regression",
  goals_negative_binomial: "Negative Binomial regression",
  goals_hurdle: "Hurdle model",
  goals_gbm_xgboost: "XGBoost",
  goals_gbm_lightgbm: "LightGBM",
};

function displayName(modelName: string): string {
  return MODEL_DISPLAY_NAMES[modelName] ?? modelName;
}

interface Props {
  summary: GoalBacktestSummary;
  calibration: GoalCalibrationReport | null;
  teamDiagnostic?: GoalTeamDiagnostic[];
}

function GoalProjections({ summary, calibration, teamDiagnostic }: Props) {
  const promoted = summary.promoted_model;
  const allComparisons = [...summary.baselines, ...summary.candidates].filter((r) => r.mae !== null).sort((a, b) => (a.mae ?? 0) - (b.mae ?? 0));

  return (
    <section className="backtest-panel">
      <h2>Player Models — Goal Projections</h2>
      <div className="backtest-callout backtest-callout--info">
        <strong>Historical model research only.</strong> Evaluates whether goal counts can be predicted from
        information available before each match — not live betting advice, and no bookmaker player-prop odds are
        integrated here.
      </div>

      <div className="backtest-callout backtest-callout--warning">
        <strong>Selection-eligibility limitation.</strong> Every prediction answers "given this player is selected
        and plays, what is their distribution of goals?" — it does NOT predict whether a player will be selected.
      </div>

      <h3>Summary</h3>
      <p className="hint">
        Promoted model: <strong>{displayName(promoted.model_name)}</strong> — evaluated on{" "}
        {promoted.evaluation_n?.toLocaleString()} player-games, seasons {promoted.evaluation_start_year}–
        {promoted.evaluation_end_year} (tuned on {promoted.tune_start_year}–{promoted.tune_end_year}). Distribution:{" "}
        <strong>{promoted.distribution_kind === "hurdle" ? "Two-part hurdle (NB)" : "Negative Binomial"}</strong>.
        Goals are low-count and zero-heavy (roughly two-thirds of player-games record zero goals), so probability
        calibration — not raw MAE — is the primary selection criterion here.
      </p>
      <div className="backtest-overall">
        <div className="backtest-stat">
          <span className="backtest-stat__label">MAE</span>
          <span className="backtest-stat__value">{num(promoted.overall_mae, 4)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">RMSE</span>
          <span className="backtest-stat__value">{num(promoted.overall_rmse, 4)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Bias</span>
          <span className="backtest-stat__value">{num(promoted.overall_bias, 4)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Actual P(0 goals)</span>
          <span className="backtest-stat__value">{pct(summary.zero_goal.actual_p0)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Predicted P(0 goals)</span>
          <span className="backtest-stat__value">{pct(summary.zero_goal.mean_predicted_p0)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Zero-goal Brier</span>
          <span className="backtest-stat__value">{num(summary.zero_goal.brier, 4)}</span>
        </div>
      </div>

      <h3>Baselines vs. candidate models</h3>
      <p className="hint">
        All models evaluated on the exact same {promoted.evaluation_n?.toLocaleString()} player-game rows. For this
        target, a rolling-average baseline can be MAE-competitive with fitted models while having no real
        threshold-probability mechanism — the promoted model is chosen for the best calibration among
        MAE-competitive candidates, not for the lowest MAE alone.
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
              <tr key={row.model_name} className={row.model_name === promoted.model_name ? "segment-table__highlight" : undefined}>
                <td>{displayName(row.model_name)}</td>
                <td>{num(row.mae, 4)}</td>
                <td>{num(row.rmse, 4)}</td>
                <td>{num(row.bias, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {calibration && (
        <>
          <h3>Threshold probability calibration</h3>
          <p className="hint">
            If the model says "30% chance of 2+ goals" across many similar predictions, roughly 30% of those games
            should actually reach 2+. Sample sizes shrink quickly at higher thresholds — n_positive shows exactly
            how many historical games actually reached that threshold, so rare-event calibration claims can be
            read with appropriate caution.
          </p>
          <div className="segment-table-scroll">
            <table className="segment-table">
              <thead>
                <tr>
                  <th>Threshold</th>
                  <th>N</th>
                  <th>N actually hit</th>
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
                    <td>{t.n_positive.toLocaleString()}</td>
                    <td>{num(t.brier, 4)}</td>
                    <td>{num(t.log_loss, 4)}</td>
                    <td>{num(t.ece, 4)}</td>
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
              <th>Bias</th>
            </tr>
          </thead>
          <tbody>
            {summary.season_breakdown.map((row) => (
              <tr key={row.season_year}>
                <td>{row.season_year}</td>
                <td>{row.n.toLocaleString()}</td>
                <td>{num(row.mae, 4)}</td>
                <td>{num(row.bias, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {teamDiagnostic && teamDiagnostic.length > 0 && (
        <>
          <h3>Internal diagnostic — upcoming team-goal totals</h3>
          <p className="hint">
            Sum of individual player-projected goals per team vs. that team's Poisson-model expected goals, for the
            next upcoming round only. A known discrepancy (see summary above) is exposed here, not hidden — this is
            a diagnostic, not a reconciliation; individual player projections are left unchanged.
          </p>
          <div className="segment-table-scroll">
            <table className="segment-table">
              <thead>
                <tr>
                  <th>Match</th>
                  <th>Team</th>
                  <th>Sum player xG</th>
                  <th>Team expected goals</th>
                  <th>Gap</th>
                </tr>
              </thead>
              <tbody>
                {teamDiagnostic.map((row) => (
                  <tr key={`${row.match_id}-${row.team_id}`}>
                    <td>{row.match_id}</td>
                    <td>{row.team_id}</td>
                    <td>{num(row.sum_predicted_goals, 2)}</td>
                    <td>{num(row.team_expected_goals, 2)}</td>
                    <td>{row.gap >= 0 ? "+" : ""}{num(row.gap, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export default GoalProjections;
