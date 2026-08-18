import { useEffect, useState } from "react";
import {
  fetchEliteDisposalDiagnostic,
  fetchModelMarketDisagreements,
  type EliteDisposalBucket,
  type ModelMarketDisagreement,
} from "../api/client";
import "./ModelMarketDisagreementsView.css";

const DIRECTION_LABELS: Record<string, string> = {
  model_above_market: "Model above market",
  market_above_model: "Market above model",
};

/** Market Integrity + Final Weekly Picks stage, Section 18: "Model vs
 * Market Disagreements" — explicitly NOT an opportunity list, never
 * labelled "Best Bets". Surfaces the largest model/market probability
 * gaps in EITHER direction, including cases where the market is far more
 * confident than the model — a signal the model may be missing something,
 * not a betting signal. Section 19's elite-disposal historical bias
 * diagnostic is shown alongside it since both exist purely to investigate
 * model quality, never to change the promoted model based on one round. */
function ModelMarketDisagreementsView() {
  const [disagreements, setDisagreements] = useState<ModelMarketDisagreement[]>([]);
  const [buckets, setBuckets] = useState<EliteDisposalBucket[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchModelMarketDisagreements({ limit: 30 }), fetchEliteDisposalDiagnostic()])
      .then(([d, b]) => {
        setDisagreements(d);
        setBuckets(b);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load diagnostics"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="loading-state">Loading…</p>;
  if (error) return <div className="prop-insights-page__error">{error}</div>;

  return (
    <div className="disagreements-view">
      <section className="disagreements-view__section">
        <h2>Model vs Market Disagreements</h2>
        <p className="hint">
          A diagnostic, not a betting list — the largest gaps between the model's probability and the market's, in EITHER
          direction. A large "market above model" gap means the market is far more confident than the model; that's a
          signal worth investigating, not a reason to headline it as an opportunity.
        </p>
        {disagreements.length === 0 ? (
          <p className="hint">No large model/market disagreements currently flagged this round.</p>
        ) : (
          <div className="prop-insights-table-scroll">
            <table className="prop-insights-table">
              <thead>
                <tr>
                  <th>Opportunity</th>
                  <th>Direction</th>
                  <th>Model prob.</th>
                  <th>Market prob.</th>
                  <th>Difference</th>
                  <th>Best price</th>
                  <th>Confidence</th>
                  <th>Recent form</th>
                </tr>
              </thead>
              <tbody>
                {disagreements.map((d, i) => (
                  <tr key={i}>
                    <td>{d.label}</td>
                    <td>
                      <span className={`disagreements-view__direction disagreements-view__direction--${d.direction}`}>
                        {DIRECTION_LABELS[d.direction]}
                      </span>
                    </td>
                    <td>{(d.model_probability * 100).toFixed(1)}%</td>
                    <td>
                      {(d.market_probability * 100).toFixed(1)}%
                      {!d.overround_removed && <span className="prop-insights-table__raw-flag">raw implied</span>}
                    </td>
                    <td className={d.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                      {d.difference_pp >= 0 ? "+" : ""}
                      {(d.difference_pp * 100).toFixed(1)}pp
                    </td>
                    <td>
                      ${d.best_price.toFixed(2)} <span className="hint">{d.best_bookmaker}</span>
                    </td>
                    <td>{d.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient data")}</td>
                    <td>
                      {d.recent_form
                        ? `L5 avg ${d.recent_form.last5_avg?.toFixed(1) ?? "—"}${
                            d.recent_form.ewma != null ? `, EWMA ${d.recent_form.ewma.toFixed(1)}` : ""
                          }`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="disagreements-view__section">
        <h2>Elite Disposal Player Monitoring</h2>
        <p className="hint">
          Research diagnostic only — never used to retune the promoted model based on current-round observations. Buckets
          are based on each player's own historical AVERAGE ACTUAL disposals (ground truth), not reputation, using the
          promoted disposal model's 2016-2025 backtest evaluation predictions.
        </p>
        {buckets === null ? (
          <p className="hint">No promoted disposal model evaluation data available.</p>
        ) : (
          <div className="disagreements-view__buckets">
            {buckets.map((b) => (
              <div key={b.bucket} className="disagreements-view__bucket-card">
                <div className="disagreements-view__bucket-header">
                  <span className="disagreements-view__bucket-label">{b.label}</span>
                  <span className="hint">
                    {b.n_players} players · {b.n_predictions.toLocaleString()} predictions
                  </span>
                </div>
                <div className="disagreements-view__bucket-stats">
                  <span>Avg actual {b.avg_actual.toFixed(1)}</span>
                  <span>Avg predicted {b.avg_predicted.toFixed(1)}</span>
                  <span className={b.bias < 0 ? "prop-insights-table__diff-neg" : "prop-insights-table__diff-pos"}>
                    Bias {b.bias >= 0 ? "+" : ""}
                    {b.bias.toFixed(2)} ({b.bias < 0 ? "model under-predicts" : "model over-predicts"})
                  </span>
                  <span>MAE {b.mae.toFixed(2)}</span>
                </div>
                {b.most_under_predicted_players.length > 0 && (
                  <table className="disagreements-view__player-table">
                    <thead>
                      <tr>
                        <th>Player</th>
                        <th>n</th>
                        <th>Avg actual</th>
                        <th>Avg predicted</th>
                        <th>Bias</th>
                      </tr>
                    </thead>
                    <tbody>
                      {b.most_under_predicted_players.map((p) => (
                        <tr key={p.player_id}>
                          <td>{p.player_name}</td>
                          <td>{p.n_predictions}</td>
                          <td>{p.avg_actual.toFixed(1)}</td>
                          <td>{p.avg_predicted.toFixed(1)}</td>
                          <td className="prop-insights-table__diff-neg">{p.bias.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

export default ModelMarketDisagreementsView;
