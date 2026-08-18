import { useEffect, useState } from "react";
import "./BacktestPage.css";
import AdvancedModelComparison from "../components/AdvancedModelComparison";
import CalibrationChart from "../components/CalibrationChart";
import Disclaimer from "../components/Disclaimer";
import DisposalProjections from "../components/DisposalProjections";
import GoalProjections from "../components/GoalProjections";
import GradientBoostingComparison from "../components/GradientBoostingComparison";
import PoissonRevisionComparison from "../components/PoissonRevisionComparison";
import {
  fetchBacktest,
  fetchBacktestDetail,
  fetchBacktests,
  fetchBoostingComparison,
  fetchDisposalBacktestSummary,
  fetchDisposalCalibration,
  fetchGoalBacktestSummary,
  fetchGoalCalibration,
  fetchGoalUpcomingTeamDiagnostic,
  fetchLogisticComparison,
  fetchModelComparison,
  fetchPlayerModelRuns,
  fetchPoissonRevision,
  type BacktestDetail,
  type BacktestOverview,
  type BacktestSegment,
  type BacktestSummary,
  type BoostingComparisonOverview,
  type DisposalBacktestSummary,
  type DisposalCalibrationReport,
  type GoalBacktestSummary,
  type GoalCalibrationReport,
  type GoalTeamDiagnostic,
  type LogisticComparisonOverview,
  type ModelComparison,
  type PlayerModelRunList,
  type PoissonRevisionComparison as PoissonRevisionComparisonData,
  type WinProbReport,
} from "../api/client";

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function num(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
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

const BASELINE_DISPLAY_NAMES: Record<string, string> = {
  elo: "Elo",
  poisson: "Poisson",
  baseline_always_home: "Baseline: always home",
  baseline_historical_home_rate: "Baseline: historical home-win rate",
  baseline_simple_form: "Baseline: simple recent form",
};

function ModelEvaluationSection({ detail }: { detail: BacktestDetail }) {
  const wp = detail.win_prob;
  return (
    <section className="backtest-panel">
      <h2>{detail.model_name === "elo" ? "Elo — evaluation period" : "Poisson — evaluation period"}</h2>
      <p className="hint">
        Evaluation period: {wp.period.evaluation_start_year}–{wp.period.evaluation_end_year} ({wp.period.n_evaluation} games).
        {" "}Seasons {wp.period.warmup_start_year}–{wp.period.warmup_end_year} ({wp.period.n_warmup} games) were used only to
        build up model state (ratings/form) and are excluded from these numbers — a model starting from a neutral
        default rating in its first season isn't a fair read of its real quality.
        {wp.period.n_current_season > 0 && (
          <> The current {wp.period.current_season_year} season ({wp.period.n_current_season} games so far) is shown separately below, not blended in.</>
        )}
      </p>

      <div className="backtest-overall">
        <div className="backtest-stat">
          <span className="backtest-stat__label">N (evaluation)</span>
          <span className="backtest-stat__value">{wp.period.n_evaluation}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Brier score</span>
          <span className="backtest-stat__value">{num(wp.evaluation_metrics.brier_score)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Log loss</span>
          <span className="backtest-stat__value">{num(wp.evaluation_metrics.log_loss)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Accuracy</span>
          <span className="backtest-stat__value">{pct(wp.evaluation_metrics.accuracy)}</span>
        </div>
        {detail.scoring && (
          <>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Total points MAE</span>
              <span className="backtest-stat__value">{num(detail.scoring.evaluation_metrics.total_points_mae, 1)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Margin MAE</span>
              <span className="backtest-stat__value">{num(detail.scoring.evaluation_metrics.margin_mae, 1)}</span>
            </div>
          </>
        )}
      </div>

      <h3>Is this actually better than a simple heuristic?</h3>
      <p className="hint">
        Same evaluation-period games, scored the same way, for {detail.model_name === "elo" ? "Elo" : "Poisson"} and
        three deliberately simple baselines: always picking the home team, the home-win rate observed so far
        (updated as it goes, never peeking ahead), and each team's win rate over its last 10 games.
      </p>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Model</th>
              <th>N</th>
              <th>Brier score</th>
              <th>Log loss</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            {wp.baseline_comparison.map((row) => (
              <tr key={row.name} className={row.name === detail.model_name ? "segment-table__highlight" : undefined}>
                <td>{BASELINE_DISPLAY_NAMES[row.name] ?? row.name}</td>
                <td>{row.n}</td>
                <td>{num(row.metrics.brier_score)}</td>
                <td>{num(row.metrics.log_loss)}</td>
                <td>{pct(row.metrics.accuracy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Calibration (evaluation period, folded onto the favoured side)</h3>
      <p className="hint">
        A prediction is "well calibrated" if, among every game it called at (say) 70% for the favourite, the
        favourite actually won about 70% of the time — no more, no less. Points should sit near the diagonal;
        bigger points have more games behind them. Expected Calibration Error (ECE):{" "}
        <strong>{wp.calibration_ece === null ? "—" : wp.calibration_ece.toFixed(4)}</strong> (0 = perfect).
      </p>
      <CalibrationChart buckets={wp.calibration} />
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
            {wp.calibration
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

      {detail.scoring && (
        <>
          <h3>Score distribution honesty (not just the mean)</h3>
          <p className="hint">
            Poisson doesn't just output an expected score — it outputs a full probability distribution. If that
            distribution is honest, the actual result should fall inside its own 50% interval about half the time,
            and inside its 80% interval about 80% of the time.
          </p>
          <div className="backtest-overall">
            <div className="backtest-stat">
              <span className="backtest-stat__label">Total in 50% interval</span>
              <span className="backtest-stat__value">{pct(detail.scoring.interval_coverage["50pct"]?.total_hit_rate)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Total in 80% interval</span>
              <span className="backtest-stat__value">{pct(detail.scoring.interval_coverage["80pct"]?.total_hit_rate)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Margin in 50% interval</span>
              <span className="backtest-stat__value">{pct(detail.scoring.interval_coverage["50pct"]?.margin_hit_rate)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Margin in 80% interval</span>
              <span className="backtest-stat__value">{pct(detail.scoring.interval_coverage["80pct"]?.margin_hit_rate)}</span>
            </div>
          </div>
        </>
      )}

      <h3>By season (evaluation period)</h3>
      <SegmentTable segments={wp.by_season} metricKeys={WIN_PROB_METRIC_KEYS} metricLabels={WIN_PROB_METRIC_LABELS} />
    </section>
  );
}

function ModelComparisonSection({ comparison }: { comparison: ModelComparison }) {
  return (
    <section className="backtest-panel">
      <h2>Elo vs. Poisson — do they agree?</h2>
      <p className="hint">
        Both models are scored independently, but they're built from completely different signals (Elo: rating
        history; Poisson: rolling scoring form) — how often they land on a similar number, and who tends to be right
        when they don't, is useful context for a future ensemble even though neither model currently defers to the
        other. Mean absolute disagreement across all {comparison.n_matches} matches:{" "}
        <strong>{pct(comparison.mean_absolute_disagreement)}</strong>.
      </p>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Disagreement</th>
              <th>N</th>
              <th>Elo Brier</th>
              <th>Elo accuracy</th>
              <th>Poisson Brier</th>
              <th>Poisson accuracy</th>
              <th>Actual home-win rate</th>
            </tr>
          </thead>
          <tbody>
            {comparison.disagreement_buckets.map((b) => (
              <tr key={b.label}>
                <td>{b.label}</td>
                <td>{b.n}</td>
                <td>{num(b.elo_metrics.brier_score)}</td>
                <td>{pct(b.elo_metrics.accuracy)}</td>
                <td>{num(b.poisson_metrics.brier_score)}</td>
                <td>{pct(b.poisson_metrics.accuracy)}</td>
                <td>{pct(b.actual_home_win_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Season-by-season stability</h3>
      <p className="hint">Both models, side by side, per season — a quick way to spot a season either one handled badly.</p>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Season</th>
              <th>Games</th>
              <th>Elo accuracy</th>
              <th>Elo Brier</th>
              <th>Elo log loss</th>
              <th>Poisson total MAE</th>
              <th>Poisson margin MAE</th>
              <th>Home win rate</th>
            </tr>
          </thead>
          <tbody>
            {comparison.season_stability.map((row) => (
              <tr key={row.season_year}>
                <td>{row.season_year}</td>
                <td>{row.n_games}</td>
                <td>{pct(row.elo_accuracy)}</td>
                <td>{num(row.elo_brier)}</td>
                <td>{num(row.elo_log_loss)}</td>
                <td>{num(row.poisson_total_mae, 1)}</td>
                <td>{num(row.poisson_margin_mae, 1)}</td>
                <td>{pct(row.home_win_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FullHistorySection({ report }: { report: WinProbReport }) {
  return (
    <section className="backtest-panel">
      <h2>{report.model_name === "elo" ? "Elo — full history detail" : "Poisson — full history detail"}</h2>
      <p className="hint">
        Every completed match ever ingested, including the early warm-up seasons excluded from the evaluation-period
        numbers above — useful for by-team and by-conviction detail, not for judging overall model quality.
      </p>
      <h3>By model conviction</h3>
      <SegmentTable segments={report.by_conviction} metricKeys={WIN_PROB_METRIC_KEYS} metricLabels={WIN_PROB_METRIC_LABELS} />
      <h3>By team</h3>
      <SegmentTable segments={report.by_team} metricKeys={WIN_PROB_METRIC_KEYS} metricLabels={WIN_PROB_METRIC_LABELS} />
    </section>
  );
}

function BacktestPage() {
  const [summaries, setSummaries] = useState<BacktestSummary[]>([]);
  const [eloDetail, setEloDetail] = useState<BacktestDetail | null>(null);
  const [poissonDetail, setPoissonDetail] = useState<BacktestDetail | null>(null);
  const [comparison, setComparison] = useState<ModelComparison | null>(null);
  const [overview, setOverview] = useState<BacktestOverview | null>(null);
  const [logisticOverview, setLogisticOverview] = useState<LogisticComparisonOverview | null>(null);
  const [boostingOverview, setBoostingOverview] = useState<BoostingComparisonOverview | null>(null);
  const [poissonRevision, setPoissonRevision] = useState<PoissonRevisionComparisonData | null>(null);
  const [playerModelRuns, setPlayerModelRuns] = useState<PlayerModelRunList | null>(null);
  const [disposalSummary, setDisposalSummary] = useState<DisposalBacktestSummary | null>(null);
  const [disposalCalibration, setDisposalCalibration] = useState<DisposalCalibrationReport | null>(null);
  const [goalSummary, setGoalSummary] = useState<GoalBacktestSummary | null>(null);
  const [goalCalibration, setGoalCalibration] = useState<GoalCalibrationReport | null>(null);
  const [goalTeamDiagnostic, setGoalTeamDiagnostic] = useState<GoalTeamDiagnostic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetchBacktests().catch(() => []),
      fetchBacktestDetail("elo"),
      fetchBacktestDetail("poisson"),
      fetchModelComparison(),
      fetchBacktest(),
      fetchLogisticComparison(),
      fetchBoostingComparison(),
      fetchPoissonRevision(),
      fetchPlayerModelRuns(),
      fetchDisposalBacktestSummary(),
      fetchDisposalCalibration(),
      fetchGoalBacktestSummary(),
      fetchGoalCalibration(),
      fetchGoalUpcomingTeamDiagnostic().catch(() => []),
    ])
      .then(
        ([
          summaryList,
          elo,
          poisson,
          cmp,
          legacyOverview,
          logisticCmp,
          boostingCmp,
          poissonRev,
          playerRuns,
          disposalSum,
          disposalCal,
          goalSum,
          goalCal,
          goalTeamDiag,
        ]) => {
          setSummaries(summaryList);
          setEloDetail(elo);
          setPoissonDetail(poisson);
          setComparison(cmp);
          setOverview(legacyOverview);
          setLogisticOverview(logisticCmp);
          setBoostingOverview(boostingCmp);
          setPoissonRevision(poissonRev);
          setPlayerModelRuns(playerRuns);
          setDisposalSummary(disposalSum);
          setDisposalCalibration(disposalCal);
          setGoalSummary(goalSum);
          setGoalCalibration(goalCal);
          setGoalTeamDiagnostic(goalTeamDiag);
        }
      )
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load backtest"))
      .finally(() => setLoading(false));
  }, []);

  const modelsAvailable = eloDetail !== null && poissonDetail !== null;

  return (
    <main className="backtest-page">
      <h1>Model Evaluation &amp; Backtesting</h1>
      <p className="subtitle">
        How the models would have performed historically, using only information that would genuinely have been
        available before each game (walk-forward, no data leakage).
      </p>

      {error && <div className="error-banner">{error}</div>}
      {loading && <p className="loading-state">Loading…</p>}

      {!loading && !error && !modelsAvailable && (
        <p className="hint">
          No backtest data available yet — run <code>elo_cli</code> and <code>poisson_cli</code> (see the README).
        </p>
      )}

      {modelsAvailable && (
        <>
          <section className="backtest-callout backtest-callout--info">
            <strong>Predictive performance ≠ betting profitability.</strong> Everything in this section measures
            whether the models' probabilities matched what actually happened. It says nothing about whether betting
            on them would have made money — that requires real historical bookmaker odds, which this project doesn't
            have (see "Betting profitability" further down for what little data exists).
          </section>

          {summaries.length > 0 && (
            <section className="backtest-panel">
              <h2>Summary</h2>
              <div className="segment-table-scroll">
                <table className="segment-table">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>Evaluated games</th>
                      <th>Brier</th>
                      <th>Log loss</th>
                      <th>Accuracy</th>
                      <th>Total MAE</th>
                      <th>Margin MAE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summaries.map((s) => (
                      <tr key={s.id}>
                        <td>{s.model_name}</td>
                        <td>{s.n_evaluation}</td>
                        <td>{num(s.headline_metrics.brier_score)}</td>
                        <td>{num(s.headline_metrics.log_loss)}</td>
                        <td>{pct(s.headline_metrics.accuracy)}</td>
                        <td>{num(s.headline_metrics.total_points_mae, 1)}</td>
                        <td>{num(s.headline_metrics.margin_mae, 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {eloDetail && <ModelEvaluationSection detail={eloDetail} />}
          {poissonDetail && <ModelEvaluationSection detail={poissonDetail} />}
          {comparison && <ModelComparisonSection comparison={comparison} />}
          {logisticOverview ? (
            <AdvancedModelComparison overview={logisticOverview} />
          ) : (
            <section className="backtest-panel">
              <h2>Advanced Model Comparison</h2>
              <p className="hint">
                Not available yet — run <code>python -m app.modelling.logistic_cli</code> (see the README).
              </p>
            </section>
          )}

          {boostingOverview ? (
            <GradientBoostingComparison overview={boostingOverview} />
          ) : (
            <section className="backtest-panel">
              <h2>Gradient Boosting</h2>
              <p className="hint">
                Not available yet — requires the elo and poisson models to be run first (see the README).
              </p>
            </section>
          )}

          {poissonRevision ? (
            <PoissonRevisionComparison comparison={poissonRevision} />
          ) : (
            <section className="backtest-panel">
              <h2>Poisson Season-Transition Revision</h2>
              <p className="hint">
                Not available yet — run <code>python -m app.modelling.poisson_cli</code> first (see the README).
              </p>
            </section>
          )}

          {playerModelRuns && disposalSummary ? (
            <DisposalProjections runList={playerModelRuns} summary={disposalSummary} calibration={disposalCalibration} />
          ) : (
            <section className="backtest-panel">
              <h2>Player Models — Disposal Projections</h2>
              <p className="hint">
                Not available yet — run <code>python -m app.player_modelling.disposal_cli</code> first (see the README).
              </p>
            </section>
          )}

          {goalSummary ? (
            <GoalProjections summary={goalSummary} calibration={goalCalibration} teamDiagnostic={goalTeamDiagnostic} />
          ) : (
            <section className="backtest-panel">
              <h2>Player Models — Goal Projections</h2>
              <p className="hint">
                Not available yet — run <code>python -m app.player_modelling.goal_cli</code> first (see the README).
              </p>
            </section>
          )}

          {overview && (
            <>
              <FullHistorySection report={overview.elo} />
              <FullHistorySection report={overview.poisson_win} />

              <section className="backtest-panel">
                <h2>Poisson — full history (total points / margin)</h2>
                <h3>By season</h3>
                <SegmentTable
                  segments={overview.poisson_scoring.by_season}
                  metricKeys={SCORING_METRIC_KEYS}
                  metricLabels={SCORING_METRIC_LABELS}
                />
              </section>

              <section className="backtest-panel">
                <h2>Betting profitability</h2>
                <div className="backtest-callout backtest-callout--warning">
                  This is a fundamentally different, much weaker claim than the predictive-performance numbers above.
                  It is <strong>not</strong> derived from historical bookmaker odds — no free source of those exists
                  for AFL, and fabricating one would defeat the point of tracking this honestly. It reflects only
                  odds you've personally logged on fixtures that have since resolved. Until real odds accumulate here,
                  betting profitability should be treated as <strong>unknown</strong>, not "probably good because the
                  predictions above look reasonable."
                </div>

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
        </>
      )}

      <Disclaimer />
    </main>
  );
}

export default BacktestPage;
