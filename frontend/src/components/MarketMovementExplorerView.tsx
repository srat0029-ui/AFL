import type {
  BookmakerQuotePoint,
  MarketMovementMatchWithHistory,
  MarketMovementSeries,
  MatchMarketOptions,
} from "../api/client";
import { formatCompactDateTime, formatPreciseDateTime } from "../lib/datetime";
import { hrs, pct, type OptionChoice } from "../features/marketMovement";
import MarketMovementChart from "./MarketMovementChart";
import MarketMovementSummaryCard from "./MarketMovementSummaryCard";

export interface MarketMovementExplorerViewProps {
  matches: MarketMovementMatchWithHistory[] | null;
  matchesLoading: boolean;
  matchesError: string | null;
  onRetryMatches: () => void;

  selectedMatchId: number | null;
  onSelectMatch: (matchId: number | null) => void;

  options: MatchMarketOptions | null;
  optionsLoading: boolean;
  optionsError: string | null;
  onRetryOptions: () => void;
  optionChoices: OptionChoice[];

  selectedKey: string | null;
  onSelectOption: (key: string | null) => void;

  series: MarketMovementSeries | null;
  seriesLoading: boolean;
  seriesError: string | null;
  onRetrySeries: () => void;

  availableBookmakers: string[];
  bookmakerFilter: Set<string> | null;
  onToggleBookmaker: (name: string) => void;
  filteredQuotes: BookmakerQuotePoint[];
}

