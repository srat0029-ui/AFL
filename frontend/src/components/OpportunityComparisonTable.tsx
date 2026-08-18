import type { WeeklyReviewOpportunity } from "../api/client";
import "./OpportunityComparisonTable.css";

const QUALITY_TIER_LABELS: Record<string, string> = {
  strong_candidate: "Strong candidate",
  worth_reviewing: "Worth reviewing",
  speculative: "Speculative",
  do_not_headline: "Do not headline",
};

const MATURITY_LABELS: Record<string, string> = {
  early_market: "Early market",
  developing_market: "Developing market",
  mature_market: "Mature market",
};

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtPp(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}pp`;
}

/** Weekly Bet Review + Decision Support stage, Section 2: side-by-side
 * shortlist comparison — every opportunity as a column, every requested
 * field as a row, so several opportunities can be compared without
 * opening each drawer individually. */
function OpportunityComparisonTable({ opportunities, onRemove }: { opportunities: WeeklyReviewOpportunity[]; onRemove: (key: string) => void }) {
  if (opportunities.length === 0) return null;

  const keyOf = (o: WeeklyReviewOpportunity) => `${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.market_type}-${o.threshold ?? o.line_value}`;

  return (
    <div className="comparison-table-scroll">
      <table className="comparison-table">
        <thead>
          <tr>
            <th className="comparison-table__row-label">Field</th>
            {opportunities.map((o) => (
              <th key={keyOf(o)}>
                {o.label}
                <button className="comparison-table__remove" onClick={() => onRemove(keyOf(o))} title="Remove from comparison">
                  ×
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="comparison-table__row-label">Match</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>Match #{o.match_id}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Market</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.market_type}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Model probability</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{fmtPct(o.model_probability)}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Model fair odds</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>${o.model_fair_odds.toFixed(2)}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Best enabled bookmaker</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.best_bookmaker}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Best odds</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>${o.best_price.toFixed(2)}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Market probability</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)} title={o.overround_removed ? "No-vig" : "Raw implied — vig not removed"}>
                {fmtPct(o.overround_removed ? o.devigged_probability : o.market_implied_probability)}
                {!o.overround_removed && <span className="prop-insights-table__raw-flag">raw</span>}
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Difference</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)} className={o.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                {fmtPp(o.difference_pp)}
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Model-estimated EV</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)} className={o.expected_value >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                {o.expected_value >= 0 ? "+" : ""}
                {o.expected_value.toFixed(2)}
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Confidence</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.confidence_tier.replace("_confidence", "")}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Quality tier</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.quality_tier ? QUALITY_TIER_LABELS[o.quality_tier.tier] : "—"}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Market maturity</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.market_maturity ? MATURITY_LABELS[o.market_maturity.tier] : "—"}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Bookmaker count</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.n_bookmakers}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Last updated</td>
            {opportunities.map((o) => {
              const best = o.bookmakers.find((b) => b.bookmaker_name === o.best_bookmaker);
              return <td key={keyOf(o)}>{best ? new Date(best.recorded_at).toLocaleString() : "—"}</td>;
            })}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Team/lineup status</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>
                {o.opportunity_type === "player" ? (o.is_confirmed ? "Confirmed" : "Unconfirmed") : "Team market"}
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Correlation warnings</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.correlation_labels.length > 0 ? o.correlation_labels.join("; ") : "—"}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Model vs market direction</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.direction_agreement.description}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Projection vs line</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>
                {o.projection_line_distance
                  ? `${o.projection_line_distance.model_projection.toFixed(1)} vs ${o.projection_line_distance.line_value.toFixed(1)} (${
                      o.projection_line_distance.distance >= 0 ? "+" : ""
                    }${o.projection_line_distance.distance.toFixed(1)} ${o.projection_line_distance.unit})`
                  : "—"}
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Model fair price</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>${o.price_sensitivity.model_fair_price.toFixed(2)}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Market movement</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.market_movement ? o.market_movement.description : "—"}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Consensus probability</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>
                {o.consensus ? `${fmtPct(o.consensus.consensus_probability)} (n=${o.consensus.n_bookmakers}, spread ${(o.consensus.spread * 100).toFixed(1)}pp)` : "—"}
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Outlier check</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>{o.outlier_check?.message ?? "Not an outlier"}</td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Evidence supporting</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>
                <ul className="comparison-table__list">
                  {o.evidence_summary.evidence_labels.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Reasons for caution</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>
                <ul className="comparison-table__list comparison-table__list--caution">
                  {o.evidence_summary.caution_labels.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Current context</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>
                {o.current_context.length === 0 ? (
                  "—"
                ) : (
                  <ul className="comparison-table__list">
                    {o.current_context.map((c) => (
                      <li key={c.id}>
                        {c.context_type_label}
                        {c.player_name ? ` — ${c.player_name}` : ""}: {c.summary}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
            ))}
          </tr>
          <tr>
            <td className="comparison-table__row-label">Latest context / Model generated</td>
            {opportunities.map((o) => (
              <td key={keyOf(o)}>
                {o.context_conflict?.latest_context_at ? new Date(o.context_conflict.latest_context_at).toLocaleString() : "No context"}
                {" / "}
                {o.context_conflict?.model_generated_at ? new Date(o.context_conflict.model_generated_at).toLocaleString() : "Live"}
                {o.context_conflict?.codes.includes("MODEL_PREDATES_CONTEXT") && (
                  <div className="comparison-table__predates-warning">Model may not fully reflect latest context</div>
                )}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export default OpportunityComparisonTable;
