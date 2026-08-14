import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "./DashboardPage.css";
import Disclaimer from "../components/Disclaimer";
import { fetchDashboard, type DashboardEntry } from "../api/client";

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

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function DashboardPage() {
  const [entries, setEntries] = useState<DashboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDashboard()
      .then(setEntries)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load dashboard"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="dashboard-page">
      <h1>Upcoming matches</h1>
      <p className="subtitle">Model probabilities and potential edges for upcoming AFL fixtures.</p>

      {error && <div className="dashboard-page__error">{error}</div>}
      {loading && <p className="hint">Loading…</p>}

      {!loading && !error && entries.length === 0 && (
        <p className="hint">
          No upcoming fixtures with model predictions available. Make sure you've run the data ingestion and modelling
          CLIs (see the README).
        </p>
      )}

      <div className="match-card-grid">
        {entries.map((entry) => {
          const homeWinPct = entry.predictions.elo_home_win_probability * 100;
          const awayWinPct = 100 - homeWinPct;
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
            </Link>
          );
        })}
      </div>

      <Disclaimer />
    </main>
  );
}

export default DashboardPage;
