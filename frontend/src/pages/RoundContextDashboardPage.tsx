import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "./RoundContextDashboardPage.css";
import { fetchContextDashboard, refreshWeather, type RoundContextDashboard, type RoundContextMatch } from "../api/client";
import { formatCompactDateTime } from "../lib/datetime";

const ANNOUNCEMENT_LABELS: Record<string, string> = {
  teams_not_announced: "Teams not announced",
  squad_announced: "Squad announced",
  final_team_confirmed: "Final team confirmed",
};

function MatchRow({ match }: { match: RoundContextMatch }) {
  const hasMajorChange = match.n_confirmed_out > 0 || match.n_substitutes > 0;
  return (
    <tr className={hasMajorChange ? "round-context-table__row round-context-table__row--flagged" : "round-context-table__row"}>
      <td>
        <Link to={`/matches/${match.match_id}`}>
          {match.home_team_name} v {match.away_team_name}
        </Link>
      </td>
      <td>{formatCompactDateTime(match.scheduled_start)}</td>
      <td>{ANNOUNCEMENT_LABELS[match.lineup_announcement_state] ?? match.lineup_announcement_state}</td>
      <td>{match.n_confirmed_in}</td>
      <td className={match.n_confirmed_out > 0 ? "round-context-table__cell--warn" : undefined}>{match.n_confirmed_out}</td>
      <td className={match.n_substitutes > 0 ? "round-context-table__cell--warn" : undefined}>{match.n_substitutes}</td>
      <td>{match.n_other_context_items}</td>
      <td>
        {match.weather ? (
          <span>
            {match.weather.temperature_c?.toFixed(0)}°C · Rain {match.weather.rain_probability_pct?.toFixed(0)}% · Wind{" "}
            {match.weather.wind_gust_kph?.toFixed(0)}km/h
            {match.weather.severe_weather_warning && <span className="round-context-table__severe"> ⚠</span>}
          </span>
        ) : (
          <span className="hint">No forecast yet</span>
        )}
      </td>
      <td className={match.n_stale_projections > 0 ? "round-context-table__cell--warn" : undefined}>{match.n_stale_projections || "—"}</td>
    </tr>
  );
}

function RoundContextDashboardPage() {
  const [data, setData] = useState<RoundContextDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  function load() {
    setLoading(true);
    fetchContextDashboard()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load context dashboard"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  async function handleRefreshWeather() {
    setRefreshing(true);
    try {
      await refreshWeather();
      load();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <main className="round-context-page">
      <header className="round-context-page__header">
        <div>
          <h1>Current Round Context</h1>
          <p className="hint">
            A pre-bet checklist for the current round: confirmed lineups, major outs/late changes, weather, and any
            player projections that may not yet reflect the latest context.
          </p>
        </div>
        <button type="button" onClick={handleRefreshWeather} disabled={refreshing} className="round-context-page__refresh">
          {refreshing ? "Fetching weather…" : "Refresh weather forecasts"}
        </button>
      </header>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="round-context-page__error">{error}</div>}

      {data && data.matches.length === 0 && <p className="hint">No upcoming matches found.</p>}

      {data && data.matches.length > 0 && (
        <>
          <p className="hint">
            Round {data.round_number}, {data.season_year} — {data.matches.length} match(es)
          </p>
          <div className="round-context-table-scroll">
            <table className="round-context-table">
              <thead>
                <tr>
                  <th>Match</th>
                  <th>Kickoff</th>
                  <th>Lineup status</th>
                  <th>Confirmed in</th>
                  <th>Confirmed out</th>
                  <th>Substitutes</th>
                  <th>Other context</th>
                  <th>Weather</th>
                  <th>Stale projections</th>
                </tr>
              </thead>
              <tbody>
                {data.matches.map((m) => (
                  <MatchRow key={m.match_id} match={m} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </main>
  );
}

export default RoundContextDashboardPage;
