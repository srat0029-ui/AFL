import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import "./HomePage.css";
import Disclaimer from "../components/Disclaimer";
import MatchCard from "../components/ui/MatchCard";
import PlayerSearch from "../components/ui/PlayerSearch";
import EmptyState from "../components/ui/EmptyState";
import Skeleton from "../components/ui/Skeleton";
import {
  fetchDashboard,
  fetchDiversifiedOpportunities,
  fetchRecentResults,
  type DashboardEntry,
  type DiversifiedOpportunity,
  type MatchSummary,
} from "../api/client";

const HOME_MATCH_LIMIT = 6;

function TopOpportunityTeaser({ opportunities, loading }: { opportunities: DiversifiedOpportunity[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="card home-teaser">
        <Skeleton width="60%" height="0.9rem" />
        <Skeleton width="40%" height="1.6rem" />
      </div>
    );
  }
  if (opportunities.length === 0) return null;
  const top = opportunities[0];
  return (
    <Link to="/prop-insights" className="card home-teaser home-teaser--link">
      <span className="home-teaser__label">Today's strongest market insight</span>
      <span className="home-teaser__value">{top.label}</span>
      <span className="home-teaser__meta">
        Model vs. market difference: <strong className={top.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
          {top.difference_pp >= 0 ? "+" : ""}{(top.difference_pp * 100).toFixed(1)}pp
        </strong>{" "}
        · best price ${top.best_price.toFixed(2)} ({top.best_bookmaker})
      </span>
      <span className="section-row__link">Explore player prop insights →</span>
    </Link>
  );
}

function HomePage() {
  const [entries, setEntries] = useState<DashboardEntry[] | null>(null);
  const [results, setResults] = useState<MatchSummary[]>([]);
  const [opportunities, setOpportunities] = useState<DiversifiedOpportunity[]>([]);
  const [opportunitiesLoading, setOpportunitiesLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDashboard()
      .then(setEntries)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load this round's matches"));
    fetchRecentResults(4).then(setResults).catch(() => setResults([]));
    fetchDiversifiedOpportunities({ view: "overall", marketScope: "all", limit: 3 })
      .then((r) => setOpportunities(r.opportunities))
      .catch(() => setOpportunities([]))
      .finally(() => setOpportunitiesLoading(false));
  }, []);

  const upcoming = (entries ?? []).slice(0, HOME_MATCH_LIMIT).map((e) => e.match);

  return (
    <main className="home-page">
      <section className="home-hero">
        <span className="home-hero__eyebrow">AFL Research &amp; Market Intelligence</span>
        <h1 className="home-hero__title">Explore the numbers behind every match, player and market.</h1>
        <p className="home-hero__subtitle">
          Model projections, live bookmaker markets, and player research in one place — built for genuine analysis,
          not hot takes.
        </p>
        <PlayerSearch />
        <div className="home-hero__actions">
          <Link to="/matches" className="home-hero__action home-hero__action--primary">
            Explore this round
          </Link>
          <Link to="/players" className="home-hero__action">
            Find a player
          </Link>
          <Link to="/prop-insights" className="home-hero__action">
            View market insights
          </Link>
          <Link to="/market-movement" className="home-hero__action">
            See market movement
          </Link>
        </div>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="home-section">
        <div className="section-row">
          <h2 className="section-title">This round</h2>
          <Link to="/matches" className="section-row__link">
            View all matches →
          </Link>
        </div>
        {entries === null && !error && (
          <div className="match-card-grid">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="card">
                <Skeleton width="70%" height="1rem" />
                <div style={{ height: 8 }} />
                <Skeleton width="100%" height="2rem" />
              </div>
            ))}
          </div>
        )}
        {entries !== null && upcoming.length === 0 && (
          <EmptyState
            title="No upcoming fixtures found"
            description="Once the next round is scheduled, matches will appear here with model outlooks and market data."
          />
        )}
        {upcoming.length > 0 && (
          <div className="match-card-grid">
            {upcoming.map((m) => (
              <MatchCard key={m.id} match={m} />
            ))}
          </div>
        )}
      </section>

      <section className="home-section home-section--split">
        <TopOpportunityTeaser opportunities={opportunities} loading={opportunitiesLoading} />

        <div className="card home-teaser">
          <span className="home-teaser__label">Recent results</span>
          {results.length === 0 ? (
            <span className="hint">No recent completed matches yet.</span>
          ) : (
            <ul className="home-results-list">
              {results.map((m) => (
                <li key={m.id}>
                  <Link to={`/matches/${m.id}`}>
                    {m.home_team.short_name} {m.home_score}–{m.away_score} {m.away_team.short_name}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <Disclaimer />
    </main>
  );
}

export default HomePage;
