import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import "./MatchDetailPage.css";
import "../pages/DashboardPage.css";
import "../pages/RealMarketTrackingPage.css";
import DataFreshnessPanel from "../components/DataFreshnessPanel";
import Disclaimer from "../components/Disclaimer";
import ExpectedLineupPanel from "../components/ExpectedLineupPanel";
import MatchContextPanel from "../components/MatchContextPanel";
import MultiBuilderView from "../components/MultiBuilderView";
import OddsPanel from "../components/OddsPanel";
import PlayerPropPanel from "../components/PlayerPropPanel";
import PlayerStatsTable, { type Column } from "../components/PlayerStatsTable";
import { DisposalProjectionTable, GoalProjectionTable } from "../components/ProjectionTable";
import { MarketMovementTable, QuoteHistoryDrawer } from "./RealMarketTrackingPage";
import {
  fetchDiversifiedOpportunities,
  fetchMarketMovement,
  fetchMatch,
  fetchMatchPlayers,
  fetchMatchProjections,
  fetchPredictions,
  type DiversifiedOpportunity,
  type MarketMovement,
  type MatchPlayers,
  type MatchPredictions,
  type MatchProjections,
  type MatchSummary,
} from "../api/client";
import { formatCountdown, formatFullDateTime } from "../lib/datetime";