function MarketMovementExplorerView({
  matches, matchesLoading, matchesError, onRetryMatches,
  selectedMatchId, onSelectMatch,
  options, optionsLoading, optionsError, onRetryOptions, optionChoices,
  selectedKey, onSelectOption,
  series, seriesLoading, seriesError, onRetrySeries,
  availableBookmakers, bookmakerFilter, onToggleBookmaker, filteredQuotes,
}: MarketMovementExplorerViewProps) {
  return (
    <>
      <div className="mme-card">
        <h3>1. Choose a match</h3>
        {matchesLoading && <p className="loading-state">Loading matches…</p>}
        {matchesError && (
          <div className="error-banner">
            {matchesError} <button type="button" className="btn" onClick={onRetryMatches}>Retry</button>
          </div>
        )}
        {!matchesLoading && !matchesError && matches && matches.length === 0 && (
          <p className="empty-state">No matches have any recorded bookmaker or model movement history yet.</p>
        )}
        {!matchesLoading && !matchesError && matches && matches.length > 0 && (
          <select className="mme-select" value={selectedMatchId ?? ""} onChange={(e) => onSelectMatch(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Select a match…</option>
            {matches.map((m) => (
              <option key={m.match.match_id} value={m.match.match_id}>
                {m.match.home_team} vs {m.match.away_team} — {formatCompactDateTime(m.match.scheduled_start)} ({m.match.status}) — {m.n_odds_quotes + m.n_player_prop_quotes} quote(s), {m.n_model_observations} model observation(s)
              </option>
            ))}
          </select>
        )}
      </div>

      {selectedMatchId !== null && (
        <div className="mme-card">
          <h3>2. Choose a market / selection</h3>
          {optionsLoading && <p className="loading-state">Loading markets…</p>}
          {optionsError && (
            <div className="error-banner">
              {optionsError} <button type="button" className="btn" onClick={onRetryOptions}>Retry</button>
            </div>
          )}
          {!optionsLoading && !optionsError && options && optionChoices.length === 0 && (
            <p className="empty-state">No team or player markets have recorded history for this match.</p>
          )}
          {!optionsLoading && !optionsError && optionChoices.length > 0 && (
            <select className="mme-select" value={selectedKey ?? ""} onChange={(e) => onSelectOption(e.target.value || null)}>
              <option value="">Select a market…</option>
              {(["Team markets", "Player markets"] as const).map((group) => {
                const inGroup = optionChoices.filter((c) => c.group === group);
                if (inGroup.length === 0) return null;
                return (
                  <optgroup key={group} label={group}>
                    {inGroup.map((c) => (
                      <option key={c.key} value={c.key}>
                        {c.label} — {c.nQuotes} quote(s){c.hasModelSeries ? "" : " (no model series)"}
                      </option>
                    ))}
                  </optgroup>
                );
              })}
            </select>
          )}
        </div>
      )}

      {seriesLoading && <p className="loading-state">Loading movement history…</p>}
      {seriesError && (
        <div className="error-banner">
          {seriesError} <button type="button" className="btn" onClick={onRetrySeries}>Retry</button>
        </div>
      )}

      {!seriesLoading && !seriesError && series && (
        <>
          <div className="mme-card">
            <h3>{series.identity_label}</h3>
            <p className="hint">
              {series.match.home_team} vs {series.match.away_team} — kickoff {formatPreciseDateTime(series.match.scheduled_start)} ({series.match.status})
            </p>
            <ul className="mme-notes">
              {series.methodology_notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </div>

          <div className="mme-summary-grid">
            <MarketMovementSummaryCard summary={series.bookmaker_summary} />
            <MarketMovementSummaryCard summary={series.consensus_summary} />
            <MarketMovementSummaryCard summary={series.model_summary} />
          </div>

          {series.lineup_status_changes.length > 0 && (
            <div className="mme-card">
              <h3>Lineup-status changes</h3>
              <p className="hint">A change observed alongside the model's own value at that same moment — never a causal claim.</p>
              <div className="mme-table-scroll">
                <table className="mme-table">
                  <thead>
                    <tr>
                      <th>From</th>
                      <th>To</th>
                      <th>When</th>
                      <th>Hours to kickoff</th>
                      <th>Model value at that observation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {series.lineup_status_changes.map((c, i) => (
                      <tr key={i}>
                        <td>{c.from_status ?? "—"}</td>
                        <td>{c.to_status ?? "—"}</td>
                        <td>{formatPreciseDateTime(c.changed_at)}</td>
                        <td>{hrs(c.hours_to_kickoff)}</td>
                        <td>
                          {c.value_changed_at_same_observation
                            ? `${pct(c.value_before)} → ${pct(c.value_after)} (coincided with this status change)`
                            : `${pct(c.value_after)} (unchanged at this observation)`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="mme-card">
            <h3>Timeline</h3>
            {availableBookmakers.length > 1 && (
              <div className="mme-bookmaker-filter">
                <span className="hint">Filter bookmaker quotes:</span>
                {availableBookmakers.map((name) => (
                  <label key={name} className="mme-bookmaker-filter__item">
                    <input type="checkbox" checked={bookmakerFilter === null || bookmakerFilter.has(name)} onChange={() => onToggleBookmaker(name)} />
                    {name}
                  </label>
                ))}
              </div>
            )}
            <MarketMovementChart
              bookmakerQuotes={filteredQuotes}
              consensusSeries={series.consensus_series}
              modelObservations={series.model_observations}
              lineupStatusChanges={series.lineup_status_changes}
            />
            <div className="mme-legend">
              <span className="mme-legend__item"><span className="mme-legend__swatch mme-legend__swatch--bookmaker" /> Bookmaker quotes</span>
              <span className="mme-legend__item"><span className="mme-legend__swatch mme-legend__swatch--consensus" /> Consensus</span>
              <span className="mme-legend__item"><span className="mme-legend__swatch mme-legend__swatch--model" /> Model</span>
              {series.lineup_status_changes.length > 0 && (
                <span className="mme-legend__item"><span className="mme-legend__swatch mme-legend__swatch--lineup" /> Lineup status change</span>
              )}
            </div>
          </div>

          <div className="mme-card">
            <h3>Evidence — bookmaker quotes ({filteredQuotes.length})</h3>
            {filteredQuotes.length === 0 ? (
              <p className="empty-state">No bookmaker quotes recorded pre-kickoff for this selection.</p>
            ) : (
              <div className="mme-table-scroll">
                <table className="mme-table">
                  <thead>
                    <tr>
                      <th>Bookmaker</th>
                      <th>Price</th>
                      <th>Implied probability</th>
                      <th>Recorded at</th>
                      <th>Hours to kickoff</th>
                      <th>Source</th>
                      <th>Closing line?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...filteredQuotes].sort((a, b) => b.hours_to_kickoff - a.hours_to_kickoff).map((q, i) => (
                      <tr key={i}>
                        <td>{q.bookmaker_name}</td>
                        <td>${q.price_decimal.toFixed(2)}</td>
                        <td>{pct(q.raw_implied_probability)}</td>
                        <td>{formatPreciseDateTime(q.recorded_at)}</td>
                        <td>{hrs(q.hours_to_kickoff)}</td>
                        <td>{q.source}</td>
                        <td>{q.is_closing_line ? "Yes (flagged in data)" : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {series.consensus_series.length > 0 && (
            <div className="mme-card">
              <h3>Evidence — bookmaker consensus ({series.consensus_series.length})</h3>
              <div className="mme-table-scroll">
                <table className="mme-table">
                  <thead>
                    <tr>
                      <th>As of</th>
                      <th>Hours to kickoff</th>
                      <th>Consensus probability</th>
                      <th>Bookmakers</th>
                      <th>De-vigged</th>
                      <th>Spread</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...series.consensus_series].sort((a, b) => b.hours_to_kickoff - a.hours_to_kickoff).map((c, i) => (
                      <tr key={i}>
                        <td>{formatPreciseDateTime(c.as_of)}</td>
                        <td>{hrs(c.hours_to_kickoff)}</td>
                        <td>{pct(c.consensus_probability)}</td>
                        <td>{c.n_bookmakers}</td>
                        <td>{c.n_devigged} / {c.n_bookmakers}</td>
                        <td>{pct(c.spread)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(series.model_observations.length > 0 || series.model_projected_mean_observations.length > 0) && (
            <div className="mme-card">
              <h3>Evidence — model observations ({series.model_observations.length + series.model_projected_mean_observations.length})</h3>
              <div className="mme-table-scroll">
                <table className="mme-table">
                  <thead>
                    <tr>
                      <th>Value type</th>
                      <th>Value</th>
                      <th>Recorded at</th>
                      <th>Hours to kickoff</th>
                      <th>Model version</th>
                      <th>Lineup status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...series.model_observations, ...series.model_projected_mean_observations]
                      .sort((a, b) => b.hours_to_kickoff - a.hours_to_kickoff)
                      .map((o, i) => (
                        <tr key={i}>
                          <td>{o.value_type}</td>
                          <td>{o.value_kind === "probability" ? pct(o.value) : o.value.toFixed(2)}</td>
                          <td>{formatPreciseDateTime(o.recorded_at)}</td>
                          <td>{hrs(o.hours_to_kickoff)}</td>
                          <td>{o.model_version}</td>
                          <td>{o.lineup_status ?? "—"}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(series.bookmaker_quotes_post_kickoff.length > 0 || series.model_observations_post_kickoff.length > 0) && (
            <div className="mme-card mme-card--diagnostic">
              <h3>At/after-kickoff observations (diagnostic only — excluded from all movement figures above)</h3>
              <div className="mme-table-scroll">
                <table className="mme-table">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>Value</th>
                      <th>Recorded at</th>
                    </tr>
                  </thead>
                  <tbody>
                    {series.bookmaker_quotes_post_kickoff.map((q, i) => (
                      <tr key={`bq-post-${i}`}>
                        <td>Bookmaker ({q.bookmaker_name})</td>
                        <td>${q.price_decimal.toFixed(2)} ({pct(q.raw_implied_probability)})</td>
                        <td>{formatPreciseDateTime(q.recorded_at)}</td>
                      </tr>
                    ))}
                    {series.model_observations_post_kickoff.map((o, i) => (
                      <tr key={`mo-post-${i}`}>
                        <td>Model ({o.value_type})</td>
                        <td>{o.value_kind === "probability" ? pct(o.value) : o.value.toFixed(2)}</td>
                        <td>{formatPreciseDateTime(o.recorded_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {!seriesLoading && !seriesError && !series && selectedKey !== null && (
        <p className="empty-state">Select a market above to view its movement history.</p>
      )}
    </>
  );
}

export default MarketMovementExplorerView;
