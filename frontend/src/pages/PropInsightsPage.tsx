import { useEffect, useMemo, useState } from "react";
import "./PropInsightsPage.css";
import Disclaimer from "../components/Disclaimer";
import { fetchPropInsights, type ConfidenceTierLive, type EdgeCategory, type PlayerPropMarketType, type PropInsight } from "../api/client";

const EDGE_LABELS: Record<EdgeCategory, string> = {
  no_meaningful_difference: "No meaningful difference",
  small_difference: "Small difference",
  moderate_difference: "Moderate difference",
  larger_difference: "Larger difference",
};

const CONFIDENCE_LABELS: Record<ConfidenceTierLive, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

type SortKey = "difference_pp" | "expected_value" | "confidence_tier" | "market_type";

const CONFIDENCE_RANK: Record<ConfidenceTierLive, number> = {
  insufficient_history: 0,
  lower_confidence: 1,
  moderate_confidence: 2,
  higher_confidence: 3,
};

function PropInsightsPage() {
  const [rows, setRows] = useState<PropInsight[]>([]);
  const [market, setMarket] = useState<PlayerPropMarketType | "">("");
  const [confidence, setConfidence] = useState<ConfidenceTierLive | "">("");
  const [sortKey, setSortKey] = useState<SortKey>("difference_pp");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchPropInsights({ market: market || undefined, confidence: confidence || undefined })
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load prop insights"))
      .finally(() => setLoading(false));
  }, [market, confidence]);

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      if (sortKey === "difference_pp") return Math.abs(b.difference_pp) - Math.abs(a.difference_pp);
      if (sortKey === "expected_value") return b.expected_value - a.expected_value;
      if (sortKey === "confidence_tier") return CONFIDENCE_RANK[b.confidence_tier] - CONFIDENCE_RANK[a.confidence_tier];
      return a.market_type.localeCompare(b.market_type);
    });
    return copy;
  }, [rows, sortKey]);

  return (
    <main className="prop-insights-page">
      <header className="prop-insights-page__header">
        <h1>Prop Insights</h1>
        <p className="hint">
          Ranks manually-entered bookmaker player-prop offers against the model's own probabilities. Model
          probability, fair odds, and expected value are model estimates only — not guaranteed outcomes. Enter
          offers on a match's detail page.
        </p>
      </header>

      <section className="prop-insights-page__filters">
        <label>
          Market
          <select value={market} onChange={(e) => setMarket(e.target.value as PlayerPropMarketType | "")}>
            <option value="">All markets</option>
            <option value="player_disposals">Disposals</option>
            <option value="player_goals">Goals</option>
          </select>
        </label>
        <label>
          Min. confidence
          <select value={confidence} onChange={(e) => setConfidence(e.target.value as ConfidenceTierLive | "")}>
            <option value="">Any</option>
            <option value="lower_confidence">Lower+</option>
            <option value="moderate_confidence">Moderate+</option>
            <option value="higher_confidence">Higher only</option>
          </select>
        </label>
        <label>
          Sort by
          <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
            <option value="difference_pp">Probability difference</option>
            <option value="expected_value">Model-estimated EV</option>
            <option value="confidence_tier">Confidence</option>
            <option value="market_type">Market type</option>
          </select>
        </label>
      </section>

      {loading && <p className="hint">Loading…</p>}
      {error && <div className="prop-insights-page__error">{error}</div>}

      {!loading && !error && sorted.length === 0 && (
        <p className="hint">No player prop quotes recorded yet — add some from a match's detail page.</p>
      )}

      {!loading && !error && sorted.length > 0 && (
        <div className="prop-insights-table-scroll">
          <table className="prop-insights-table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Market</th>
                <th>Model prob.</th>
                <th>Model fair odds</th>
                <th>Offered odds</th>
                <th>Market implied prob.</th>
                <th>Difference</th>
                <th>Model-est. EV</th>
                <th>Confidence</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.id}>
                  <td>{r.player_name}</td>
                  <td>
                    {r.market_type === "player_disposals" ? "Disposals" : "Goals"}{" "}
                    {r.line_type === "multi_plus" ? `${r.threshold.toFixed(1)}+` : `o/u ${r.threshold.toFixed(1)}`}
                    <br />
                    <span className="hint">{r.bookmaker_name}</span>
                  </td>
                  <td>{(r.model_probability * 100).toFixed(1)}%</td>
                  <td>${r.model_fair_odds.toFixed(2)}</td>
                  <td>${r.offered_odds.toFixed(2)}</td>
                  <td title={r.overround_removed ? "Vig removed" : "Raw implied — vig not removed"}>
                    {((r.devigged_probability ?? r.raw_implied_probability) * 100).toFixed(1)}%
                    {!r.overround_removed && <span className="prop-insights-table__raw-flag">raw</span>}
                  </td>
                  <td className={r.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                    {r.difference_pp >= 0 ? "+" : ""}
                    {(r.difference_pp * 100).toFixed(1)}pp
                  </td>
                  <td className={r.expected_value >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                    {r.expected_value >= 0 ? "+" : ""}
                    {r.expected_value.toFixed(2)}
                  </td>
                  <td>
                    <span className={`confidence-badge confidence-badge--${r.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
                      {CONFIDENCE_LABELS[r.confidence_tier]}
                    </span>
                  </td>
                  <td className="prop-insights-table__notes">
                    <span className={`edge-category-badge edge-category-badge--${r.edge_category}`}>{EDGE_LABELS[r.edge_category]}</span>
                    {r.warnings.length > 0 && <span className="prop-insights-table__warning-icon" title={r.warnings.join(" ")}>⚠</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Disclaimer />
    </main>
  );
}

export default PropInsightsPage;
