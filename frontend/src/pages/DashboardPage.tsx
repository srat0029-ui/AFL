import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import "./DashboardPage.css";
import Disclaimer from "../components/Disclaimer";
import { DisposalProjectionTable, GoalProjectionTable } from "../components/ProjectionTable";
import {
  fetchDashboard,
  fetchDiversifiedOpportunities,
  fetchLiveStatus,
  fetchRecentResults,
  fetchUpcomingProjections,
  type DashboardEntry,
  type DiversifiedOpportunity,
  type DisposalProjection,
  type GoalProjection,
  type LiveStatusReport,
  type MatchSummary,
  type WeeklySummary,
} from "../api/client";
import { dayGroupKey, dayGroupLabel, formatClockTime, formatCompactDateTime, formatShortDate } from "../lib/datetime";

const EDGE_LABELS: Record<string, string> = {
  strong: "Strong edge",
  moderate: "Moderate edge",
  weak: "Weak edge",
  none: "No edge",
};

const CONFIDENCE_LABELS: Record<string, string> = {
  higher: "Higher confidence",
  moderate: "Moderate confidence",
  lower: "Lower confidence",
  insufficient_data: "Insufficient data",
};

const formatKickoff = formatCompactDateTime;
const formatResultDate = formatShortDate;

function RecentResults({ results }: { results: MatchSummary[] }) {
  if (results.length === 0) {
    return null;
  }
  return (
    <section className="recent-results">
      <h2>Recent results</h2>
      <div className="recent-results__list">
        {results.map((match) => {
          const homeWon = (match.home_score ?? 0) > (match.away_score ?? 0);
          const awayWon = (match.away_score ?? 0) > (match.home_score ?? 0);
          return (
            <Link to={`/matches/${match.id}`} key={match.id} className="result-card">
              <div className="result-card__meta">
                {formatResultDate(match.scheduled_start)} · Round {match.round_number}
                {match.venue ? ` · ${match.venue.name}` : ""}
              </div>
              <div className="result-card__row">
                <span className={`result-card__team${homeWon ? " result-card__team--winner" : ""}`}>
                  {match.home_team.name}
                </span>
                <span className="result-card__score">{match.home_score}</span>
              </div>
              <div className="result-card__row">
                <span className={`result-card__team${awayWon ? " result-card__team--winner" : ""}`}>
                  {match.away_team.name}
                </span>
                <span className="result-card__score">{match.away_score}</span>
              </div>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

function BestOpportunitiesSection({
  opportunities,
  summary,
  loading,
}: {
  opportunities: DiversifiedOpportunity[];
  summary: WeeklySummary | null;
  loading: boolean;
}) {
  return (
    <section className="dashboard-section">
      <div className="dashboard-section__header">
        <h2>Best Opportunities This Round</h2>
        <Link to="/prop-insights" className="dashboard-section__link">
          View all →
        </Link>
      </div>
      {summary && (
        <p className="hint dashboard-section__weekly-summary">
          {summary.round_number !== null && `Round ${summary.round_number} · `}
          {summary.n_opportunities_passing_gates} opportunities pass current quality gates · {summary.n_unique_players} unique players ·{" "}
          {summary.n_unique_matches} matches · {summary.n_bookmakers} bookmakers
          {summary.best_difference_pp !== null && ` · best model-market difference: +${(summary.best_difference_pp * 100).toFixed(1)}pp`}
          {summary.best_price_advantage_pct !== null && ` · best price-shopping improvement: +${summary.best_price_advantage_pct.toFixed(1)}%`}
        </p>
      )}
      {loading && <p className="loading-state">Loading…</p>}
      {!loading && opportunities.length === 0 && (
        <p className="empty-state">
          No opportunities currently pass the default quality gates — check Prop Insights directly to include
          uncertain participation, stale odds, or low-history markets.
        </p>
      )}
      {!loading && opportunities.length > 0 && (
        <div className="opportunity-list">
          {opportunities.map((o, i) => (
            <div key={`${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.threshold ?? o.line_value}`} className="opportunity-list__row">
              <span className="opportunity-list__rank">{i + 1}</span>
              <span className={`prop-insights-table__type-badge prop-insights-table__type-badge--${o.opportunity_type}`}>
                {o.opportunity_type === "player" ? "Player" : "Team"}
              </span>
              <span className="opportunity-list__label">
                {o.label}
                {o.alternate_lines.length > 0 && (
                  <span className="diversified-view__alt-count" title={`${o.alternate_lines.length} alternate line(s) in this family`}>
                    +{o.alternate_lines.length} alt
                  </span>
                )}
              </span>
              <span className="opportunity-list__price">
                ${o.best_price.toFixed(2)} <span className="hint">{o.best_bookmaker}</span>
              </span>
              <span className={o.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
                {o.difference_pp >= 0 ? "+" : ""}
                {(o.difference_pp * 100).toFixed(1)}pp
              </span>
              <span
                className={`confidence-badge confidence-badge--${o.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}
              >
                {o.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient data")}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function MarketCoverageSection({ status, loading }: { status: LiveStatusReport | null; loading: boolean }) {
  if (loading) {
    return (
      <section className="dashboard-section">
        <h2>Market Coverage</h2>
        <p className="loading-state">Loading…</p>
      </section>
    );
  }
  if (!status) return null;

  const s = status.round_summary;
  const bookmakers = new Set<string>();
  for (const m of status.matches) for (const b of m.bookmakers_observed) bookmakers.add(b);
  const lastRefreshTimes = status.matches.map((m) => m.last_odds_refresh).filter((t): t is string => t !== null);
  const lastRefresh = lastRefreshTimes.length > 0 ? lastRefreshTimes.sort().at(-1)! : null;
  const recommendedNext = lastRefresh ? new Date(new Date(lastRefresh).getTime() + 2 * 60 * 60 * 1000) : null;

  return (
    <section className="dashboard-section">
      <h2>Market Coverage</h2>
      <div className="market-coverage-grid">
        <div>
          <span className="market-coverage-grid__label">Matches with odds</span>
          <span className="market-coverage-grid__value">
            {s.n_matches_with_bookmaker_events}/{s.n_upcoming_matches}
          </span>
        </div>
        <div>
          <span className="market-coverage-grid__label">Bookmakers active</span>
          <span className="market-coverage-grid__value">{bookmakers.size}</span>
        </div>
        <div>
          <span className="market-coverage-grid__label">Matches with prop markets</span>
          <span className="market-coverage-grid__value">{s.n_matches_with_prop_markets}</span>
        </div>
        <div>
          <span className="market-coverage-grid__label">Unique players w/ markets</span>
          <span className="market-coverage-grid__value">{s.n_unique_players_with_markets}</span>
        </div>
      </div>
      {lastRefresh ? (
        <p className="hint dashboard-section__refresh-line">
          Last odds refresh: {formatClockTime(lastRefresh)} · Recommended next refresh:{" "}
          {recommendedNext ? formatClockTime(recommendedNext) : "—"}
        </p>
      ) : (
        <p className="hint dashboard-section__refresh-line">Waiting for bookmaker markets — none observed yet this round.</p>
      )}
    </section>
  );
}

function LiveDataStatusSection({ status, loading }: { status: LiveStatusReport | null; loading: boolean }) {
  if (loading || !status) return null;
  const lastRun = status.recent_runs[0];
  return (
    <section className="dashboard-section dashboard-section--compact">
      <div className="dashboard-section__header">
        <h2>Live Data Status</h2>
        <Link to="/live-status" className="dashboard-section__link">
          Full status →
        </Link>
      </div>
      {lastRun ? (
        <p className="hint">
          Last run <span className={`live-status-badge live-status-badge--run-${lastRun.overall_status}`}>{lastRun.overall_status.toUpperCase()}</span> at{" "}
          {formatCompactDateTime(lastRun.run_at)} · {lastRun.quotes_added} quotes · {lastRun.observations_added} observations
        </p>
      ) : (
        <p className="hint">No run-live-cycle runs recorded yet.</p>
      )}
    </section>
  );
}

function DashboardPage() {
  const [entries, setEntries] = useState<DashboardEntry[]>([]);
  const [results, setResults] = useState<MatchSummary[]>([]);
  const [opportunities, setOpportunities] = useState<DiversifiedOpportunity[]>([]);
  const [weeklySummary, setWeeklySummary] = useState<WeeklySummary | null>(null);
  const [opportunitiesLoading, setOpportunitiesLoading] = useState(true);
  const [disposalProjections, setDisposalProjections] = useState<DisposalProjection[]>([]);
  const [goalProjections, setGoalProjections] = useState<GoalProjection[]>([]);
  const [liveStatus, setLiveStatus] = useState<LiveStatusReport | null>(null);
  const [liveStatusLoading, setLiveStatusLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDashboard()
      .then(setEntries)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load dashboard"))
      .finally(() => setLoading(false));
    // Independent of model/dashboard availability — reads normalised match
    // data directly, so it loads even before the modelling CLIs have run.
    fetchRecentResults().then(setResults).catch(() => setResults([]));

    fetchDiversifiedOpportunities({ view: "overall", marketScope: "all", limit: 5 })
      .then((r) => {
        setOpportunities(r.opportunities);
        setWeeklySummary(r.summary);
      })
      .catch(() => {
        setOpportunities([]);
        setWeeklySummary(null);
      })
      .finally(() => setOpportunitiesLoading(false));

    fetchUpcomingProjections({})
      .then((r) => {
        setDisposalProjections([...(r.disposals ?? [])].sort((a, b) => b.expected - a.expected).slice(0, 5));
        setGoalProjections([...(r.goals ?? [])].sort((a, b) => b.expected - a.expected).slice(0, 5));
      })
      .catch(() => {
        setDisposalProjections([]);
        setGoalProjections([]);
      });

    fetchLiveStatus()
      .then(setLiveStatus)
      .catch(() => setLiveStatus(null))
      .finally(() => setLiveStatusLoading(false));
  }, []);

  // Section 20: group upcoming matches under day-of-week headers, in the
  // Australia/Hobart display timezone (not UTC, not browser-local).
  const groupedByDay = useMemo(() => {
    const groups = new Map<string, { label: string; entries: DashboardEntry[] }>();
    for (const entry of entries) {
      const key = dayGroupKey(entry.match.scheduled_start);
      if (!groups.has(key)) groups.set(key, { label: dayGroupLabel(entry.match.scheduled_start), entries: [] });
      groups.get(key)!.entries.push(entry);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [entries]);

  return (
    <main className="dashboard-page">
      <h1>Dashboard</h1>
      <p className="subtitle">Model probabilities, best-priced opportunities, and market coverage for the current AFL round.</p>

      {error && <div className="error-banner">{error}</div>}

      <BestOpportunitiesSection opportunities={opportunities} summary={weeklySummary} loading={opportunitiesLoading} />

      <section className="dashboard-section">
        <h2>Upcoming Matches</h2>
        {loading && <p className="loading-state">Loading…</p>}
        {!loading && !error && entries.length === 0 && (
          <p className="empty-state">No upcoming AFL fixtures found. Make sure you've run the data ingestion (see the README).</p>
        )}

        {groupedByDay.map(([key, group]) => (
          <div key={key} className="dashboard-day-group">
            <h3 className="dashboard-day-group__label">{group.label}</h3>
            <div className="match-card-grid">
              {group.entries.map((entry) => {
                const predictions = entry.predictions;
                const homeWinPct = predictions ? predictions.elo_home_win_probability * 100 : null;
                const awayWinPct = homeWinPct === null ? null : 100 - homeWinPct;
                return (
                  <Link to={`/matches/${entry.match.id}`} key={entry.match.id} className="match-card">
                    <div className="match-card__meta">
                      {formatKickoff(entry.match.scheduled_start)}
                      {entry.match.venue ? ` · ${entry.match.venue.name}` : ""}
                    </div>

                    <div className="match-card__teams">
                      <span
                        className="match-card__swatch"
                        style={{ background: entry.match.home_team.primary_colour ?? "#666" }}
                      />
                      <span className="match-card__team-name">{entry.match.home_team.name}</span>
                      <span className="match-card__vs">vs</span>
                      <span className="match-card__team-name">{entry.match.away_team.name}</span>
                      <span
                        className="match-card__swatch"
                        style={{ background: entry.match.away_team.primary_colour ?? "#666" }}
                      />
                    </div>

                    {homeWinPct !== null && awayWinPct !== null ? (
                      <>
                        <div className="match-card__prob-bar">
                          <div
                            className="match-card__prob-bar-home"
                            style={{
                              width: `${homeWinPct}%`,
                              background: entry.match.home_team.primary_colour ?? "#3b82f6",
                            }}
                          />
                          <div
                            className="match-card__prob-bar-away"
                            style={{
                              width: `${awayWinPct}%`,
                              background: entry.match.away_team.primary_colour ?? "#6b7280",
                            }}
                          />
                        </div>
                        <div className="match-card__prob-labels">
                          <span>{homeWinPct.toFixed(0)}%</span>
                          <span>{awayWinPct.toFixed(0)}%</span>
                        </div>

                        {entry.best_edge ? (
                          <div className="match-card__edge">
                            <span className={`edge-badge edge-badge--${entry.best_edge.edge_tier}`}>
                              {EDGE_LABELS[entry.best_edge.edge_tier]}
                            </span>
                            <span className={`confidence-badge confidence-badge--${entry.best_edge.confidence_tier}`}>
                              {CONFIDENCE_LABELS[entry.best_edge.confidence_tier]}
                            </span>
                            <span className="match-card__edge-selection">on {entry.best_edge.selection}</span>
                          </div>
                        ) : (
                          <div className="match-card__edge match-card__edge--none">No odds recorded yet</div>
                        )}
                      </>
                    ) : (
                      <div className="match-card__edge match-card__edge--none">Model prediction unavailable</div>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      {(disposalProjections.length > 0 || goalProjections.length > 0) && (
        <section className="dashboard-section">
          <div className="dashboard-section__header">
            <h2>Player Projections</h2>
            <Link to="/player-insights" className="dashboard-section__link">
              View all →
            </Link>
          </div>
          <p className="hint">Strongest raw model probabilities this round, before comparing against any market price.</p>
          {disposalProjections.length > 0 && <DisposalProjectionTable rows={disposalProjections} showMatchColumn />}
          {goalProjections.length > 0 && <GoalProjectionTable rows={goalProjections} showMatchColumn />}
        </section>
      )}

      <MarketCoverageSection status={liveStatus} loading={liveStatusLoading} />
      <LiveDataStatusSection status={liveStatus} loading={liveStatusLoading} />

      <RecentResults results={results} />

      <Disclaimer />
    </main>
  );
}

export default DashboardPage;
