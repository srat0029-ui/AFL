import { useState } from "react";
import type { WeeklyReviewOpportunity } from "../api/client";

const QUALITY_TIER_LABELS: Record<string, string> = {
  strong_candidate: "Strong candidate",
  worth_reviewing: "Worth reviewing",
  speculative: "Speculative",
  do_not_headline: "Do not headline",
};

export function opportunityKey(o: WeeklyReviewOpportunity): string {
  return `${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.market_type}-${o.threshold ?? o.line_value}`;
}

// Deeper evidence for one opportunity — kept out of the row itself (Section 4:
// "reduce nested cards and repeated explanatory text") and only rendered
// once a row is expanded. Every field here already comes back on the
// opportunity payload; nothing new is fetched.
function EvidenceDetail({ opportunity }: { opportunity: WeeklyReviewOpportunity }) {
  const { evidence_summary, why_it_ranks_here, caveats, direction_agreement, market_movement, consensus, outlier_check, context_conflict } = opportunity;
  return (
    <div className="weekly-review-row__evidence">
      <div className="weekly-review-row__evidence-col">
        <span className="weekly-review-row__evidence-label">Market probability</span>
        <span>{(opportunity.market_implied_probability * 100).toFixed(1)}%</span>
        {consensus && (
          <span className="hint">
            Consensus {(consensus.consensus_probability * 100).toFixed(1)}% across {consensus.n_bookmakers} bookmaker(s)
          </span>
        )}
        <span>{direction_agreement.description}</span>
        {market_movement && <span className="hint">{market_movement.description}</span>}
        {outlier_check?.is_outlier && <span className="weekly-review-row__caution">{outlier_check.message}</span>}
      </div>
      {(why_it_ranks_here.length > 0 || evidence_summary.evidence_labels.length > 0) && (
        <div className="weekly-review-row__evidence-col">
          <span className="weekly-review-row__evidence-label">Why it ranks here</span>
          <ul>
            {[...why_it_ranks_here, ...evidence_summary.evidence_labels].map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}
      {(caveats.length > 0 || evidence_summary.caution_labels.length > 0 || (context_conflict?.labels.length ?? 0) > 0) && (
        <div className="weekly-review-row__evidence-col">
          <span className="weekly-review-row__evidence-label">Caveats</span>
          <ul>
            {[...caveats, ...evidence_summary.caution_labels].map((line, i) => (
              <li key={i} className="weekly-review-row__caution">
                {line}
              </li>
            ))}
            {context_conflict?.labels.map((line, i) => (
              <li key={`ctx-${i}`} className="weekly-review-row__caution">
                {line}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** One compact row shared by every Weekly Review hierarchy section — a
 * checkbox adds/removes the opportunity from the side-by-side comparison
 * tray (Section 2) without needing to open a drawer; clicking the row body
 * itself expands the deeper evidence in place (Section 4). */
function WeeklyReviewOpportunityRow({
  opportunity,
  selected,
  onToggle,
}: {
  opportunity: WeeklyReviewOpportunity;
  selected: boolean;
  onToggle: (key: string) => void;
}) {
  const key = opportunityKey(opportunity);
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="weekly-review-row">
      <label className="weekly-review-row__checkbox" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" checked={selected} onChange={() => onToggle(key)} />
      </label>
      <div className="weekly-review-row__body" onClick={() => setExpanded((v) => !v)} role="button" tabIndex={0}>
        <div className="weekly-review-row__header">
          <span className="weekly-review-row__label">{opportunity.label}</span>
          {opportunity.quality_tier && (
            <span className={`final-shortlist__tier-badge final-shortlist__tier-badge--${opportunity.quality_tier.tier}`}>
              {QUALITY_TIER_LABELS[opportunity.quality_tier.tier]}
            </span>
          )}
          <span className="weekly-review-row__price">
            ${opportunity.best_price.toFixed(2)} <span className="hint">{opportunity.best_bookmaker}</span>
          </span>
        </div>
        <div className="weekly-review-row__meta">
          <span>Model {(opportunity.model_probability * 100).toFixed(1)}%</span>
          <span className={opportunity.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
            {opportunity.difference_pp >= 0 ? "+" : ""}
            {(opportunity.difference_pp * 100).toFixed(1)}pp
          </span>
          <span className={`confidence-badge confidence-badge--${opportunity.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
            {opportunity.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient data")}
          </span>
          {opportunity.evidence_summary.caution_labels.length > 0 && (
            <span className="weekly-review-row__caution">{opportunity.evidence_summary.caution_labels.length} caution flag(s)</span>
          )}
          {opportunity.model_risk_flags.length > 0 && (
            <span className="weekly-review-row__caution" title={opportunity.model_risk_flags.map((f) => f.description).join(" ")}>
              Recent usage change
            </span>
          )}
          <span className="weekly-review-row__expand-hint">{expanded ? "Hide evidence ▾" : "Evidence ▸"}</span>
        </div>
        {expanded && <EvidenceDetail opportunity={opportunity} />}
      </div>
    </div>
  );
}

export default WeeklyReviewOpportunityRow;
