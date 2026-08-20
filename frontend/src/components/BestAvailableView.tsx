import { useEffect, useState } from "react";
import { fetchOpportunityTiers, type BestOpportunity, type DiversifiedOpportunity, type OpportunityTiersResponse } from "../api/client";
import "./BestAvailableView.css";

const CONFIDENCE_LABELS: Record<string, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

const EXCLUSION_LABELS: Record<string, string> = {
  stale_odds: "stale odds",
  insufficient_history: "insufficient history",
  confirmed_out: "player confirmed out",
  no_eligible_price: "no eligible bookmaker price",
  integrity_failure: "price-integrity failure",
};

function opportunityKey(o: BestOpportunity): string {
  return `${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.market_type}-${o.threshold ?? o.line_value}`;
}

function diffClass(o: BestOpportunity): string {
  if (o.edge_category === "no_meaningful_difference") return "prop-insights-table__diff-neutral";
  return o.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg";
}

function OpportunityRow({ o, caveats }: { o: BestOpportunity; caveats?: string[] }) {
  return (
    <tr>
      <td>
        <span className={`prop-insights-table__type-badge prop-insights-table__type-badge--${o.opportunity_type}`}>
          {o.opportunity_type === "player" ? "Player" : "Team"}
        </span>
      </td>
      <td>
        {o.label}
        {o.opportunity_type === "player" && !o.is_confirmed && (
          <span className="prop-insights-table__unconfirmed-flag" title="Participation not confirmed">
            unconfirmed
          </span>
        )}
        {caveats && caveats.length > 0 && (
          <div className="best-available__caveats">
            {caveats.map((c, i) => (
              <span key={i} className="best-available__caveat">
                {c}
              </span>
            ))}
          </div>
        )}
      </td>
      <td>{(o.model_probability * 100).toFixed(1)}%</td>
      <td>
        ${o.best_price.toFixed(2)} <span className="hint">{o.best_bookmaker}</span>
      </td>
      <td className={diffClass(o)}>
        {o.difference_pp >= 0 ? "+" : ""}
        {(o.difference_pp * 100).toFixed(1)}pp
      </td>
      <td>
        <span className={`confidence-badge confidence-badge--${o.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
          {CONFIDENCE_LABELS[o.confidence_tier] ?? o.confidence_tier}
        </span>
      </td>
      <td>{o.opportunity_score.toFixed(1)}</td>
    </tr>
  );
}

function OpportunityTable({ opportunities, showCaveats }: { opportunities: (BestOpportunity | DiversifiedOpportunity)[]; showCaveats?: boolean }) {
  return (
    <div className="prop-insights-table-scroll">
      <table className="prop-insights-table">
        <thead>
          <tr>
            <th>Type</th>
            <th>Opportunity</th>
            <th>Model prob.</th>
            <th>Best price</th>
            <th>Difference</th>
            <th>Confidence</th>
            <th>Score</th>
          </tr>
        </thead>
        <tbody>
          {opportunities.map((o) => (
            <OpportunityRow key={opportunityKey(o)} o={o} caveats={showCaveats ? (o.quality_tier?.caveats ?? []) : undefined} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Product-quality stage: the practical DEFAULT Prop Insights view. Best
 * Opportunities / Worth Reviewing / All Available all reuse the exact same
 * opportunity_score and quality_tier already computed elsewhere — this view
 * only changes which bucket a market lands in, never a probability. If
 * Best Opportunities is empty, the strongest Worth Reviewing entries are
 * shown in its place rather than an empty screen. */
function BestAvailableView() {
  const [data, setData] = useState<OpportunityTiersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAllAvailable, setShowAllAvailable] = useState(false);
  const [showWhy, setShowWhy] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetchOpportunityTiers()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load opportunities"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="loading-state">Loading…</p>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return null;

  const bestIsEmpty = data.best.length === 0;
  const showFallback = bestIsEmpty && data.fallback_message !== null;

  return (
    <div className="best-available">
      <p className="hint">
        {data.n_candidates} valid opportunit{data.n_candidates === 1 ? "y" : "ies"} pass hard integrity checks this
        round · {data.n_hard_excluded} excluded (see below).
      </p>

      <section className="best-available__section">
        <h2>Best Opportunities</h2>
        <p className="hint">Confirmed lineup, fresh odds, sufficient history, meaningful model-market difference.</p>
        {showFallback && <p className="empty-state">{data.fallback_message}</p>}
        {!showFallback && bestIsEmpty && <p className="empty-state">No opportunities currently meet the Best Opportunities bar.</p>}
        {!bestIsEmpty && <OpportunityTable opportunities={data.best} />}
        {showFallback && <OpportunityTable opportunities={data.worth_reviewing} showCaveats />}
      </section>

      {!showFallback && (
        <section className="best-available__section">
          <h2>Worth Reviewing</h2>
          <p className="hint">A positive model-market difference with a caveat shown below — not hidden, just not headline-ready yet.</p>
          {data.worth_reviewing.length === 0 ? (
            <p className="empty-state">Nothing currently in this tier.</p>
          ) : (
            <OpportunityTable opportunities={data.worth_reviewing} showCaveats />
          )}
        </section>
      )}

      <section className="best-available__section">
        <button type="button" className="btn" onClick={() => setShowAllAvailable((v) => !v)}>
          {showAllAvailable ? "Hide" : "Show"} All Available Opportunities ({data.all_available.length})
        </button>
        {showAllAvailable && (
          <>
            <p className="hint">Every valid current market, ranked by score — includes negative/neutral differences.</p>
            <OpportunityTable opportunities={data.all_available} />
          </>
        )}
      </section>

      <section className="best-available__section">
        <button type="button" className="btn" onClick={() => setShowWhy((v) => !v)}>
          {showWhy ? "Hide" : "Why aren't more markets shown?"}
        </button>
        {showWhy && (
          <ul className="best-available__breakdown">
            {Object.entries(data.exclusion_breakdown).map(([reason, count]) => (
              <li key={reason}>
                {count} {EXCLUSION_LABELS[reason] ?? reason.replace(/_/g, " ")}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default BestAvailableView;
