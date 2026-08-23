import { useEffect, useState } from "react";
import { fetchFinalShortlist, type FinalShortlistOpportunity, type FinalShortlistResponse, type QualityTierName } from "../api/client";
import OpportunityDrawer from "./OpportunityDrawer";
import "./FinalShortlistView.css";

const QUALITY_TIER_LABELS: Record<QualityTierName, string> = {
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

function opportunityKey(o: FinalShortlistOpportunity): string {
  return `${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.market_type}-${o.threshold ?? o.line_value}`;
}

/** Market Integrity + Final Weekly Picks stage, Sections 7-11, 21-22: the
 * Final Weekly Shortlist — more selective than Best Opportunities. Only
 * quality-tier strong_candidate/worth_reviewing opportunities appear, at
 * most one representative per strongly-correlated team-market group, and
 * a chosen "Top N" is a MAXIMUM never a manufactured target — the list can
 * legitimately be shorter than N, or empty. */
function FinalShortlistView() {
  const [limit, setLimit] = useState<5 | 10 | 20>(10);
  const [includeUnconfirmedPlayers, setIncludeUnconfirmedPlayers] = useState(false);
  const [data, setData] = useState<FinalShortlistResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [showExcluded, setShowExcluded] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetchFinalShortlist({ limit, includeUnconfirmedPlayers })
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load Final Shortlist"))
      .finally(() => setLoading(false));
  }, [limit, includeUnconfirmedPlayers]);

  return (
    <div className="final-shortlist">
      <p className="hint final-shortlist__intro">
        The most selective view this app offers: distinct model opinions currently strong enough to act on, with correlated
        markets (like a team's H2H and line) collapsed to one representative. "Top {limit}" is a maximum, never a target — if
        fewer opportunities genuinely qualify, fewer are shown.
      </p>

      <section className="prop-insights-page__filters">
        <label>
          Maximum
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value) as 5 | 10 | 20)}>
            <option value={5}>Top 5</option>
            <option value={10}>Top 10</option>
            <option value={20}>Top 20</option>
          </select>
        </label>
        <label className="prop-insights-page__checkbox">
          <input
            type="checkbox"
            checked={includeUnconfirmedPlayers}
            onChange={(e) => setIncludeUnconfirmedPlayers(e.target.checked)}
          />
          Include unconfirmed player opportunities
        </label>
      </section>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="prop-insights-page__error">{error}</div>}

      {!loading && !error && data && data.opportunities.length === 0 && (
        <p className="hint final-shortlist__empty">{data.empty_state_reason}</p>
      )}

      {!loading && !error && data && data.opportunities.length > 0 && (
        <div className="final-shortlist__cards">
          {data.opportunities.map((o, i) => {
            const key = opportunityKey(o);
            const expanded = expandedKey === key;
            return (
              <div key={key} className="final-shortlist__card">
                <div className="final-shortlist__card-header" onClick={() => setExpandedKey(expanded ? null : key)}>
                  <span className="final-shortlist__rank">#{i + 1}</span>
                  <div className="final-shortlist__card-title">
                    <span className="final-shortlist__label">{o.label}</span>
                    {o.quality_tier && (
                      <span className={`final-shortlist__tier-badge final-shortlist__tier-badge--${o.quality_tier.tier}`}>
                        {QUALITY_TIER_LABELS[o.quality_tier.tier]}
                      </span>
                    )}
                  </div>
                  <span className="final-shortlist__price">
                    ${o.best_price.toFixed(2)} <span className="hint">{o.best_bookmaker}</span>
                  </span>
                </div>

                <div className="final-shortlist__card-meta">
                  <span>Model {(o.model_probability * 100).toFixed(1)}%</span>
                  <span className={o.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                    {o.difference_pp >= 0 ? "+" : ""}
                    {(o.difference_pp * 100).toFixed(1)}pp
                  </span>
                  <span>{o.n_bookmakers} book{o.n_bookmakers === 1 ? "" : "s"}</span>
                  {o.market_maturity && <span>{MATURITY_LABELS[o.market_maturity.tier]}</span>}
                  {o.opportunity_type === "player" && (
                    <span className={o.is_confirmed ? "final-shortlist__confirmed" : "final-shortlist__unconfirmed"}>
                      {o.is_confirmed ? "Confirmed" : "Unconfirmed"}
                    </span>
                  )}
                </div>

                {o.why_it_ranks_here.length > 0 && (
                  <ul className="final-shortlist__reasons">
                    {o.why_it_ranks_here.map((r, ri) => (
                      <li key={ri}>{r}</li>
                    ))}
                  </ul>
                )}

                {o.caveats.length > 0 && (
                  <div className="final-shortlist__caveats">
                    {o.caveats.map((c, ci) => (
                      <span key={ci} className="final-shortlist__caveat-badge">
                        {c}
                      </span>
                    ))}
                  </div>
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

                {expanded && (
                  <div className="final-shortlist__drawer">
                    <OpportunityDrawer
                      bookmakers={o.bookmakers}
                      bestBookmaker={o.best_bookmaker}
                      whyModelLikesIt={o.why_model_likes_it}
                      calibration={null}
                      warnings={o.warnings}
                      modelProbability={o.model_probability}
                      modelFairOdds={o.model_fair_odds}
                      confidenceTier={o.confidence_tier}
                      threshold={o.threshold}
                      lineType={o.line_type}
                      alternateLines={o.alternate_lines}
                      correlationLabels={[]}
                      reasonLabels={[]}
                      priceAdvantagePct={null}
                      recentForm={null}
                      addBetSnapshot={{
                        matchId: o.match_id,
                        opportunityType: o.opportunity_type,
                        label: o.label,
                        selection: o.selection ?? (o.opportunity_type === "player" ? "over" : ""),
                        marketType: o.market_type,
                        bookmaker: o.best_bookmaker,
                        oddsTaken: o.best_price,
                        modelProbability: o.model_probability,
                        modelFairOdds: o.model_fair_odds,
                        confidenceTier: o.confidence_tier,
                        sourceMode: "final_shortlist",
                        playerId: o.player_id,
                        lineType: o.line_type,
                        threshold: o.threshold,
                        lineValue: o.line_value,
                        lineupStatus: o.selection_status,
                      }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {data && data.excluded.length > 0 && (
        <section className="final-shortlist__excluded">
          <button className="final-shortlist__excluded-toggle" onClick={() => setShowExcluded((s) => !s)}>
            {showExcluded ? "Hide" : "Show"} {data.excluded.length} opportunities considered but not shortlisted
          </button>
          {showExcluded && (
            <ul className="final-shortlist__excluded-list">
              {data.excluded.map((e, i) => (
                <li key={i}>
                  <strong>{e.label}</strong> — {e.reason}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

export default FinalShortlistView;
