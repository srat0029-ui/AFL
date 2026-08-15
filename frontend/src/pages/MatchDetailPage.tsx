import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import "./MatchDetailPage.css";
import Disclaimer from "../components/Disclaimer";
import OddsPanel from "../components/OddsPanel";
import PlayerStatsTable, { type Column } from "../components/PlayerStatsTable";
import { fetchMatch, fetchMatchPlayers, fetchPredictions, type MatchPlayers, type MatchPredictions, type MatchSummary } from "../api/client";

// Round/opponent are redundant on a page already scoped to one match.
const MATCH_PLAYER_COLUMNS: Column[] = [
  { key: "disposals", label: "DI" },
  { key: "kicks", label: "KI" },
  { key: "handballs", label: "HB" },
  { key: "marks", label: "MK" },
  { key: "tackles", label: "TK" },
  { key: "goals", label: "GL" },
  { key: "behinds", label: "BH" },
];

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function MatchDetailPage() {
  const { matchId } = useParams<{ matchId: string }>();
  const id = Number(matchId);

  const [match, setMatch] = useState<MatchSummary | null>(null);
  const [predictions, setPredictions] = useState<MatchPredictions | null>(null);
  const [players, setPlayers] = useState<MatchPlayers | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isFinite(id)) return;
    setLoading(true);
    Promise.all([fetchMatch(id), fetchPredictions(id), fetchMatchPlayers(id)])
      .then(([matchData, predictionsData, playersData]) => {
        setMatch(matchData);
        setPredictions(predictionsData);
        setPlayers(playersData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load match"))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <main className="match-detail-page">
        <p className="hint">Loading…</p>
      </main>
    );
  }

  if (error || !match) {
    return (
      <main className="match-detail-page">
        <div className="match-detail-page__error">{error ?? "Match not found."}</div>
        <Link to="/" className="back-link">
          &larr; Back to dashboard
        </Link>
      </main>
    );
  }

  return (
    <main className="match-detail-page">
      <Link to="/" className="back-link">
        &larr; Back to dashboard
      </Link>

      <header className="match-header">
        <div className="match-header__teams">
          <span className="match-header__swatch" style={{ background: match.home_team.primary_colour ?? "#666" }} />
          <h1>
            {match.home_team.name} vs {match.away_team.name}
          </h1>
          <span className="match-header__swatch" style={{ background: match.away_team.primary_colour ?? "#666" }} />
        </div>
        <p className="hint">
          {formatKickoff(match.scheduled_start)}
          {match.venue ? ` · ${match.venue.name}` : ""} · Round {match.round_number}, {match.season_year}
        </p>
        {match.status === "completed" && (
          <p className="match-header__result">
            Final score: {match.home_team.short_name} {match.home_score} — {match.away_score}{" "}
            {match.away_team.short_name}
          </p>
        )}
      </header>

      {predictions ? (
        <section className="model-panel">
          <h2>Model probabilities</h2>

          <div className="model-panel__grid">
            <div className="model-stat">
              <span className="model-stat__label">Elo win probability</span>
              <span className="model-stat__value">
                {(predictions.elo_home_win_probability * 100).toFixed(1)}% / {(100 - predictions.elo_home_win_probability * 100).toFixed(1)}%
              </span>
              <span className="model-stat__hint">{match.home_team.short_name} / {match.away_team.short_name}</span>
            </div>

            <div className="model-stat">
              <span className="model-stat__label">Poisson win / draw / away</span>
              <span className="model-stat__value">
                {(predictions.poisson_home_win_probability * 100).toFixed(1)}% /{" "}
                {(predictions.poisson_draw_probability * 100).toFixed(1)}% /{" "}
                {(predictions.poisson_away_win_probability * 100).toFixed(1)}%
              </span>
            </div>

            <div className="model-stat">
              <span className="model-stat__label">Expected scoreline</span>
              <span className="model-stat__value">
                {predictions.poisson_home_expected_score.toFixed(0)} — {predictions.poisson_away_expected_score.toFixed(0)}
              </span>
              <span className="model-stat__hint">
                Total {predictions.poisson_expected_total_points.toFixed(0)}, margin{" "}
                {predictions.poisson_expected_margin >= 0 ? "+" : ""}
                {predictions.poisson_expected_margin.toFixed(0)} ({match.home_team.short_name})
              </span>
            </div>
          </div>

          <p className="model-panel__note">
            Elo is the primary match-winner model; Poisson independently models each team's expected score and is
            used for line/total markets below. Where they disagree significantly, confidence in any edge found is
            reduced — see the odds table below.
          </p>
        </section>
      ) : (
        <section className="model-panel">
          <p className="hint">
            No model predictions available yet — run <code>elo_cli</code> and <code>poisson_cli</code> (see the
            README).
          </p>
        </section>
      )}

      <OddsPanel matchId={match.id} homeTeamName={match.home_team.name} awayTeamName={match.away_team.name} />

      {players && (players.home_team_players.length > 0 || players.away_team_players.length > 0) && (
        <section className="backtest-panel">
          <h2>Player statistics</h2>
          <h3>{match.home_team.name}</h3>
          <PlayerStatsTable games={players.home_team_players} showPlayerColumn columns={MATCH_PLAYER_COLUMNS} />
          <h3>{match.away_team.name}</h3>
          <PlayerStatsTable games={players.away_team_players} showPlayerColumn columns={MATCH_PLAYER_COLUMNS} />
        </section>
      )}

      <Disclaimer />
    </main>
  );
}

export default MatchDetailPage;
