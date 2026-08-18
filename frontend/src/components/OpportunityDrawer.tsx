import type { BookmakerQuote, CalibrationMetrics, OpportunityAlternateLine, RecentForm } from "../api/client";
import { formatCompactDateTime } from "../lib/datetime";
import RecentFormChart from "./RecentFormChart";
import "./OpportunityDrawer.css";

interface OpportunityDrawerProps {
  bookmakers: BookmakerQuote[];
  bestBookmaker: string;
  whyModelLikesIt: string;
  calibration: CalibrationMetrics | null;
  warnings: string[];
  // Present when the caller has full opportunity context (diversified
  // views) — the drawer renders the richer Model/Market/Alternate Lines
  // layout (Section 12) when these are supplied, and falls back to the
  // simpler bookmaker-comparison-only layout when they're omitted (the
  // raw "All Markets" table, which doesn't compute family/recent-form data).
  modelProbability?: number;
  modelFairOdds?: number;
  confidenceTier?: string;
  threshold?: number | null;
  lineType?: string | null;
  alternateLines?: OpportunityAlternateLine[];
  correlationLabels?: string[];
  reasonLabels?: string[];
  priceAdvantagePct?: number | null;
  recentForm?: RecentForm | null;
}

const CONFIDENCE_LABELS: Record<string, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

/** Sections 12-16 of the Weekly Opportunity Discovery stage in one
 * component: Model | Market | Alternate Lines, recent-form context (a
 * chart plus a descriptive hit-rate sentence — never presented as proof
 * of anything), and any model-vs-recent-form disagreement flags. Also
 * doubles as the plain bookmaker-comparison drawer (Sections 14/15 of the
 * earlier best-bets stage) when the richer props aren't supplied. */
