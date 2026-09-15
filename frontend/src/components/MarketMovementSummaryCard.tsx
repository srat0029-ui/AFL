import type { SeriesSummary } from "../api/client";
import { formatPreciseDateTime } from "../lib/datetime";
import { hrs, pct, pctChange } from "../features/marketMovement";

function MarketMovementSummaryCard({ summary }: { summary: SeriesSummary | null }) {
  if (summary === null) {
    return (
      <div className="mme-card mme-card--muted">
        <h4>No series available</h4>
        <p className="hint">Nothing recorded for this identity.</p>
      </div>
    );
  }
  if (summary.n_observations === 0) {
    return (
      <div className="mme-card mme-card--muted">
        <h4>{summary.label}</h4>
        <p className="hint">No pre-kickoff observations recorded yet.</p>
      </div>
    );
  }
  return (
    <div className="mme-card">
      <h4>
        {summary.label}
        {summary.insufficient_history && <span className="mme-tag mme-tag--warning">Insufficient history</span>}
      </h4>
      <div className="mme-card__grid">
        <div>
          <span className="mme-card__label">First observed</span>
          <span className="mme-card__value">
            {pct(summary.first_observed?.probability)}
            {summary.first_observed?.price_decimal ? ` ($${summary.first_observed.price_decimal.toFixed(2)})` : ""}
          </span>
          <span className="mme-card__meta">
            {summary.first_observed && formatPreciseDateTime(summary.first_observed.recorded_at)} ({summary.first_observed && hrs(summary.first_observed.hours_to_kickoff)} before kickoff)
            {summary.first_observed?.bookmaker_name ? ` · ${summary.first_observed.bookmaker_name}` : ""}
          </span>
        </div>
        <div>
          <span className="mme-card__label">Latest observed pre-kickoff</span>
          <span className="mme-card__value">
            {pct(summary.latest_observed_pre_kickoff?.probability)}
            {summary.latest_observed_pre_kickoff?.price_decimal ? ` ($${summary.latest_observed_pre_kickoff.price_decimal.toFixed(2)})` : ""}
          </span>
          <span className="mme-card__meta">
            {summary.latest_observed_pre_kickoff && formatPreciseDateTime(summary.latest_observed_pre_kickoff.recorded_at)} (
            {summary.latest_observed_pre_kickoff && hrs(summary.latest_observed_pre_kickoff.hours_to_kickoff)} before kickoff)
            {summary.latest_observed_pre_kickoff?.bookmaker_name ? ` · ${summary.latest_observed_pre_kickoff.bookmaker_name}` : ""}
          </span>
        </div>
        {!summary.insufficient_history && (
          <>
            <div>
              <span className="mme-card__label">Total movement</span>
              <span className={`mme-card__value ${(summary.total_probability_change ?? 0) >= 0 ? "mme-pos" : "mme-neg"}`}>{pctChange(summary.total_probability_change)}</span>
            </div>
            <div>
              <span className="mme-card__label">Largest single move</span>
              {summary.largest_single_movement ? (
                <>
                  <span className={`mme-card__value ${summary.largest_single_movement.absolute_change >= 0 ? "mme-pos" : "mme-neg"}`}>
                    {pctChange(summary.largest_single_movement.absolute_change)}
                  </span>
                  <span className="mme-card__meta">{hrs(summary.largest_single_movement.hours_to_kickoff)} before kickoff</span>
                </>
              ) : (
                <span className="mme-card__value">—</span>
              )}
            </div>
          </>
        )}
        <div>
          <span className="mme-card__label">Observations</span>
          <span className="mme-card__value">{summary.n_observations}</span>
        </div>
      </div>
    </div>
  );
}

export default MarketMovementSummaryCard;
