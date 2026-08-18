import { useEffect, useState } from "react";
import "./LiveStatusPage.css";
import {
  fetchLiveStatus,
  type LiveCycleRun,
  type LiveCycleStepStatus,
  type MatchCoverageStatus,
  type MatchSimpleStatus,
  type LiveStatusReport,
} from "../api/client";
import { formatCompactDateTime } from "../lib/datetime";

const STATUS_LABELS: Record<MatchSimpleStatus, string> = {
  waiting_for_teams: "Waiting for teams",
  waiting_for_bookmaker_markets: "Waiting for bookmaker markets",
  stale: "Stale",
  ready: "Ready",
  odds_available: "Odds available",
  completed_awaiting_player_stats: "Completed / awaiting player stats",
  settled: "Settled",
  completed_no_market_data: "Completed (no market data collected)",
};

const STEP_STATUS_LABELS: Record<LiveCycleStepStatus, string> = {
  success: "OK",
  warning: "Warning",
  recoverable_failure: "Recoverable failure",
  blocking_failure: "Blocking failure",
};

const CLI_COMMANDS = [
  { command: "python -m app.player_modelling.cli run-live-cycle", note: "The one command to run through an AFL round — orchestrates everything below in order." },
  { command: "python -m app.player_modelling.cli refresh-prop-odds", note: "Odds refresh + observation creation only (quota-aware, match-time-aware)." },
  { command: "python -m app.player_modelling.cli settle-props", note: "Settle completed matches' observations only." },
  { command: "python -m app.player_modelling.cli refresh-live", note: "Detect and regenerate stale projections only." },
];

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return formatCompactDateTime(iso);
}

function MatchRow({ match }: { match: MatchCoverageStatus }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <tr className="live-status-table__row" onClick={() => setExpanded((e) => !e)}>
        <td>
          {match.home_team_name} v {match.away_team_name}
        </td>
        <td>{formatDateTime(match.scheduled_start)}</td>
        <td>
          <span className={`live-status-badge live-status-badge--${match.simple_status}`}>{STATUS_LABELS[match.simple_status]}</span>
        </td>
        <td>{match.lineup_announcement_state.replace(/_/g, " ")}</td>
        <td>{match.projections_generated ? "Yes" : "No"}</td>
        <td>{match.bookmaker_event_exists ? "Yes" : "No"}</td>
        <td>{match.bookmakers_observed.join(", ") || "—"}</td>
        <td>
          <span className={match.disposals_available ? "market-flag market-flag--yes" : "market-flag market-flag--no"}>
            {match.disposals_available ? "Disposals" : "—"}
          </span>{" "}
          <span className={match.goals_available ? "market-flag market-flag--yes" : "market-flag market-flag--no"}>
            {match.goals_available ? "Goals" : "—"}
          </span>
        </td>
        <td>{match.n_quotes}</td>
        <td>{match.unique_player_count}</td>
        <td>{formatDateTime(match.last_odds_refresh)}</td>
        <td>
          {match.n_observations} ({match.n_observations_settled} settled)
        </td>
      </tr>
      {expanded && (
        <tr className="live-status-table__detail-row">
          <td colSpan={12}>
            <strong>Diagnosis:</strong> {match.diagnosis.detail}{" "}
            {match.diagnosis.would_be_skipped_this_cycle ? "(within refresh interval — considered fresh)" : "(due for refresh now)"} ·{" "}
            {match.diagnosis.hours_to_kickoff >= 0 ? `${match.diagnosis.hours_to_kickoff.toFixed(1)}h to kickoff` : "in progress or completed"}
          </td>
        </tr>
      )}
    </>
  );
}

