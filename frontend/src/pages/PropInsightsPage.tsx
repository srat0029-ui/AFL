import { Fragment, useEffect, useMemo, useState } from "react";
import "./PropInsightsPage.css";
import BestAvailableView from "../components/BestAvailableView";
import BookmakerSettingsPanel from "../components/BookmakerSettingsPanel";
import Disclaimer from "../components/Disclaimer";
import DiversifiedOpportunitiesView from "../components/DiversifiedOpportunitiesView";
import FinalShortlistView from "../components/FinalShortlistView";
import ModelMarketDisagreementsView from "../components/ModelMarketDisagreementsView";
import OpportunityDrawer from "../components/OpportunityDrawer";
import {
  fetchBestOpportunities,
  fetchNormalizedPropInsights,
  fetchPropInsights,
  type BestOpportunity,
  type ConfidenceTierLive,
  type EdgeCategory,
  type NormalizedPropInsight,
  type OddsFreshness,
  type PlayerPropMarketType,
  type PropInsight,
  type SelectionStatus,
} from "../api/client";

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

const FRESHNESS_LABELS: Record<OddsFreshness, string> = { fresh: "Fresh", aging: "Aging", stale: "Stale" };
const FRESHNESS_RANK: Record<OddsFreshness, number> = { fresh: 0, aging: 1, stale: 2 };

const LINEUP_LABELS: Partial<Record<SelectionStatus, string>> = {
  confirmed_selected: "Confirmed",
  substitute: "Substitute",
  named_in_squad: "Named in squad",
  emergency: "Emergency",
  placeholder: "Placeholder",
  uncertain: "Uncertain",
};

type Tab =
  | "best_available"
  | "final_shortlist"
  | "best_overall"
  | "best_disposals"
  | "best_goals"
  | "all_markets"
  | "disposals"
  | "goals"
  | "by_match"
  | "manual_log"
  | "disagreements"
  | "bookmaker_settings";

const TABS: { key: Tab; label: string }[] = [
  { key: "best_available", label: "Best Available" },
  { key: "final_shortlist", label: "Final Shortlist" },
  { key: "best_overall", label: "Best Overall" },
  { key: "best_disposals", label: "Best Disposals" },
  { key: "best_goals", label: "Best Goals" },
  { key: "all_markets", label: "All Markets" },
  { key: "disposals", label: "Disposals" },
  { key: "goals", label: "Goals" },
  { key: "by_match", label: "By Match" },
  { key: "manual_log", label: "Quote Log" },
  { key: "disagreements", label: "Model vs Market" },
  { key: "bookmaker_settings", label: "Bookmaker Settings" },
];

const DIVERSIFIED_TABS: Partial<Record<Tab, "overall" | "disposals" | "goals">> = {
  best_overall: "overall",
  best_disposals: "disposals",
  best_goals: "goals",
};

function marketLabel(marketType: PlayerPropMarketType, lineType: string, threshold: number): string {
  const market = marketType === "player_disposals" ? "Disposals" : "Goals";
  const line = lineType === "multi_plus" ? `${threshold.toFixed(1)}+` : `o/u ${threshold.toFixed(1)}`;
  return `${market} ${line}`;
}

type TopN = 10 | 25 | 0; // 0 = All

