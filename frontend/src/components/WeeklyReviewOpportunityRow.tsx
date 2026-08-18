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

/** One compact row shared by every Weekly Review hierarchy section — a
 * checkbox adds/removes the opportunity from the side-by-side comparison
 * tray (Section 2) without needing to open a drawer. */
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
  return (
    <div className="weekly-review-row">
      <label className="weekly-review-row__checkbox">
        <input type="checkbox" checked={selected} onChange={() => onToggle(key)} />
      </label>
      <div className="weekly-review-row__body">
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
          <span>{opportunity.direction_agreement.classification === "agrees_on_direction" ? "Agrees on direction" : "Disagrees on direction"}</span>
          {opportunity.evidence_summary.caution_labels.length > 0 && (
            <span className="weekly-review-row__caution">{opportunity.evidence_summary.caution_labels.length} caution flag(s)</span>
          )}
        </div>
      </div>
    </div>
  );
}

export default WeeklyReviewOpportunityRow;