function RunCard({ run }: { run: LiveCycleRun }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="live-status-card">
      <div className="live-status-run__header" onClick={() => setExpanded((e) => !e)}>
        <span className={`live-status-badge live-status-badge--run-${run.overall_status}`}>{run.overall_status.toUpperCase()}</span>
        <span>{formatDateTime(run.run_at)}</span>
        <span className="hint">
          {run.quotes_added} quotes · {run.observations_added} observations · {run.observations_settled} settled
          {run.odds_credits_consumed !== null && ` · ${run.odds_credits_consumed} odds credit(s) used`}
        </span>
      </div>
      {expanded && (
        <div className="live-status-run__steps">
          {run.steps.map((s, i) => (
            <div key={i} className={`live-status-run__step live-status-run__step--${s.status}`}>
              <span className="live-status-run__step-status">{STEP_STATUS_LABELS[s.status]}</span>
              <span className="live-status-run__step-name">{s.step}</span>
              <span className="hint">{s.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function LiveStatusPage() {
  const [report, setReport] = useState<LiveStatusReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchLiveStatus()
      .then(setReport)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load live status"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="live-status-page">
      <header className="live-status-page__header">
        <h1>Live Status</h1>
        <p className="hint">
          An operational control panel, not a research page — shows what's already been collected and what still
          needs attention for the current upcoming round. Nothing on this page ever triggers an external request
          (paid or free): refreshing data is always an explicit command you run yourself (below).
        </p>
      </header>

      {loading && <p className="hint">Loading…</p>}
      {error && <div className="live-status-page__error">{error}</div>}

      {!loading && !error && report && (
        <>
          <div className="live-status-card">
            <h3>Round summary</h3>
            <div className="live-status-grid">
              <div><span className="live-status-grid__label">Upcoming matches</span><span className="live-status-grid__value">{report.round_summary.n_upcoming_matches}</span></div>
              <div><span className="live-status-grid__label">With projections</span><span className="live-status-grid__value">{report.round_summary.n_matches_with_projections}</span></div>
              <div><span className="live-status-grid__label">With bookmaker events</span><span className="live-status-grid__value">{report.round_summary.n_matches_with_bookmaker_events}</span></div>
              <div><span className="live-status-grid__label">With prop markets</span><span className="live-status-grid__value">{report.round_summary.n_matches_with_prop_markets}</span></div>
              <div><span className="live-status-grid__label">Unique players w/ markets</span><span className="live-status-grid__value">{report.round_summary.n_unique_players_with_markets}</span></div>
              <div><span className="live-status-grid__label">Real quotes stored</span><span className="live-status-grid__value">{report.round_summary.n_real_quotes_stored}</span></div>
              <div><span className="live-status-grid__label">Real observations stored</span><span className="live-status-grid__value">{report.round_summary.n_real_observations_stored}</span></div>
              <div><span className="live-status-grid__label">Confirmed lineups</span><span className="live-status-grid__value">{report.round_summary.n_confirmed_lineups}</span></div>
              <div><span className="live-status-grid__label">Placeholder/uncertain lineups</span><span className="live-status-grid__value">{report.round_summary.n_placeholder_or_uncertain_lineups}</span></div>
            </div>
          </div>

          <div className="live-status-card">
            <h3>Match readiness</h3>
            <p className="hint">Click a row for the full diagnosis of why a match is or isn't showing odds yet.</p>
            <div className="live-status-table-scroll">
              <table className="live-status-table">
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Kickoff</th>
                    <th>Status</th>
                    <th>Lineup</th>
                    <th>Projections</th>
                    <th>Bookmaker event</th>
                    <th>Bookmakers</th>
                    <th>Markets</th>
                    <th>Quotes</th>
                    <th>Players</th>
                    <th>Last odds refresh</th>
                    <th>Observations</th>
                  </tr>
                </thead>
                <tbody>
                  {report.matches.map((m) => (
                    <MatchRow key={m.match_id} match={m} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="live-status-card">
            <h3>Recent operational runs</h3>
            {report.recent_runs.length === 0 && <p className="hint">No run-live-cycle runs recorded yet.</p>}
            <div className="live-status-runs">
              {report.recent_runs.map((r) => (
                <RunCard key={r.id} run={r} />
              ))}
            </div>
          </div>

          <div className="live-status-card">
            <h3>Commands</h3>
            <p className="hint">Run these from the backend directory. Nothing on this page can trigger them for you.</p>
            <div className="live-status-commands">
              {CLI_COMMANDS.map((c) => (
                <div key={c.command} className="live-status-command">
                  <code>{c.command}</code>
                  <span className="hint">{c.note}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </main>
  );
}

export default LiveStatusPage;
