import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import "./PlayerProfilePage.css";
import PlayerStatsTable from "../components/PlayerStatsTable";
import { DisposalProjectionTable, GoalProjectionTable } from "../components/ProjectionTable";
import PageHeader from "../components/ui/PageHeader";
import FilterChips from "../components/ui/FilterChips";
import { StatTile, StatTileRow } from "../components/ui/StatTile";
import EmptyState from "../components/ui/EmptyState";
import Skeleton from "../components/ui/Skeleton";
import {
  fetchOpportunityTiers,
  fetchPlayerForm,
  fetchPlayerProjection,
  type BestOpportunity,
  type PlayerForm,
  type PlayerGameStat,
  type PlayerProjection,
} from "../api/client";

const CONFIDENCE_LABELS: Record<string, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

type StatKey = "disposals" | "goals";
type RangeKey = "5" | "10" | "20";

const STAT_OPTIONS: { value: StatKey; label: string }[] = [
  { value: "disposals", label: "Disposals" },
  { value: "goals", label: "Goals" },
];

const RANGE_OPTIONS: { value: RangeKey; label: string }[] = [
  { value: "5", label: "L5" },
  { value: "10", label: "L10" },
  { value: "20", label: "L20" },
];

const MILESTONES: Record<StatKey, number[]> = {
  disposals: [20, 25, 30],
  goals: [1, 2, 3],
};

function num(value: number | undefined | null, digits = 1): string {
  return value === undefined || value === null ? "—" : value.toFixed(digits);
}

function confidenceClass(tier: string): string {
  return tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data");
}

