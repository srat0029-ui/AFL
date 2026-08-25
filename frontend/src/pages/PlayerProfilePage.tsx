import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import "./PlayerProfilePage.css";
import PlayerStatsTable from "../components/PlayerStatsTable";
import { DisposalProjectionTable, GoalProjectionTable } from "../components/ProjectionTable";
import {
  fetchOpportunityTiers,
  fetchPlayerForm,
  fetchPlayerProjection,
  type BestOpportunity,
  type PlayerForm,
  type PlayerProjection,
} from "../api/client";

const CONFIDENCE_LABELS: Record<string, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

function num(value: number | undefined, digits = 1): string {
  return value === undefined ? "—" : value.toFixed(digits);
}

function confidenceClass(tier: string): string {
  return tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data");
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

// Compact bookmaker-market row for this player — the same opportunity data
// product pages already surface elsewhere (Best Available, Weekly Review),
// just filtered down to this one player rather than a new data source.
function MarketsTable({ markets }: { markets: BestOpportunity[] }) {
  if (markets.length === 0) {
    return <p className="empty-state">No active bookmaker markets for this player right now.</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Market</th>
            <th className="num">Model prob.</th>
            <th className="num">Bookmaker price</th>
            <th className="num">Fair price</th>
            <th className="num">Edge</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {markets.map((m) => (
            <tr key={`${m.match_id}-${m.market_type}-${m.threshold ?? m.line_value}`}>
              <td>{m.label}</td>
              <td className="num">{(m.model_probability * 100).toFixed(1)}%</td>
              <td className="num">
                ${m.best_price.toFixed(2)} <span className="hint">{m.best_bookmaker}</span>
              </td>
              <td className="num">${m.model_fair_odds.toFixed(2)}</td>
              <td className={`num ${m.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}`}>
                {m.difference_pp >= 0 ? "+" : ""}
                {(m.difference_pp * 100).toFixed(1)}pp
              </td>
              <td>
                <span className={`confidence-badge confidence-badge--${confidenceClass(m.confidence_tier)}`}>
                  {CONFIDENCE_LABELS[m.confidence_tier] ?? m.confidence_tier}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlayerProfilePage() {
  const { playerId } = useParams<{ playerId: string }>();
  const id = Number(playerId);

  const [form, setForm] = useState<PlayerForm | null>(null);
  const [projection, setProjection] = useState<PlayerProjection | null>(null);
  const [markets, setMarkets] = useState<BestOpportunity[]>([]);
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
    fetchOpportunityTiers({ marketScope: "player" })
      .then((tiers) => setMarkets(tiers.all_available.filter((o) => o.player_id === id)))
      .catch(() => setMarkets([])); // markets are supplementary — a failure here shouldn't block the rest of the profile
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
  const headlineProjection = projection?.disposals ?? null;
  const headlineGoals = !headlineProjection ? projection?.goals ?? null : null;
  const hasContextFlags =
    !!projection &&
    (projection.current_context.length > 0 || projection.tog_volatile || projection.substitute_risk || projection.returning_from_injury || !!projection.role_note);

  return (
    <main className="player-profile-page">
      <Link to="/" className="back-link">
        &larr; Back to dashboard
      </Link>

      <header className="player-header">
        <h1 className="player-header__title">
          {player.display_name}
          <span className="player-header__team">{player.current_team ? ` · ${player.current_team.name}` : ""}</span>
          {player.is_active === false && <span className="chip chip--neutral player-header__inactive">Inactive</span>}
        </h1>
        {headlineProjection && (
          <p className="player-header__projection">
            Projection <span className="num">{num(headlineProjection.expected)}</span> disposals
          </p>
        )}
        {headlineGoals && (
          <p className="player-header__projection">
            Projection <span className="num">{num(headlineGoals.expected, 2)}</span> goals
          </p>
        )}
        {!headlineProjection && !headlineGoals && <p className="hint">No upcoming projection available for this player.</p>}
      </header>

      {projection && (projection.disposals || projection.goals) && (
        <section className="card">
          <h2 className="section-title">Upcoming projection</h2>
          {projection.disposals && (
            <>
              <h3 className="player-profile-page__subheading">Disposals</h3>
              <DisposalProjectionTable rows={[projection.disposals]} />
            </>
          )}
          {projection.goals && (
            <>
              <h3 className="player-profile-page__subheading">Goals</h3>
              <GoalProjectionTable rows={[projection.goals]} />
            </>
          )}
        </section>
      )}

      <section className="card">
        <h2 className="section-title">Bookmaker markets</h2>
        <MarketsTable markets={markets} />
      </section>

      <section className="card">
        <h2 className="section-title">Recent form — disposals, last {recentGames.length} games</h2>
        <FormChart games={recentGames} />
        <PlayerStatsTable games={recentGames} showPlayerColumn={false} />
      </section>

      {(hasContextFlags || latestSeason || seasonAverages.length > 1) && (
        <details className="player-profile-page__more">
          <summary>More detail — selection context, season averages, history</summary>
          <div className="player-profile-page__more-body">
            {hasContextFlags && projection && (
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

            {latestSeason && (
              <div className="player-profile-page__season-block">
                <h3>
                  {latestSeason.season_year} season averages ({latestSeason.games_played} games)
                </h3>
                <div className="stat-strip">
                  <div className="stat-strip__item">
                    <span className="stat-strip__label">Disposals</span>
                    <span className="stat-strip__value">{num(latestSeason.averages.disposals)}</span>
                  </div>
                  <div className="stat-strip__item">
                    <span className="stat-strip__label">Kicks</span>
                    <span className="stat-strip__value">{num(latestSeason.averages.kicks)}</span>
                  </div>
                  <div className="stat-strip__item">
                    <span className="stat-strip__label">Handballs</span>
                    <span className="stat-strip__value">{num(latestSeason.averages.handballs)}</span>
                  </div>
                  <div className="stat-strip__item">
                    <span className="stat-strip__label">Marks</span>
                    <span className="stat-strip__value">{num(latestSeason.averages.marks)}</span>
                  </div>
                  <div className="stat-strip__item">
                    <span className="stat-strip__label">Tackles</span>
                    <span className="stat-strip__value">{num(latestSeason.averages.tackles)}</span>
                  </div>
                  <div className="stat-strip__item">
                    <span className="stat-strip__label">Goals</span>
                    <span className="stat-strip__value">{num(latestSeason.averages.goals)}</span>
                  </div>
                </div>
              </div>
            )}

            {seasonAverages.length > 1 && (
              <div className="player-profile-page__season-block">
                <h3>Season-by-season averages</h3>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Season</th>
                        <th className="num">Games</th>
                        <th className="num">DI</th>
                        <th className="num">KI</th>
                        <th className="num">HB</th>
                        <th className="num">MK</th>
                        <th className="num">TK</th>
                        <th className="num">GL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {seasonAverages.map((s) => (
                        <tr key={s.season_year}>
                          <td>{s.season_year}</td>
                          <td className="num">{s.games_played}</td>
                          <td className="num">{num(s.averages.disposals)}</td>
                          <td className="num">{num(s.averages.kicks)}</td>
                          <td className="num">{num(s.averages.handballs)}</td>
                          <td className="num">{num(s.averages.marks)}</td>
                          <td className="num">{num(s.averages.tackles)}</td>
                          <td className="num">{num(s.averages.goals)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </details>
      )}
    </main>
  );
}

export default PlayerProfilePage;
