import { Fragment, useEffect, useState } from "react";
import {
  fetchDiversifiedOpportunities,
  type DiversifiedOpportunitiesResponse,
  type DiversifiedOpportunity,
  type OpportunityView,
} from "../api/client";
import OpportunityDrawer from "./OpportunityDrawer";
import "./DiversifiedOpportunitiesView.css";

const CONFIDENCE_LABELS: Record<string, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

const FRESHNESS_LABELS: Record<string, string> = { fresh: "Fresh", aging: "Aging", stale: "Stale" };

function opportunityKey(o: DiversifiedOpportunity): string {
  return `${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.market_type}-${o.threshold ?? o.line_value}`;
}

interface DiversifiedOpportunitiesViewProps {
  view: OpportunityView;
}

/** Sections 1-11 of the Weekly Opportunity Discovery stage: the curated,
 * diversified "Best Opportunities" experience. Distinct from the raw "All
 * Markets" ranking (still fully available elsewhere) — this groups
 * correlated alternate lines into families and shows one representative
 * headline per family, so a single hot player's alternate thresholds can
 * never fill the whole list. */
function DiversifiedOpportunitiesView({ view }: DiversifiedOpportunitiesViewProps) {
  const [marketScope, setMarketScope] = useState<"all" | "player" | "team">("all");
  const [topN, setTopN] = useState<10 | 25 | 0>(10);
  const [includeUncertain, setIncludeUncertain] = useState(false);
  const [includeStale, setIncludeStale] = useState(false);
  const [includeInsufficientHistory, setIncludeInsufficientHistory] = useState(false);
  const [onePerMatch, setOnePerMatch] = useState(false);
  const [onePerPlayer, setOnePerPlayer] = useState(view !== "overall");
  const [data, setData] = useState<DiversifiedOpportunitiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  // Section 7: "Best Disposals"/"Best Goals" default to one representative
  // line per player; "Best Overall" relies on the max-2-per-player rule
  // instead. Reset when switching views so a leftover toggle from one
  // view doesn't silently carry into another.
  useEffect(() => {
    setOnePerPlayer(view !== "overall");
    setOnePerMatch(false);
  }, [view]);

  useEffect(() => {
    setLoading(true);
    fetchDiversifiedOpportunities({
      view,
      marketScope,
      includeUncertain,
      includeStale,
      includeInsufficientHistory,
      onePerMatch,
      onePerPlayer,
      limit: topN === 0 ? null : topN,
    })
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load opportunities"))
      .finally(() => setLoading(false));
  }, [view, marketScope, topN, includeUncertain, includeStale, includeInsufficientHistory, onePerMatch, onePerPlayer]);

  const summary = data?.summary;

  return (
    <div className="diversified-view">
      {summary && (
        <section className="diversified-view__summary">
          {summary.round_number !== null && <span className="diversified-view__summary-item">Round {summary.round_number}</span>}
          <span className="diversified-view__summary-item">{summary.n_opportunities_passing_gates} opportunities pass current quality gates</span>
          <span className="diversified-view__summary-item">{summary.n_unique_players} unique players</span>
          <span className="diversified-view__summary-item">{summary.n_unique_matches} matches</span>
          <span className="diversified-view__summary-item">{summary.n_bookmakers} bookmakers</span>
          {summary.best_difference_pp !== null && (
            <span className="diversified-view__summary-item">Best model-market difference: +{(summary.best_difference_pp * 100).toFixed(1)}pp</span>
          )}
          {summary.best_price_advantage_pct !== null && (
            <span className="diversified-view__summary-item">Best price-shopping improvement: +{summary.best_price_advantage_pct.toFixed(1)}%</span>
          )}
        </section>
      )}

      {data && data.bookmaker_coverage.length > 0 && (
        <section className="diversified-view__coverage">
          <span className="diversified-view__coverage-label">Bookmaker coverage:</span>
          {data.bookmaker_coverage.map((c) => (
            <span key={c.bookmaker_name} className="diversified-view__coverage-item">
              {c.bookmaker_name}: {c.n_active_player_markets} markets across {c.n_matches_covered} match{c.n_matches_covered === 1 ? "" : "es"}
            </span>
          ))}
        </section>
      )}

      <section className="prop-insights-page__filters">
        {view === "overall" && (
          <label>
            Market
            <select value={marketScope} onChange={(e) => setMarketScope(e.target.value as "all" | "player" | "team")}>
              <option value="all">All (player + team)</option>
              <option value="player">Player only</option>
              <option value="team">Team only</option>
            </select>
          </label>
        )}
        <label>
          Show
          <select value={topN} onChange={(e) => setTopN(Number(e.target.value) as 10 | 25 | 0)}>
            <option value={10}>Top 10</option>
            <option value={25}>Top 25</option>
            <option value={0}>All</option>
          </select>
        </label>
        <label className="prop-insights-page__checkbox">
          <input type="checkbox" checked={includeUncertain} onChange={(e) => setIncludeUncertain(e.target.checked)} />
          Include unconfirmed participation
        </label>
        <label className="prop-insights-page__checkbox">
          <input type="checkbox" checked={includeStale} onChange={(e) => setIncludeStale(e.target.checked)} />
          Include stale odds
        </label>
        <label className="prop-insights-page__checkbox">
          <input type="checkbox" checked={includeInsufficientHistory} onChange={(e) => setIncludeInsufficientHistory(e.target.checked)} />
          Include insufficient-history markets
        </label>
        <label className="prop-insights-page__checkbox">
          <input type="checkbox" checked={onePerMatch} onChange={(e) => setOnePerMatch(e.target.checked)} />
          One opportunity per match
        </label>
        <label className="prop-insights-page__checkbox">
          <input type="checkbox" checked={onePerPlayer} onChange={(e) => setOnePerPlayer(e.target.checked)} />
          One opportunity per player
        </label>
      </section>

      {loading && <p className="hint">Loading…</p>}
      {error && <div className="prop-insights-page__error">{error}</div>}

      {!loading && !error && data && data.opportunities.length === 0 && (
        <p className="hint">
          No opportunities currently pass the quality gates for this round — try including uncertain participation,
          stale odds, or insufficient-history markets above, or check back once bookmaker markets open.
        </p>
      )}

      {!loading && !error && data && data.opportunities.length > 0 && (
        <div className="prop-insights-table-scroll">
          <table className="prop-insights-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Type</th>
                <th>Opportunity</th>
                <th>Model prob.</th>
                <th>Best price</th>
                <th>Price adv.</th>
                <th>Books</th>
                <th>Market prob.</th>
                <th>Edge</th>
                <th>Model-est. EV</th>
                <th>Confidence</th>
                <th>Freshness</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>
              {data.opportunities.map((o, i) => {
                const key = opportunityKey(o);
                const expanded = expandedKey === key;
                return (
                  <Fragment key={key}>
                    <tr className="prop-insights-table__row-clickable" onClick={() => setExpandedKey(expanded ? null : key)}>
                      <td>{i + 1}</td>
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
                        {o.alternate_lines.length > 0 && (
                          <span className="diversified-view__alt-count" title={`${o.alternate_lines.length} alternate line(s) in this family`}>
                            +{o.alternate_lines.length} alt
                          </span>
                        )}
                        {o.correlation_labels.length > 0 && (
                          <div className="diversified-view__correlation-labels">
                            {o.correlation_labels.map((label, ci) => (
                              <span key={ci} className="diversified-view__correlation-badge">
                                {label}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td>{(o.model_probability * 100).toFixed(1)}%</td>
                      <td title={o.quote_source === "manual" ? "Manual entry — not a current live price" : undefined}>
                        ${o.best_price.toFixed(2)}
                        <br />
                        <span className="hint">{o.best_bookmaker}</span>
                      </td>
                      <td>{o.price_advantage_pct != null ? `+${o.price_advantage_pct.toFixed(1)}%` : "—"}</td>
                      <td>{o.n_bookmakers === 1 ? "1 (only)" : o.n_bookmakers}</td>
                      <td title={o.overround_removed ? "No-vig market probability — bookmaker margin removed" : "Raw implied probability — bookmaker margin NOT removed, only one side of the market was quoted"}>
                        {((o.devigged_probability ?? o.market_implied_probability) * 100).toFixed(1)}%
                        {!o.overround_removed && <span className="prop-insights-table__raw-flag">raw implied</span>}
                      </td>
                      <td className={o.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                        {o.difference_pp >= 0 ? "+" : ""}
                        {(o.difference_pp * 100).toFixed(1)}pp
                      </td>
                      <td className={o.expected_value >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                        {o.expected_value >= 0 ? "+" : ""}
                        {o.expected_value.toFixed(2)}
                      </td>
                      <td>
                        <span className={`confidence-badge confidence-badge--${o.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
                          {CONFIDENCE_LABELS[o.confidence_tier]}
                        </span>
                      </td>
                      <td>
                        <span className={`prop-insights-table__freshness prop-insights-table__freshness--${o.odds_freshness}`}>
                          {FRESHNESS_LABELS[o.odds_freshness]}
                        </span>
                      </td>
                      <td
                        title={`difference ${o.opportunity_components.difference.toFixed(1)} + EV ${o.opportunity_components.expected_value.toFixed(1)} + confidence ${o.opportunity_components.confidence.toFixed(1)} + freshness ${o.opportunity_components.freshness.toFixed(1)} + lineup ${o.opportunity_components.lineup.toFixed(1)} + calibration ${o.opportunity_components.calibration.toFixed(1)}, x${o.opportunity_components.penalty_multiplier.toFixed(2)} (${o.opportunity_components.penalty_reasons.join(", ") || "no penalty"})`}
                      >
                        {o.opportunity_score.toFixed(1)}
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="prop-insights-table__drawer-row">
                        <td colSpan={13}>
                          <OpportunityDrawer
                            bookmakers={o.bookmakers}
                            bestBookmaker={o.best_bookmaker}
                            whyModelLikesIt={o.why_model_likes_it}
                            calibration={o.calibration}
                            warnings={o.warnings}
                            modelProbability={o.model_probability}
                            modelFairOdds={o.model_fair_odds}
                            confidenceTier={o.confidence_tier}
                            threshold={o.threshold}
                            lineType={o.line_type}
                            alternateLines={o.alternate_lines}
                            correlationLabels={o.correlation_labels}
                            reasonLabels={o.reason_labels}
                            priceAdvantagePct={o.price_advantage_pct}
                            recentForm={o.recent_form}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default DiversifiedOpportunitiesView;
