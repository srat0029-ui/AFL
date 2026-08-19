import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import "./PlayerProfilePage.css";
import PlayerStatsTable from "../components/PlayerStatsTable";
import { DisposalProjectionTable, GoalProjectionTable } from "../components/ProjectionTable";
import { fetchPlayerForm, fetchPlayerProjection, type PlayerForm, type PlayerProjection } from "../api/client";

function num(value: number | undefined, digits = 1): string {
  return value === undefined ? "—" : value.toFixed(digits);
}

function FormChart({ games }: { games: PlayerForm["recent_games"] }) {
  // oldest-to-newest left-to-right, matching how a "recent form" run chart reads
  const chronological = [...games].reverse();
  const maxDisposals = Math.max(1, ...chronological.map((g) => g.disposals ?? 0));

  return (
    <div className="form-chart">
      {chronological.map((g) => (
        <div className="form-chart__bar-wrap" key={`${g.player_id}-${g.match_id}`} title={`R${g.round_number} vs ${g.opponent_team?.short_name ?? "?"}: ${g.disposals ?? "—"} disposals`}>
          <div className="form-chart__bar" style={{ height: `${((g.disposals ?? 0) / maxDisposals) * 100}%` }} />
          <span className="form-chart__label">R{g.round_number}</span>
        </div>
      ))}
      {chronological.length === 0 && <p className="empty-state">No recent games.</p>}
    </div>
  );
}

function PlayerProfilePage() {
  const { playerId } = useParams<{ playerId: string }>();
  const id = Number(playerId);

  const [form, setForm] = useState<PlayerForm | null>(null);
  const [projection, setProjection] = useState<PlayerProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isFinite(id)) return;
    setLoading(true);
    Promise.all([fetchPlayerForm(id, 10), fetchPlayerProjection(id)])
      .then(([formData, projectionData]) => {
        setForm(formData);
        setProjection(projectionData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load player"))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <main className="player-profile-page">
        <p className="loading-state">Loading…</p>
      </main>
    );
  }

  if (error || !form) {
    return (
      <main className="player-profile-page">
        <div className="error-banner">{error ?? "Player not found."}</div>
        <Link to="/" className="back-link">
          &larr; Back to dashboard
        </Link>
      </main>
    );
  }

  const { player, recent_games: recentGames, season_averages: seasonAverages } = form;
  const latestSeason = seasonAverages[0];

  return (
    <main className="player-profile-page">
      <Link to="/" className="back-link">
        &larr; Back to dashboard
      </Link>

      <header className="player-header">
        <h1>{player.display_name}</h1>
        <p className="hint">
          {player.current_team ? player.current_team.name : "Team unknown"}
          {player.is_active === false && " · Inactive"}
        </p>
      </header>

      {projection && (projection.disposals || projection.goals) && (
        <section className="backtest-panel">
          <h2>Upcoming projection</h2>
          <p className="hint">
            Projected outcome for this player's next upcoming match, from the promoted disposal/goal models — not a
            guarantee, and distinct from the recent-form averages below.
          </p>
          {projection.disposals && (
            <>
              <h3>Disposals</h3>
              <DisposalProjectionTable rows={[projection.disposals]} />
            </>
          )}
          {projection.goals && (
            <>
              <h3>Goals</h3>
              <GoalProjectionTable rows={[projection.goals]} />
            </>
          )}

          {(projection.current_context.length > 0 ||
            projection.tog_volatile ||
            projection.substitute_risk ||
            projection.returning_from_injury ||
            projection.role_note) && (
            <div className="player-context-block">
              <h3>Selection &amp; context</h3>
              <ul className="player-context-block__list">
                {projection.substitute_risk && <li>Flagged as a substitute risk in the current lineup entry.</li>}
                {projection.returning_from_injury && <li>Marked as returning from injury in the current lineup entry.</li>}
                {projection.role_note && <li>Role note: {projection.role_note}</li>}
                {projection.tog_volatile && <li>Recent time-on-ground% has been volatile over the last 5 games.</li>}
                {projection.current_context.map((c) => (
                  <li key={c.id}>
                    {c.context_type_label}: {c.summary}{" "}
                    <span className="hint">
                      ({c.source}, {c.freshness})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {latestSeason && (
        <section className="backtest-panel">
          <h2>{latestSeason.season_year} season averages ({latestSeason.games_played} games)</h2>
          <div className="backtest-overall">
            <div className="backtest-stat">
              <span className="backtest-stat__label">Disposals</span>
              <span className="backtest-stat__value">{num(latestSeason.averages.disposals)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Kicks</span>
              <span className="backtest-stat__value">{num(latestSeason.averages.kicks)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Handballs</span>
              <span className="backtest-stat__value">{num(latestSeason.averages.handballs)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Marks</span>
              <span className="backtest-stat__value">{num(latestSeason.averages.marks)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Tackles</span>
              <span className="backtest-stat__value">{num(latestSeason.averages.tackles)}</span>
            </div>
            <div className="backtest-stat">
              <span className="backtest-stat__label">Goals</span>
              <span className="backtest-stat__value">{num(latestSeason.averages.goals)}</span>
            </div>
          </div>
        </section>
      )}

      <section className="backtest-panel">
        <h2>Recent form — disposals, last {recentGames.length} games</h2>
        <FormChart games={recentGames} />
        <h3>Recent games</h3>
        <PlayerStatsTable games={recentGames} showPlayerColumn={false} />
      </section>

      {seasonAverages.length > 1 && (
        <section className="backtest-panel">
          <h2>Season-by-season averages</h2>
          <div className="segment-table-scroll">
            <table className="segment-table">
              <thead>
                <tr>
                  <th>Season</th>
                  <th>Games</th>
                  <th>DI</th>
                  <th>KI</th>
                  <th>HB</th>
                  <th>MK</th>
                  <th>TK</th>
                  <th>GL</th>
                </tr>
              </thead>
              <tbody>
                {seasonAverages.map((s) => (
                  <tr key={s.season_year}>
                    <td>{s.season_year}</td>
                    <td>{s.games_played}</td>
                    <td>{num(s.averages.disposals)}</td>
                    <td>{num(s.averages.kicks)}</td>
                    <td>{num(s.averages.handballs)}</td>
                    <td>{num(s.averages.marks)}</td>
                    <td>{num(s.averages.tackles)}</td>
                    <td>{num(s.averages.goals)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}

export default PlayerProfilePage;
