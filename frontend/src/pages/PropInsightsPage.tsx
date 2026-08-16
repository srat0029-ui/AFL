import { useEffect, useMemo, useState } from "react";
import "./PropInsightsPage.css";
import Disclaimer from "../components/Disclaimer";
import {
  fetchNormalizedPropInsights,
  fetchPropInsights,
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

type Tab = "opportunities" | "disposals" | "goals" | "by_match" | "manual_log";

const TABS: { key: Tab; label: string }[] = [
  { key: "opportunities", label: "Best Opportunities" },
  { key: "disposals", label: "Disposals" },
  { key: "goals", label: "Goals" },
  { key: "by_match", label: "By Match" },
  { key: "manual_log", label: "Quote Log" },
];

function marketLabel(marketType: PlayerPropMarketType, lineType: string, threshold: number): string {
  const market = marketType === "player_disposals" ? "Disposals" : "Goals";
  const line = lineType === "multi_plus" ? `${threshold.toFixed(1)}+` : `o/u ${threshold.toFixed(1)}`;
  return `${market} ${line}`;
}

function PropInsightsPage() {
  const [tab, setTab] = useState<Tab>("opportunities");
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

  const isNormalizedTab = tab !== "manual_log";
  const marketForTab: PlayerPropMarketType | undefined =
    tab === "disposals" ? "player_disposals" : tab === "goals" ? "player_goals" : undefined;

  useEffect(() => {
    if (!isNormalizedTab) {
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
      opportunitiesOnly: tab === "opportunities",
      matchId: tab === "by_match" && selectedMatchId !== "" ? selectedMatchId : undefined,
    })
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load prop insights"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, confidence, includeUncertain, selectedMatchId]);

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

      <nav className="prop-insights-page__tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`prop-insights-page__tab${tab === t.key ? " prop-insights-page__tab--active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

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
      <p className="hint">Confirmed-out players are never shown here, regardless of any setting above.</p>

      {loading && <p className="hint">Loading…</p>}
      {error && <div className="prop-insights-page__error">{error}</div>}

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
                  <td title={r.overround_removed ? "Vig removed (same bookmaker's paired side)" : "Raw implied — vig not removed"}>
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

      {!loading && !error && !isNormalizedTab && manualRows.length === 0 && (
        <p className="hint">No player prop quotes recorded yet — add some from a match's detail page.</p>
      )}

      {!loading && !error && !isNormalizedTab && manualRows.length > 0 && (
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
