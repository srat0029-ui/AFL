import { useEffect, useState } from "react";
import "./BacktestPage.css";
import Disclaimer from "../components/Disclaimer";
import { fetchBacktest, type BacktestOverview, type BacktestSegment, type WinProbReport } from "../api/client";

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function num(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

function SegmentTable({ segments, metricKeys, metricLabels }: { segments: BacktestSegment[]; metricKeys: string[]; metricLabels: Record<string, string> }) {
  return (
    <div className="segment-table-scroll">
      <table className="segment-table">
        <thead>
          <tr>
            <th>Segment</th>
            <th>N</th>
            {metricKeys.map((k) => (
              <th key={k}>{metricLabels[k] ?? k}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {segments.map((s) => (
            <tr key={s.label}>
              <td>{s.label}</td>
              <td>{s.n}</td>
              {metricKeys.map((k) => (
                <td key={k}>{k === "accuracy" ? pct(s.metrics[k]) : num(s.metrics[k])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const WIN_PROB_METRIC_KEYS = ["brier_score", "log_loss", "accuracy"];
const WIN_PROB_METRIC_LABELS: Record<string, string> = {
  brier_score: "Brier score",
  log_loss: "Log loss",
  accuracy: "Accuracy",
};

const SCORING_METRIC_KEYS = ["total_points_mae", "margin_mae"];
const SCORING_METRIC_LABELS: Record<string, string> = {
  total_points_mae: "Total points MAE",
  margin_mae: "Margin MAE",
};

function WinProbSection({ report }: { report: WinProbReport }) {
  return (
    <section className="backtest-panel">
      <h2>{report.model_name === "elo" ? "Elo (match winner)" : "Poisson (match winner)"}</h2>
      <div className="backtest-overall">
        <div className="backtest-stat">
          <span className="backtest-stat__label">N</span>
          <span className="backtest-stat__value">{report.overall.n}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Brier score</span>
          <span className="backtest-stat__value">{num(report.overall.metrics.brier_score)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Log loss</span>
          <span className="backtest-stat__value">{num(report.overall.metrics.log_loss)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Accuracy</span>
          <span className="backtest-stat__value">{pct(report.overall.metrics.accuracy)}</span>
        </div>
      </div>

      <h3>Calibration (predicted bucket vs actual home-win rate)</h3>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Bucket</th>
              <th>N</th>
              <th>Predicted</th>
              <th>Actual</th>
            </tr>
          </thead>
          <tbody>
            {report.calibration
              .filter((b) => b.n > 0)
              .map((b) => (
                <tr key={b.bucket}>
                  <td>{b.bucket}</td>
                  <td>{b.n}</td>
                  <td>{pct(b.avg_predicted)}</td>
                  <td>{pct(b.actual_rate)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <h3>By season</h3>
      <SegmentTable segments={report.by_season} metricKeys={WIN_PROB_METRIC_KEYS} metricLabels={WIN_PROB_METRIC_LABELS} />

      <h3>By model conviction</h3>
      <p className="hint">
        How sure the model was, regardless of which side it favoured — not the same as the live confidence tiers shown
        on match pages, which also factor in market data this backtest doesn't use.
      </p>
      <SegmentTable segments={report.by_conviction} metricKeys={WIN_PROB_METRIC_KEYS} metricLabels={WIN_PROB_METRIC_LABELS} />

      <h3>By team</h3>
      <SegmentTable segments={report.by_team} metricKeys={WIN_PROB_METRIC_KEYS} metricLabels={WIN_PROB_METRIC_LABELS} />
    </section>
  );
}

function BacktestPage() {
  const [overview, setOverview] = useState<BacktestOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchBacktest()
      .then(setOverview)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load backtest"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="backtest-page">
      <h1>Backtesting</h1>
      <p className="subtitle">
        How the models would have performed historically, using only information that would genuinely have been
        available before each game (walk-forward, no data leakage).
      </p>

      {error && <div className="backtest-page__error">{error}</div>}
      {loading && <p className="hint">Loading…</p>}

      {!loading && !error && !overview && (
        <p className="hint">
          No backtest data available yet — run <code>elo_cli</code> and <code>poisson_cli</code> (see the README).
        </p>
      )}

      {overview && (
        <>
          <WinProbSection report={overview.elo} />
          <WinProbSection report={overview.poisson_win} />

          <section className="backtest-panel">
            <h2>Poisson (total points / margin)</h2>
            <div className="backtest-overall">
              <div className="backtest-stat">
                <span className="backtest-stat__label">N</span>
                <span className="backtest-stat__value">{overview.poisson_scoring.overall.n}</span>
              </div>
              <div className="backtest-stat">
                <span className="backtest-stat__label">Total points MAE</span>
                <span className="backtest-stat__value">{num(overview.poisson_scoring.overall.metrics.total_points_mae, 1)}</span>
              </div>
              <div className="backtest-stat">
                <span className="backtest-stat__label">Margin MAE</span>
                <span className="backtest-stat__value">{num(overview.poisson_scoring.overall.metrics.margin_mae, 1)}</span>
              </div>
            </div>
            <h3>By season</h3>
            <SegmentTable
              segments={overview.poisson_scoring.by_season}
              metricKeys={SCORING_METRIC_KEYS}
              metricLabels={SCORING_METRIC_LABELS}
            />
          </section>

          <section className="backtest-panel">
            <h2>Logged odds performance</h2>
            <p className="hint">
              Real win rate, ROI, and profit/loss computed only from odds you've actually logged on fixtures that
              have since been played — not historical market data (no free historical AFL odds source exists, and
              fabricating one would defeat the point). This section fills in automatically as you log odds on
              upcoming matches and they resolve.
            </p>

            {overview.logged_odds.n_total === 0 ? (
              <p className="hint">No resolved tracked selections yet.</p>
            ) : (
              <>
                <div className="backtest-overall">
                  <div className="backtest-stat">
                    <span className="backtest-stat__label">Resolved selections</span>
                    <span className="backtest-stat__value">{overview.logged_odds.n_resolved}</span>
                  </div>
                  <div className="backtest-stat">
                    <span className="backtest-stat__label">Win rate</span>
                    <span className="backtest-stat__value">{pct(overview.logged_odds.win_rate)}</span>
                  </div>
                  <div className="backtest-stat">
                    <span className="backtest-stat__label">ROI</span>
                    <span className="backtest-stat__value">
                      {overview.logged_odds.roi_pct === null ? "—" : `${overview.logged_odds.roi_pct.toFixed(1)}%`}
                    </span>
                  </div>
                  <div className="backtest-stat">
                    <span className="backtest-stat__label">P&amp;L (units)</span>
                    <span className="backtest-stat__value">{overview.logged_odds.total_pnl_units.toFixed(2)}</span>
                  </div>
                </div>

                <div className="segment-table-scroll">
                  <table className="segment-table">
                    <thead>
                      <tr>
                        <th>Match</th>
                        <th>Market</th>
                        <th>Selection</th>
                        <th>Price</th>
                        <th>Model %</th>
                        <th>Result</th>
                        <th>P&amp;L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.logged_odds.selections.map((s, i) => (
                        <tr key={i}>
                          <td>#{s.match_id}</td>
                          <td>{s.market_type}</td>
                          <td>{s.selection}</td>
                          <td>${s.price_decimal.toFixed(2)}</td>
                          <td>{pct(s.model_probability)}</td>
                          <td>{s.won === null ? "Void" : s.won ? "Won" : "Lost"}</td>
                          <td>{s.pnl_units.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
        </>
      )}

      <Disclaimer />
    </main>
  );
}

export default BacktestPage;