function average(games: PlayerGameStat[], stat: StatKey): number | null {
  const values = games.map((g) => g[stat]).filter((v): v is number => v !== null && v !== undefined);
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function hitRate(games: PlayerGameStat[], stat: StatKey, threshold: number): number | null {
  const values = games.map((g) => g[stat]).filter((v): v is number => v !== null && v !== undefined);
  if (values.length === 0) return null;
  return values.filter((v) => v >= threshold).length / values.length;
}

/** A readable bar chart of a player's real recent-game values for the
 * selected stat — oldest to newest, left to right, with the sample's own
 * average drawn as a reference line. No smoothing, no invented games. */
function FormChart({ games, stat }: { games: PlayerGameStat[]; stat: StatKey }) {
  const chronological = [...games].reverse();
  const values = chronological.map((g) => g[stat] ?? 0);
  const max = Math.max(1, ...values);
  const avg = average(games, stat);

  if (chronological.length === 0) {
    return <EmptyState title="No recent games" description="This player has no logged games in this range yet." />;
  }

  return (
    <div className="form-chart">
      {avg !== null && (
        <div className="form-chart__avg-line" style={{ bottom: `${(avg / max) * 100}%` }}>
          <span className="form-chart__avg-label">avg {num(avg)}</span>
        </div>
      )}
      <div className="form-chart__bars">
        {chronological.map((g) => {
          const value = g[stat];
          return (
            <div
              className="form-chart__bar-wrap"
              key={`${g.player_id}-${g.match_id}`}
              title={`R${g.round_number} vs ${g.opponent_team?.short_name ?? "?"}: ${value ?? "—"} ${stat}`}
            >
              <div className="form-chart__bar" style={{ height: `${((value ?? 0) / max) * 100}%` }} />
              <span className="form-chart__label">R{g.round_number}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Compact bookmaker-market row for this player — the same opportunity data
// product pages already surface elsewhere (Best Available, Weekly Review),
// just filtered down to this one player rather than a new data source.
function MarketsTable({ markets }: { markets: BestOpportunity[] }) {
  if (markets.length === 0) {
    return <EmptyState title="No active bookmaker markets" description="There's no live bookmaker price for this player right now." />;
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
  const [stat, setStat] = useState<StatKey>("disposals");
  const [range, setRange] = useState<RangeKey>("10");

  function load() {
    if (!Number.isFinite(id)) return;
    setLoading(true);
    setError(null);
    Promise.all([fetchPlayerForm(id, Number(range)), fetchPlayerProjection(id)])
      .then(([formData, projectionData]) => {
        setForm(formData);
        setProjection(projectionData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load player"))
      .finally(() => setLoading(false));
  }

  useEffect(load, [id, range]);

  useEffect(() => {
    if (!Number.isFinite(id)) return;
    fetchOpportunityTiers({ marketScope: "player" })
      .then((tiers) => setMarkets(tiers.all_available.filter((o) => o.player_id === id)))
      .catch(() => setMarkets([])); // markets are supplementary — a failure here shouldn't block the rest of the profile
  }, [id]);

  const milestoneStats = useMemo(() => {
    if (!form) return [];
    return MILESTONES[stat].map((t) => ({ threshold: t, rate: hitRate(form.recent_games, stat, t) }));
  }, [form, stat]);

  if (loading && !form) {
    return (
      <main className="player-profile-page">
        <Skeleton width="35%" height="1.8rem" />
        <div style={{ height: 14 }} />
        <Skeleton width="100%" height="8rem" />
      </main>
    );
  }

  if (error || !form) {
    return (
      <main className="player-profile-page">
        <div className="error-banner">{error ?? "Player not found."}</div>
        <Link to="/players" className="back-link">
          &larr; Back to players
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
  const recentAverage = average(recentGames, stat);

  return (
    <main className="player-profile-page">
      <Link to="/players" className="back-link">
        &larr; Back to players
      </Link>

      <PageHeader
        eyebrow={player.current_team?.name}
        title={player.display_name}
        description={
          player.is_active === false ? (
            <span className="chip chip--neutral">Inactive</span>
          ) : (
            "Recent form, upcoming projection, and how this player's numbers change with context."
          )
        }
      />

      {(headlineProjection || headlineGoals) && (
        <StatTileRow>
          {headlineProjection && (
            <StatTile
              label="Next match — projected disposals"
              value={num(headlineProjection.expected)}
              meta={`50% range ${headlineProjection.interval_50[0].toFixed(0)}–${headlineProjection.interval_50[1].toFixed(0)}`}
            />
          )}
          {headlineGoals && (
            <StatTile label="Next match — projected goals" value={num(headlineGoals.expected, 2)} meta={headlineGoals.confidence_tier.replace(/_/g, " ")} />
          )}
        </StatTileRow>
      )}
      {!headlineProjection && !headlineGoals && (
        <EmptyState title="No upcoming projection" description="This player has no scheduled match with a generated projection right now." />
      )}

      <section className="card">
        <div className="section-row">
          <h2 className="section-title">Recent form</h2>
        </div>
        <div className="player-profile-page__controls">
          <FilterChips label="Stat" options={STAT_OPTIONS} value={stat} onChange={setStat} />
          <FilterChips label="Range" options={RANGE_OPTIONS.map((o) => ({ ...o, disabled: loading }))} value={range} onChange={setRange} />
        </div>

        <StatTileRow>
          <StatTile label={`Average, last ${recentGames.length}`} value={num(recentAverage)} />
          {milestoneStats.map((m) => (
            <StatTile key={m.threshold} label={`${m.threshold}+ hit rate`} value={m.rate === null ? "—" : `${(m.rate * 100).toFixed(0)}%`} meta={`${recentGames.length} games`} />
          ))}
        </StatTileRow>

        <FormChart games={recentGames} stat={stat} />

        <details className="disclosure player-profile-page__evidence-toggle">
          <summary>View match-by-match evidence</summary>
          <PlayerStatsTable games={recentGames} showPlayerColumn={false} />
        </details>
      </section>

      {projection && (projection.disposals || projection.goals) && (
        <section className="card">
          <h2 className="section-title">Upcoming projection detail</h2>
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
        <div className="section-row">
          <h2 className="section-title">Compare with teammate or opponent</h2>
          <Link to="/player-research" className="section-row__link">
            Open Player Research →
          </Link>
        </div>
        <p className="hint">
          See how {player.display_name}'s numbers actually change with a specific teammate on the field, or against a
          specific opponent — with sample sizes and confidence shown honestly.
        </p>
      </section>

      {(hasContextFlags || latestSeason || seasonAverages.length > 1) && (
        <details className="disclosure player-profile-page__more">
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