function MatchOpportunitiesSection({ opportunities, loading }: { opportunities: DiversifiedOpportunity[]; loading: boolean }) {
  return (
    <section className="dashboard-section">
      <h2 className="section-title">Best player opportunities</h2>
      {loading && <p className="loading-state">Loading…</p>}
      {!loading && opportunities.length === 0 && (
        <p className="empty-state">No player opportunities currently pass the quality gates for this match.</p>
      )}
      {!loading && opportunities.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Selection</th>
                <th className="num">Price</th>
                <th className="num">Edge</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {opportunities.map((o) => (
                <tr key={`${o.opportunity_type}-${o.player_id ?? o.selection}-${o.threshold ?? o.line_value}`}>
                  <td>
                    {o.label}
                    {o.alternate_lines.length > 0 && (
                      <span className="chip chip--accent" style={{ marginLeft: "0.4rem" }}>
                        +{o.alternate_lines.length}
                      </span>
                    )}
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

function MatchMovementSection({ movements }: { movements: MarketMovement[] }) {
  const [selected, setSelected] = useState<MarketMovement | null>(null);
  if (movements.length === 0) return null;
  return (
    <>
      <MarketMovementTable movements={movements} onSelect={setSelected} title="Market movement for this match" />
      {selected && <QuoteHistoryDrawer movement={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

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

const formatKickoff = formatFullDateTime;

function MatchDetailPage() {
  const { matchId } = useParams<{ matchId: string }>();
  const id = Number(matchId);

  const [match, setMatch] = useState<MatchSummary | null>(null);
  const [predictions, setPredictions] = useState<MatchPredictions | null>(null);
  const [players, setPlayers] = useState<MatchPlayers | null>(null);
  const [projections, setProjections] = useState<MatchProjections | null>(null);
  const [projectionsTab, setProjectionsTab] = useState<"disposals" | "goals">("disposals");
  const [opportunities, setOpportunities] = useState<DiversifiedOpportunity[]>([]);
  const [opportunitiesLoading, setOpportunitiesLoading] = useState(true);
  const [movements, setMovements] = useState<MarketMovement[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function loadProjections() {
    if (!Number.isFinite(id)) return;
    fetchMatchProjections(id).then(setProjections).catch(() => setProjections(null));
  }

  useEffect(() => {
    if (!Number.isFinite(id)) return;
    setLoading(true);
    Promise.all([fetchMatch(id), fetchPredictions(id), fetchMatchPlayers(id), fetchMatchProjections(id)])
      .then(([matchData, predictionsData, playersData, projectionsData]) => {
        setMatch(matchData);
        setPredictions(predictionsData);
        setPlayers(playersData);
        setProjections(projectionsData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load match"))
      .finally(() => setLoading(false));

    // Diversified so a single hot player's alternate lines can't dominate
    // this match's list — same guarantee as the Dashboard/Prop Insights view.
    fetchDiversifiedOpportunities({ view: "overall", marketScope: "player", limit: null })
      .then((r) => setOpportunities(r.opportunities.filter((o) => o.match_id === id)))
      .catch(() => setOpportunities([]))
      .finally(() => setOpportunitiesLoading(false));

    fetchMarketMovement({ matchId: id })
      .then(setMovements)
      .catch(() => setMovements([]));
  }, [id]);

  if (loading) {
    return (
      <main className="match-detail-page">
        <p className="loading-state">Loading…</p>
      </main>
    );
  }

  if (error || !match) {
    return (
      <main className="match-detail-page">
        <div className="error-banner">{error ?? "Match not found."}</div>
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
          <h1 className="page-title match-header__title">
            {match.home_team.name} vs {match.away_team.name}
          </h1>
          <span className="match-header__swatch" style={{ background: match.away_team.primary_colour ?? "#666" }} />
        </div>
        <p className="hint">
          {formatKickoff(match.scheduled_start)}
          {match.status === "scheduled" && ` (${formatCountdown(match.scheduled_start)})`}
          {match.venue ? ` · ${match.venue.name}` : ""} · Round {match.round_number}, {match.season_year}
        </p>
        {match.status === "completed" && (
          <p className="match-header__result">
            Final score: {match.home_team.short_name} {match.home_score} — {match.away_score}{" "}
            {match.away_team.short_name}
          </p>
        )}
      </header>

      {match.status === "scheduled" && (
        <details className="system-status">
          <summary>
            <span className="section-title">Data freshness</span>
          </summary>
          <div className="system-status__body">
            <DataFreshnessPanel />
          </div>
        </details>
      )}

      {predictions ? (
        <section className="model-panel">
          <h2 className="section-title">Model vs market</h2>

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

      {match.status === "scheduled" && <MatchOpportunitiesSection opportunities={opportunities} loading={opportunitiesLoading} />}

      {match.status === "scheduled" && <MultiBuilderView matchId={match.id} />}

      {match.status === "scheduled" && (
        <>
          <ExpectedLineupPanel
            matchId={match.id}
            homeTeamId={match.home_team.id}
            awayTeamId={match.away_team.id}
            homeTeamName={match.home_team.name}
            awayTeamName={match.away_team.name}
            onChanged={loadProjections}
          />

          <MatchContextPanel
            matchId={match.id}
            homeTeamId={match.home_team.id}
            awayTeamId={match.away_team.id}
            homeTeamName={match.home_team.name}
            awayTeamName={match.away_team.name}
          />

          <section className="backtest-panel">
            <h2 className="section-title">Player projections</h2>
            <p className="hint">
              Projected disposals and goals for players marked expected-in or uncertain above, from the promoted
              disposal/goal models. Not live betting advice.
            </p>
            <div className="projection-tabs">
              <button
                type="button"
                className={projectionsTab === "disposals" ? "projection-tabs__btn projection-tabs__btn--active" : "projection-tabs__btn"}
                onClick={() => setProjectionsTab("disposals")}
              >
                Disposals
              </button>
              <button
                type="button"
                className={projectionsTab === "goals" ? "projection-tabs__btn projection-tabs__btn--active" : "projection-tabs__btn"}
                onClick={() => setProjectionsTab("goals")}
              >
                Goals
              </button>
            </div>
            {projections === null && (
              <p className="hint">
                No projections generated yet — run <code>python -m app.player_modelling.cli project-upcoming</code>{" "}
                after setting expected lineups.
              </p>
            )}
            {projections && projectionsTab === "disposals" && <DisposalProjectionTable rows={projections.disposals} />}
            {projections && projectionsTab === "goals" && <GoalProjectionTable rows={projections.goals} />}
          </section>

          <PlayerPropPanel matchId={match.id} />
        </>
      )}

      <MatchMovementSection movements={movements} />

      {players && (players.home_team_players.length > 0 || players.away_team_players.length > 0) && (
        <section className="backtest-panel">
          <h2 className="section-title">Player statistics</h2>
          <h3 className="match-detail-subheading">{match.home_team.name}</h3>
          <PlayerStatsTable games={players.home_team_players} showPlayerColumn columns={MATCH_PLAYER_COLUMNS} />
          <h3 className="match-detail-subheading">{match.away_team.name}</h3>
          <PlayerStatsTable games={players.away_team_players} showPlayerColumn columns={MATCH_PLAYER_COLUMNS} />
        </section>
      )}

      <Disclaimer />
    </main>
  );
}

export default MatchDetailPage;
