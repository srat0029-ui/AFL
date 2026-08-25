import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import "./DashboardPage.css";
import DataFreshnessPanel from "../components/DataFreshnessPanel";
import Disclaimer from "../components/Disclaimer";
import {
  fetchDashboard,
  fetchDataFreshness,
  fetchDiversifiedOpportunities,
  fetchLiveStatus,
  fetchRecentResults,
  type DashboardEntry,
  type DataFreshnessItem,
  type DiversifiedOpportunity,
  type LiveStatusReport,
  type MatchSummary,
  type WeeklySummary,
} from "../api/client";
import { dayGroupKey, dayGroupLabel, formatClockTime, formatShortDate, formatTimeOnly } from "../lib/datetime";

function RoundStatusStrip({
  entries,
  freshnessItems,
  liveStatus,
}: {
  entries: DashboardEntry[];
  freshnessItems: DataFreshnessItem[];
  liveStatus: LiveStatusReport | null;
}) {
  const nFresh = freshnessItems.filter((i) => i.status === "fresh").length;
  const lastRefreshed = freshnessItems
    .map((i) => i.last_refreshed)
    .filter((t): t is string => t !== null)
    .sort()
    .at(-1);
  const nConfirmed = liveStatus?.round_summary.n_confirmed_lineups ?? null;

  return (
    <div className="stat-strip">
      <div className="stat-strip__item">
        <span className="stat-strip__label">Upcoming matches</span>
        <span className="stat-strip__value num">{entries.length}</span>
      </div>
      <div className="stat-strip__item">
        <span className="stat-strip__label">Fresh markets</span>
        <span className="stat-strip__value num">
          {nFresh}/{freshnessItems.length || "–"}
        </span>
      </div>
      <div className="stat-strip__item">
        <span className="stat-strip__label">Confirmed players</span>
        <span className="stat-strip__value num">{nConfirmed ?? "–"}</span>
      </div>
      <div className="stat-strip__item">
        <span className="stat-strip__label">Data last refreshed</span>
        <span className="stat-strip__value">{lastRefreshed ? formatClockTime(lastRefreshed) : "—"}</span>
      </div>
    </div>
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
      <div className="section-row">
        <h2 className="section-title">Best opportunities</h2>
        <Link to="/prop-insights" className="section-row__link">
          View all →
        </Link>
      </div>
      {summary && (
        <p className="hint dashboard-section__weekly-summary">
          {summary.n_opportunities_passing_gates} passing quality gates · {summary.n_unique_players} players ·{" "}
          {summary.n_unique_matches} matches · {summary.n_bookmakers} bookmakers
        </p>
      )}
      {loading && <p className="loading-state">Loading…</p>}
      {!loading && opportunities.length === 0 && (
        <p className="empty-state">No opportunities currently pass the default quality gates.</p>
      )}
      {!loading && opportunities.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th></th>
                <th>Selection</th>
                <th className="num">Price</th>
                <th className="num">Edge</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {opportunities.map((o, i) => (
                <tr key={`${o.opportunity_type}-${o.match_id}-${o.player_id ?? o.selection}-${o.threshold ?? o.line_value}`}>
                  <td className="num">{i + 1}</td>
                  <td>
                    {o.label}
                    {o.alternate_lines.length > 0 && <span className="chip chip--accent" style={{ marginLeft: "0.4rem" }}>+{o.alternate_lines.length}</span>}
                  </td>
                  <td className="num">
                    ${o.best_price.toFixed(2)} <span className="hint">{o.best_bookmaker}</span>
                  </td>
                  <td className={o.difference_pp >= 0 ? "num prop-insights-table__diff-pos" : "num prop-insights-table__diff-neg"}>
                    {o.difference_pp >= 0 ? "+" : ""}
                    {(o.difference_pp * 100).toFixed(1)}pp
                  </td>
                  <td>
                    <span className={`confidence-badge confidence-badge--${o.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
                      {o.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient data")}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function UpcomingMatchesSection({ groupedByDay, loading, error }: { groupedByDay: [string, { label: string; entries: DashboardEntry[] }][]; loading: boolean; error: string | null }) {
  return (
    <section className="dashboard-section">
      <h2 className="section-title">Upcoming matches</h2>
      {loading && <p className="loading-state">Loading…</p>}
      {!loading && !error && groupedByDay.length === 0 && (
        <p className="empty-state">No upcoming AFL fixtures found. Make sure you've run the data ingestion (see the README).</p>
      )}

      {groupedByDay.map(([key, group]) => (
        <div key={key} className="dashboard-day-group">
          <h3 className="dashboard-day-group__label">{group.label}</h3>
          <div className="match-row-list">
            {group.entries.map((entry) => {
              const predictions = entry.predictions;
              const homeWinPct = predictions ? predictions.elo_home_win_probability * 100 : null;
              const awayWinPct = homeWinPct === null ? null : 100 - homeWinPct;
              return (
                <Link to={`/matches/${entry.match.id}`} key={entry.match.id} className="match-row">
                  <span className="match-row__time">{formatTimeOnly(entry.match.scheduled_start)}</span>
                  <span className="match-row__teams">
                    {entry.match.home_team.name} <span className="match-row__vs">v</span> {entry.match.away_team.name}
                  </span>
                  <span className="match-row__prob num">
                    {homeWinPct !== null && awayWinPct !== null ? `${homeWinPct.toFixed(0)}% / ${awayWinPct.toFixed(0)}%` : "—"}
                  </span>
                  <span className="match-row__edge">
                    {entry.best_edge ? (
                      <span className={`chip chip--${entry.best_edge.edge_tier === "none" ? "neutral" : entry.best_edge.edge_tier === "weak" ? "warning" : "success"}`}>
                        {entry.best_edge.edge_tier} edge
                      </span>
                    ) : (
                      <span className="hint">no odds yet</span>
                    )}
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </section>
  );
}

function SystemStatusSection({ status }: { status: LiveStatusReport | null }) {
  const lastRun = status?.recent_runs[0] ?? null;
  return (
    <section className="dashboard-section dashboard-section--compact">
      <details className="system-status">
        <summary>
          <span className="section-title">System status</span>
          {lastRun && (
            <span className={`chip chip--${lastRun.overall_status === "ok" ? "success" : lastRun.overall_status === "partial" ? "warning" : "danger"}`}>
              last run {lastRun.overall_status}
            </span>
          )}
          <Link to="/live-status" className="section-row__link" onClick={(e) => e.stopPropagation()}>
            Full status →
          </Link>
        </summary>
        <div className="system-status__body">
          <DataFreshnessPanel />
        </div>
      </details>
    </section>
  );
}

function RecentResults({ results }: { results: MatchSummary[] }) {
  if (results.length === 0) return null;
  return (
    <section className="dashboard-section dashboard-section--compact">
      <h2 className="section-title">Recent results</h2>
      <div className="match-row-list">
        {results.map((match) => {
          const homeWon = (match.home_score ?? 0) > (match.away_score ?? 0);
          return (
            <Link to={`/matches/${match.id}`} key={match.id} className="match-row">
              <span className="match-row__time">{formatShortDate(match.scheduled_start)}</span>
              <span className="match-row__teams">
                <span className={homeWon ? "match-row__team--winner" : undefined}>{match.home_team.name}</span> {match.home_score} —{" "}
                {match.away_score} <span className={!homeWon ? "match-row__team--winner" : undefined}>{match.away_team.name}</span>
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

function DashboardPage() {
  const [entries, setEntries] = useState<DashboardEntry[]>([]);
  const [results, setResults] = useState<MatchSummary[]>([]);
  const [opportunities, setOpportunities] = useState<DiversifiedOpportunity[]>([]);
  const [weeklySummary, setWeeklySummary] = useState<WeeklySummary | null>(null);
  const [opportunitiesLoading, setOpportunitiesLoading] = useState(true);
  const [liveStatus, setLiveStatus] = useState<LiveStatusReport | null>(null);
  const [freshnessItems, setFreshnessItems] = useState<DataFreshnessItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDashboard()
      .then(setEntries)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load dashboard"))
      .finally(() => setLoading(false));
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

    fetchLiveStatus().then(setLiveStatus).catch(() => setLiveStatus(null));
    fetchDataFreshness()
      .then((r) => setFreshnessItems(r.items))
      .catch(() => setFreshnessItems([]));
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
      <h1 className="page-title">Dashboard</h1>
      <p className="page-subtitle">What's happening this round, and whether the data is ready.</p>

      {error && <div className="error-banner">{error}</div>}

      <RoundStatusStrip entries={entries} freshnessItems={freshnessItems} liveStatus={liveStatus} />

      <BestOpportunitiesSection opportunities={opportunities} summary={weeklySummary} loading={opportunitiesLoading} />

      <UpcomingMatchesSection groupedByDay={groupedByDay} loading={loading} error={error} />

      <SystemStatusSection status={liveStatus} />

      <RecentResults results={results} />

      <Disclaimer />
    </main>
  );
}

export default DashboardPage;