function PropInsightsPage() {
  const [tab, setTab] = useState<Tab>("best_available");
  const [rows, setRows] = useState<NormalizedPropInsight[]>([]);
  const [manualRows, setManualRows] = useState<PropInsight[]>([]);
  const [confidence, setConfidence] = useState<ConfidenceTierLive | "">("");
  const [includeUncertain, setIncludeUncertain] = useState(true);
  const [selectedMatchId, setSelectedMatchId] = useState<number | "">("");
  const [bookmakerFilter, setBookmakerFilter] = useState<string>("");
  const [maxFreshness, setMaxFreshness] = useState<OddsFreshness>("stale");
  const [minDifferencePp, setMinDifferencePp] = useState<string>("");
  const [minOdds, setMinOdds] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // "All Markets" tab state (Section 1: the raw, ungrouped ranking —
  // every supported market individually, exactly as computed, never
  // hidden behind family grouping).
  const [allMarketsOpportunities, setAllMarketsOpportunities] = useState<BestOpportunity[]>([]);
  const [allMarketsTopN, setAllMarketsTopN] = useState<TopN>(25);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const isDiversifiedTab = tab in DIVERSIFIED_TABS;
  const isSelfManagedTab = isDiversifiedTab || tab === "final_shortlist" || tab === "disagreements" || tab === "bookmaker_settings";
  const isNormalizedTab = !isSelfManagedTab && tab !== "manual_log" && tab !== "all_markets";
  const marketForTab: PlayerPropMarketType | undefined =
    tab === "disposals" ? "player_disposals" : tab === "goals" ? "player_goals" : undefined;

  useEffect(() => {
    if (isSelfManagedTab) return; // this tab's own component manages its own fetch
    if (tab === "all_markets") {
      setLoading(true);
      fetchBestOpportunities({
        marketScope: "all",
        includeUncertain,
        limit: allMarketsTopN === 0 ? undefined : allMarketsTopN,
      })
        .then(setAllMarketsOpportunities)
        .catch((err) => setError(err instanceof Error ? err.message : "Failed to load market ranking"))
        .finally(() => setLoading(false));
      return;
    }
    if (tab === "manual_log") {
      setLoading(true);
      fetchPropInsights({ confidence: confidence || undefined, includeUncertain })
        .then(setManualRows)
        .catch((err) => setError(err instanceof Error ? err.message : "Failed to load prop insights"))
        .finally(() => setLoading(false));
      return;
    }
    setLoading(true);
    fetchNormalizedPropInsights({
      market: marketForTab,
      confidence: confidence || undefined,
      includeUncertain,
      opportunitiesOnly: false,
      matchId: tab === "by_match" && selectedMatchId !== "" ? selectedMatchId : undefined,
    })
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load prop insights"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, confidence, includeUncertain, selectedMatchId, allMarketsTopN, isSelfManagedTab]);

  // "By Match" needs a match list to pick from - derive it from an
  // unfiltered fetch rather than a separate endpoint, since every
  // normalized row already carries match_id/round_number/season_year.
  const [allMatches, setAllMatches] = useState<{ match_id: number; round_number: number; season_year: number }[]>([]);
  useEffect(() => {
    if (tab !== "by_match") return;
    fetchNormalizedPropInsights({}).then((all) => {
      const seen = new Map<number, { match_id: number; round_number: number; season_year: number }>();
      for (const r of all) seen.set(r.match_id, { match_id: r.match_id, round_number: r.round_number, season_year: r.season_year });
      setAllMatches([...seen.values()].sort((a, b) => a.match_id - b.match_id));
    });
  }, [tab]);

  const availableBookmakers = useMemo(() => {
    const names = new Set<string>();
    for (const r of rows) for (const b of r.bookmakers) names.add(b.bookmaker_name);
    return [...names].sort();
  }, [rows]);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (bookmakerFilter && !r.bookmakers.some((b) => b.bookmaker_name === bookmakerFilter)) return false;
      if (FRESHNESS_RANK[r.odds_freshness] > FRESHNESS_RANK[maxFreshness]) return false;
      if (minDifferencePp !== "" && r.difference_pp * 100 < Number(minDifferencePp)) return false;
      if (minOdds !== "" && r.best_price < Number(minOdds)) return false;
      return true;
    });
  }, [rows, bookmakerFilter, maxFreshness, minDifferencePp, minOdds]);

  return (
    <main className="prop-insights-page">
      <header className="prop-insights-page__header">
        <h1>Prop Insights</h1>
        <p className="hint">
          Compares every available bookmaker price — manually entered and automatically fetched — against the
          model's own probabilities, and highlights the best price currently on offer. Model probability, fair
          odds, and expected value are model estimates only — not guaranteed outcomes.
        </p>
      </header>

      <nav className="tab-bar">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab-bar__tab${tab === t.key ? " tab-bar__tab--active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "best_available" && <BestAvailableView />}
      {isDiversifiedTab && <DiversifiedOpportunitiesView view={DIVERSIFIED_TABS[tab]!} />}
      {tab === "final_shortlist" && <FinalShortlistView />}
      {tab === "disagreements" && <ModelMarketDisagreementsView />}
      {tab === "bookmaker_settings" && <BookmakerSettingsPanel />}

      {tab === "all_markets" && (
        <>
          <section className="prop-insights-page__filters">
            <label>
              Show
              <select value={allMarketsTopN} onChange={(e) => setAllMarketsTopN(Number(e.target.value) as TopN)}>
                <option value={10}>Top 10</option>
                <option value={25}>Top 25</option>
                <option value={0}>All</option>
              </select>
            </label>
            <label className="prop-insights-page__checkbox">
              <input type="checkbox" checked={includeUncertain} onChange={(e) => setIncludeUncertain(e.target.checked)} />
              Include unconfirmed participation
            </label>
          </section>
          <p className="hint">The raw, ungrouped ranking — every supported market individually, with no family grouping or diversification.</p>

          {loading && <p className="loading-state">Loading…</p>}
          {error && <div className="error-banner">{error}</div>}

          {!loading && !error && allMarketsOpportunities.length === 0 && (
            <p className="empty-state">No markets currently pass the quality gates — check back once bookmaker markets open.</p>
          )}

          {!loading && !error && allMarketsOpportunities.length > 0 && (
            <div className="prop-insights-table-scroll">
              <table className="prop-insights-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Type</th>
                    <th>Opportunity</th>
                    <th>Model prob.</th>
                    <th>Best price</th>
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
                  {allMarketsOpportunities.map((o, i) => {
                    const key = `${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.market_type}-${o.threshold ?? o.line_value}`;
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
                          </td>
                          <td>{(o.model_probability * 100).toFixed(1)}%</td>
                          <td title={o.best_bookmaker}>
                            ${o.best_price.toFixed(2)}
                            <br />
                            <span className="hint">{o.best_bookmaker}</span>
                          </td>
                          <td>{o.n_bookmakers}</td>
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
                            <td colSpan={12}>
                              <OpportunityDrawer
                                bookmakers={o.bookmakers}
                                bestBookmaker={o.best_bookmaker}
                                whyModelLikesIt={o.why_model_likes_it}
                                calibration={o.calibration}
                                warnings={o.warnings}
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
        </>
      )}

      {isNormalizedTab && (
        <section className="prop-insights-page__filters">
          {tab === "by_match" && (
            <label>
              Match
              <select value={selectedMatchId} onChange={(e) => setSelectedMatchId(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Select a match…</option>
                {allMatches.map((m) => (
                  <option key={m.match_id} value={m.match_id}>
                    Match {m.match_id} — round {m.round_number}, {m.season_year}
                  </option>
                ))}
              </select>
            </label>
          )}
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
            Bookmaker
            <select value={bookmakerFilter} onChange={(e) => setBookmakerFilter(e.target.value)}>
              <option value="">All bookmakers</option>
              {availableBookmakers.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Max odds freshness
            <select value={maxFreshness} onChange={(e) => setMaxFreshness(e.target.value as OddsFreshness)}>
              <option value="fresh">Fresh only</option>
              <option value="aging">Fresh + aging</option>
              <option value="stale">Include stale</option>
            </select>
          </label>
          <label>
            Min. difference (pp)
            <input type="number" step="0.5" value={minDifferencePp} onChange={(e) => setMinDifferencePp(e.target.value)} placeholder="any" />
          </label>
          <label>
            Min. odds
            <input type="number" step="0.05" value={minOdds} onChange={(e) => setMinOdds(e.target.value)} placeholder="any" />
          </label>
          <label className="prop-insights-page__checkbox">
            <input type="checkbox" checked={includeUncertain} onChange={(e) => setIncludeUncertain(e.target.checked)} />
            Include unconfirmed participation
          </label>
        </section>
      )}
      {!isSelfManagedTab && <p className="hint">Confirmed-out players are never shown here, regardless of any setting above.</p>}

      {isNormalizedTab && loading && <p className="loading-state">Loading…</p>}
      {isNormalizedTab && error && <div className="error-banner">{error}</div>}

      {!loading && !error && isNormalizedTab && filtered.length === 0 && (
        <p className="hint">
          {tab === "by_match" && selectedMatchId === ""
            ? "Select a match above."
            : "No player prop markets match these filters yet — enter a manual quote or run the automated refresh."}
        </p>
      )}

      {!loading && !error && isNormalizedTab && filtered.length > 0 && (
        <div className="prop-insights-table-scroll">
          <table className="prop-insights-table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Market</th>
                <th>Model prob.</th>
                <th>Best odds</th>
                <th>Books</th>
                <th>Implied prob.</th>
                <th>Difference</th>
                <th>Model-est. EV</th>
                <th>Confidence</th>
                <th>Lineup</th>
                <th>Freshness</th>
                <th>Score</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={`${r.match_id}-${r.player_id}-${r.market_type}-${r.line_type}-${r.threshold}`}>
                  <td>
                    {r.player_name}
                    {!r.is_confirmed && (
                      <span className="prop-insights-table__unconfirmed-flag" title="Participation not confirmed">
                        unconfirmed
                      </span>
                    )}
                  </td>
                  <td>{marketLabel(r.market_type, r.line_type, r.threshold)}</td>
                  <td>{(r.model_probability * 100).toFixed(1)}%</td>
                  <td title={`${r.best_bookmaker} — first ${r.price_movement.first_price.toFixed(2)}, high ${r.price_movement.highest_price.toFixed(2)}, low ${r.price_movement.lowest_price.toFixed(2)}`}>
                    ${r.best_price.toFixed(2)}
                    <br />
                    <span className="hint">{r.best_bookmaker}</span>
                  </td>
                  <td>{r.n_bookmakers}</td>
                  <td title={r.overround_removed ? "No-vig market probability — bookmaker margin removed (same bookmaker's paired side)" : "Raw implied probability — bookmaker margin NOT removed, only one side of the market was quoted"}>
                    {((r.devigged_probability ?? r.raw_implied_probability) * 100).toFixed(1)}%
                    {!r.overround_removed && <span className="prop-insights-table__raw-flag">raw implied</span>}
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
                  <td>{LINEUP_LABELS[r.selection_status] ?? r.selection_status}</td>
                  <td>
                    <span className={`prop-insights-table__freshness prop-insights-table__freshness--${r.odds_freshness}`}>
                      {FRESHNESS_LABELS[r.odds_freshness]}
                    </span>
                  </td>
                  <td title={`difference ${r.opportunity_components.difference.toFixed(1)} + EV ${r.opportunity_components.expected_value.toFixed(1)} + confidence ${r.opportunity_components.confidence.toFixed(1)} + freshness ${r.opportunity_components.freshness.toFixed(1)} + lineup ${r.opportunity_components.lineup.toFixed(1)} + calibration ${r.opportunity_components.calibration.toFixed(1)}, x${r.opportunity_components.penalty_multiplier.toFixed(2)} (${r.opportunity_components.penalty_reasons.join(", ") || "no penalty"})`}>
                    {r.opportunity_score.toFixed(1)}
                  </td>
                  <td className="prop-insights-table__notes">
                    <span className={`edge-category-badge edge-category-badge--${r.edge_category}`}>{EDGE_LABELS[r.edge_category]}</span>
                    {r.warnings.length > 0 && (
                      <span className="prop-insights-table__warning-icon" title={r.warnings.join(" ")}>
                        ⚠
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && tab === "manual_log" && manualRows.length === 0 && (
        <p className="empty-state">No player prop quotes recorded yet — add some from a match's detail page.</p>
      )}

      {!loading && !error && tab === "manual_log" && manualRows.length > 0 && (
        <div className="prop-insights-table-scroll">
          <table className="prop-insights-table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Market</th>
                <th>Source</th>
                <th>Model prob.</th>
                <th>Offered odds</th>
                <th>Implied prob.</th>
                <th>Difference</th>
                <th>Model-est. EV</th>
                <th>Confidence</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {manualRows.map((r) => (
                <tr key={r.id}>
                  <td>
                    {r.player_name}
                    {!r.is_confirmed && <span className="prop-insights-table__unconfirmed-flag" title="Participation not confirmed">unconfirmed</span>}
                  </td>
                  <td>
                    {marketLabel(r.market_type, r.line_type, r.threshold)}
                    <br />
                    <span className="hint">{r.bookmaker_name}</span>
                  </td>
                  <td>
                    <span className={`prop-insights-table__source prop-insights-table__source--${r.source === "manual" ? "manual" : "automated"}`}>
                      {r.source === "manual" ? "Manual" : r.source}
                    </span>
                  </td>
                  <td>{(r.model_probability * 100).toFixed(1)}%</td>
                  <td>${r.offered_odds.toFixed(2)}</td>
                  <td title={r.overround_removed ? "No-vig market probability — bookmaker margin removed" : "Raw implied probability — bookmaker margin NOT removed, only one side of the market was quoted"}>
                    {((r.devigged_probability ?? r.raw_implied_probability) * 100).toFixed(1)}%
                    {!r.overround_removed && <span className="prop-insights-table__raw-flag">raw implied</span>}
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