function OpportunityDrawer({
  bookmakers,
  bestBookmaker,
  whyModelLikesIt,
  calibration,
  warnings,
  modelProbability,
  modelFairOdds,
  confidenceTier,
  threshold,
  lineType,
  alternateLines,
  correlationLabels,
  reasonLabels,
  priceAdvantagePct,
  recentForm,
}: OpportunityDrawerProps) {
  const sorted = [...bookmakers].sort((a, b) => b.price_decimal - a.price_decimal);
  const hasModelSection = modelProbability !== undefined;

  return (
    <div className="opportunity-drawer">
      <p className="opportunity-drawer__why">{whyModelLikesIt}</p>

      {(correlationLabels?.length || reasonLabels?.length) && (
        <div className="opportunity-drawer__badges">
          {correlationLabels?.map((label, i) => (
            <span key={`c-${i}`} className="opportunity-drawer__badge opportunity-drawer__badge--correlation">
              {label}
            </span>
          ))}
          {reasonLabels?.map((label, i) => (
            <span key={`r-${i}`} className="opportunity-drawer__badge opportunity-drawer__badge--reason">
              {label}
            </span>
          ))}
        </div>
      )}

      {hasModelSection && (
        <div className="opportunity-drawer__grid-section">
          <p className="opportunity-drawer__section-title">Model</p>
          <div className="opportunity-drawer__stat-grid">
            <div>
              <span className="opportunity-drawer__stat-label">Probability</span>
              <span className="opportunity-drawer__stat-value">{((modelProbability ?? 0) * 100).toFixed(1)}%</span>
            </div>
            {modelFairOdds !== undefined && (
              <div>
                <span className="opportunity-drawer__stat-label">Fair odds</span>
                <span className="opportunity-drawer__stat-value">${modelFairOdds.toFixed(2)}</span>
              </div>
            )}
            {confidenceTier !== undefined && (
              <div>
                <span className="opportunity-drawer__stat-label">Confidence</span>
                <span className="opportunity-drawer__stat-value">{CONFIDENCE_LABELS[confidenceTier] ?? confidenceTier}</span>
              </div>
            )}
            {calibration && (
              <div>
                <span className="opportunity-drawer__stat-label">Calibration (ECE)</span>
                <span className="opportunity-drawer__stat-value">{calibration.ece.toFixed(3)}</span>
              </div>
            )}
          </div>
        </div>
      )}

      <p className="opportunity-drawer__section-title">Market{priceAdvantagePct != null ? " — price shopping" : ""}</p>
      {priceAdvantagePct != null && (
        <p className="hint opportunity-drawer__price-advantage">
          Best-price advantage: +{priceAdvantagePct.toFixed(1)}% vs next-best bookmaker
        </p>
      )}
      <div className="opportunity-drawer__book-table-scroll">
        <table className="opportunity-drawer__book-table">
          <thead>
            <tr>
              <th>Bookmaker</th>
              <th>Odds</th>
              <th>Implied prob.</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((b) => (
              <tr
                key={b.bookmaker_name}
                className={b.bookmaker_name === bestBookmaker ? "opportunity-drawer__book-row opportunity-drawer__book-row--best" : "opportunity-drawer__book-row"}
              >
                <td>
                  {b.bookmaker_name}
                  {b.bookmaker_name === bestBookmaker && <span className="opportunity-drawer__best-flag">Best</span>}
                </td>
                <td>${b.price_decimal.toFixed(2)}</td>
                <td>{((1 / b.price_decimal) * 100).toFixed(1)}%</td>
                <td>
                  {formatCompactDateTime(b.recorded_at)}{" "}
                  <span className={`opportunity-drawer__freshness opportunity-drawer__freshness--${b.freshness}`}>{b.freshness}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {calibration && (
        <p className="hint opportunity-drawer__calibration">
          Historical calibration: this model's {calibration.evaluated_threshold}+ predictions had a calibration error (ECE) of{" "}
          {calibration.ece.toFixed(3)} across {calibration.n.toLocaleString()} evaluation games.
        </p>
      )}

      {alternateLines && alternateLines.length > 0 && (
        <>
          <p className="opportunity-drawer__section-title">Alternate lines ({alternateLines.length})</p>
          <div className="opportunity-drawer__book-table-scroll">
            <table className="opportunity-drawer__book-table">
              <thead>
                <tr>
                  <th>Line</th>
                  <th>Model prob.</th>
                  <th>Best odds</th>
                  <th>Book</th>
                  <th>Difference</th>
                  <th>Model-est. EV</th>
                </tr>
              </thead>
              <tbody>
                {alternateLines.map((alt) => (
                  <tr key={alt.label} className="opportunity-drawer__book-row">
                    <td>{alt.label}</td>
                    <td>{(alt.model_probability * 100).toFixed(1)}%</td>
                    <td>${alt.best_price.toFixed(2)}</td>
                    <td>{alt.best_bookmaker}</td>
                    <td className={alt.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                      {alt.difference_pp >= 0 ? "+" : ""}
                      {(alt.difference_pp * 100).toFixed(1)}pp
                    </td>
                    <td className={alt.expected_value >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                      {alt.expected_value >= 0 ? "+" : ""}
                      {alt.expected_value.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {recentForm && (
        <>
          <p className="opportunity-drawer__section-title">Recent form</p>
          <div className="opportunity-drawer__recent-form">
            <RecentFormChart
              values={recentForm.last10}
              threshold={threshold ?? null}
              predictedMean={recentForm.predicted_mean}
              lineType={lineType ?? null}
            />
            <div className="opportunity-drawer__recent-form-text">
              <p className="hint">{recentForm.hit_rate_description}</p>
              <p className="hint">
                Last 5 avg: {recentForm.last5_avg?.toFixed(1) ?? "—"} · Last 10 avg: {recentForm.last10_avg?.toFixed(1) ?? "—"}
              </p>
              {recentForm.form_disagreement_label && (
                <p className="opportunity-drawer__disagreement">{recentForm.form_disagreement_label}</p>
              )}
              {recentForm.conservative_model_flag && (
                <p className="opportunity-drawer__disagreement">{recentForm.conservative_model_flag}</p>
              )}
            </div>
          </div>
        </>
      )}

      {warnings.length > 0 && (
        <ul className="opportunity-drawer__warnings">
          {warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default OpportunityDrawer;
