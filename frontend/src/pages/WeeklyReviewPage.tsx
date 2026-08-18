import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import DataFreshnessPanel from "../components/DataFreshnessPanel";
import Disclaimer from "../components/Disclaimer";
import OpportunityComparisonTable from "../components/OpportunityComparisonTable";
import ShortlistSnapshotHistory from "../components/ShortlistSnapshotHistory";
import WeeklyReviewOpportunityRow, { opportunityKey } from "../components/WeeklyReviewOpportunityRow";
import { fetchWeeklyReviewPage, type WeeklyReviewOpportunity, type WeeklyReviewPage as WeeklyReviewPageData } from "../api/client";
import "./WeeklyReviewPage.css";

type Section = "shortlist" | "player" | "team" | "waiting" | "coverage" | "history";

function WeeklyReviewPage() {
  const [page, setPage] = useState<WeeklyReviewPageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<Section>("shortlist");
  const [comparisonKeys, setComparisonKeys] = useState<Set<string>>(new Set());

  useEffect(() => {
    setLoading(true);
    fetchWeeklyReviewPage({})
      .then(setPage)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load Weekly Review"))
      .finally(() => setLoading(false));
  }, []);

  function toggleComparison(key: string) {
    setComparisonKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const allOpportunities: WeeklyReviewOpportunity[] = page
    ? [...page.final_shortlist, ...page.strongest_player_opportunities, ...page.strongest_team_opportunities, ...page.markets_waiting_on_team_confirmation]
    : [];
  const selectedOpportunities = allOpportunities.filter((o) => comparisonKeys.has(opportunityKey(o)));
  // de-dupe (an opportunity can appear in more than one section)
  const seen = new Set<string>();
  const uniqueSelected = selectedOpportunities.filter((o) => {
    const k = opportunityKey(o);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });

  return (
    <main className="weekly-review-page">
      <header className="weekly-review-page__header">
        <h1>Weekly Review</h1>
        <p className="hint">
          The page to open first when deciding what to inspect this round — the Final Weekly Shortlist, the strongest
          player and team opportunities behind it, model/market disagreements worth investigating, markets still waiting
          on team confirmation, and bookmaker coverage. Select opportunities below to compare them side by side.
        </p>
      </header>

      <DataFreshnessPanel />

      <nav className="tab-bar">
        <button className={`tab-bar__tab${activeSection === "shortlist" ? " tab-bar__tab--active" : ""}`} onClick={() => setActiveSection("shortlist")}>
          Final Weekly Shortlist
        </button>
        <button className={`tab-bar__tab${activeSection === "player" ? " tab-bar__tab--active" : ""}`} onClick={() => setActiveSection("player")}>
          Strongest Player Opportunities
        </button>
        <button className={`tab-bar__tab${activeSection === "team" ? " tab-bar__tab--active" : ""}`} onClick={() => setActiveSection("team")}>
          Strongest Team Opportunities
        </button>
        <button className={`tab-bar__tab${activeSection === "waiting" ? " tab-bar__tab--active" : ""}`} onClick={() => setActiveSection("waiting")}>
          Markets Waiting on Team Confirmation
        </button>
        <button className={`tab-bar__tab${activeSection === "coverage" ? " tab-bar__tab--active" : ""}`} onClick={() => setActiveSection("coverage")}>
          Market Coverage
        </button>
        <button className={`tab-bar__tab${activeSection === "history" ? " tab-bar__tab--active" : ""}`} onClick={() => setActiveSection("history")}>
          Snapshot History
        </button>
      </nav>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && page && (
        <>
          {activeSection === "shortlist" && (
            <section>
              <p className="hint">
                {page.final_shortlist.length} opportunit{page.final_shortlist.length === 1 ? "y" : "ies"} currently clear
                the readiness bar.{" "}
                <Link to="/prop-insights">See Model vs Market Disagreements ({page.model_vs_market_disagreements_count})</Link>
              </p>
              {page.final_shortlist.length === 0 && <p className="empty-state">Nothing currently qualifies for the Shortlist.</p>}
              <div className="weekly-review-page__rows">
                {page.final_shortlist.map((o) => (
                  <WeeklyReviewOpportunityRow key={opportunityKey(o)} opportunity={o} selected={comparisonKeys.has(opportunityKey(o))} onToggle={toggleComparison} />
                ))}
              </div>
            </section>
          )}

          {activeSection === "player" && (
            <section>
              <p className="hint">The strongest player opportunities, once confirmed teams make them eligible for the Shortlist.</p>
              {page.strongest_player_opportunities.length === 0 && (
                <p className="empty-state">
                  {page.any_confirmed_player_lineups ? "No player opportunities currently qualify." : "Player opportunities are waiting for confirmed teams."}
                </p>
              )}
              <div className="weekly-review-page__rows">
                {page.strongest_player_opportunities.map((o) => (
                  <WeeklyReviewOpportunityRow key={opportunityKey(o)} opportunity={o} selected={comparisonKeys.has(opportunityKey(o))} onToggle={toggleComparison} />
                ))}
              </div>
            </section>
          )}

          {activeSection === "team" && (
            <section>
              <div className="weekly-review-page__rows">
                {page.strongest_team_opportunities.map((o) => (
                  <WeeklyReviewOpportunityRow key={opportunityKey(o)} opportunity={o} selected={comparisonKeys.has(opportunityKey(o))} onToggle={toggleComparison} />
                ))}
              </div>
            </section>
          )}

          {activeSection === "waiting" && (
            <section>
              <p className="hint">Player markets with a positive model-market difference that aren't confirmed yet — worth a second look once lineups land.</p>
              {page.markets_waiting_on_team_confirmation.length === 0 && <p className="empty-state">Nothing currently waiting.</p>}
              <div className="weekly-review-page__rows">
                {page.markets_waiting_on_team_confirmation.map((o) => (
                  <WeeklyReviewOpportunityRow key={opportunityKey(o)} opportunity={o} selected={comparisonKeys.has(opportunityKey(o))} onToggle={toggleComparison} />
                ))}
              </div>
            </section>
          )}

          {activeSection === "coverage" && (
            <section>
              <div className="market-coverage-grid">
                {page.bookmaker_coverage.map((c) => (
                  <div key={c.bookmaker_name}>
                    <span className="market-coverage-grid__label">{c.bookmaker_name}</span>
                    <span className="market-coverage-grid__value">{c.n_active_player_markets}</span>
                    <span className="hint">markets across {c.n_matches_covered} match{c.n_matches_covered === 1 ? "" : "es"}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {activeSection === "history" && <ShortlistSnapshotHistory />}

          {uniqueSelected.length > 0 && activeSection !== "history" && (
            <section className="weekly-review-page__comparison">
              <div className="weekly-review-page__comparison-header">
                <h2>Comparing {uniqueSelected.length} opportunit{uniqueSelected.length === 1 ? "y" : "ies"}</h2>
                <button className="btn" onClick={() => setComparisonKeys(new Set())}>
                  Clear all
                </button>
              </div>
              <OpportunityComparisonTable opportunities={uniqueSelected} onRemove={(key) => toggleComparison(key)} />
            </section>
          )}
        </>
      )}

      <Disclaimer />
    </main>
  );
}

export default WeeklyReviewPage;
