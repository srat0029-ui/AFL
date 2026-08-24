import { useEffect, useState } from "react";
import { fetchRoundMultiSummary, type MultiTierKey, type RoundMultiSummaryRow } from "../api/client";
import MultiBuilderView, { ReadinessBadge } from "../components/MultiBuilderView";
import Disclaimer from "../components/Disclaimer";
import { formatCompactDateTime } from "../lib/datetime";
import "./MultisPage.css";

const TIER_LABELS: Record<string, string> = {
  conservative: "Conservative", balanced: "Balanced", higher_return: "Higher Return", longer_shot: "Longer Shot",
};
const TIER_ORDER: MultiTierKey[] = ["conservative", "balanced", "higher_return", "longer_shot"];

// Item 17: every finals match's best (default High Probability) option per
// tier, inline in the overview list — never requiring a click into each
// match just to see what's available.
function TierSummaryGrid({ row }: { row: RoundMultiSummaryRow }) {
  return (
    <div className="multis-page__tier-summary-grid">
      {TIER_ORDER.map((t) => {
        const opt = row.best_options_by_tier[t];
        return (
          <div key={t} className="multis-page__tier-summary-cell">
            <span className="multis-page__tier-summary-label">{TIER_LABELS[t]}</span>
            {opt ? (
              <span className="multis-page__tier-summary-value">
                ${opt.indicative_combined_odds.toFixed(2)} · {opt.n_legs} legs · {opt.bookmaker}
              </span>
            ) : (
              <span className="multis-page__tier-summary-value multis-page__tier-summary-value--empty">Not available</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MatchRow({ row, selected, onSelect }: { row: RoundMultiSummaryRow; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`multis-page__match-row${selected ? " multis-page__match-row--active" : ""}`} onClick={onSelect}>
      <div className="multis-page__match-teams">
        {row.home_team_name} v {row.away_team_name}
        <ReadinessBadge readiness={row.readiness} />
      </div>
      <div className="hint">{formatCompactDateTime(row.scheduled_start)}</div>
      <div className="multis-page__match-stats hint">
        {row.n_eligible_legs} eligible legs · {row.n_bookmakers_available} bookmaker(s) support a multi
      </div>
      <TierSummaryGrid row={row} />
      {row.tiers_available.length === 0 && <p className="hint">No tiers currently buildable, even provisionally</p>}
    </button>
  );
}

/** Compact round-wide Multi Builder view — pick a match and see its
 * generated tiers without opening every Match Centre. Reuses the exact
 * same per-match component/endpoint Match Centre uses. */
function MultisPage() {
  const [rows, setRows] = useState<RoundMultiSummaryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchRoundMultiSummary({ confirmedOnly: false })
      .then((r) => {
        setRows(r.matches);
        setSelectedMatchId((prev) => prev ?? r.matches[0]?.match_id ?? null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load multis"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="multis-page">
      <header className="multis-page__header">
        <h1>Multis</h1>
        <p className="hint">
          Model-informed multi combinations across the round's matches — pick a match to see its Conservative,
          Balanced, Higher Return, and Longer Shot options. Never guaranteed, safe, or a lock.
        </p>
      </header>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && rows.length === 0 && <p className="empty-state">No upcoming matches found.</p>}

      {!loading && !error && rows.length > 0 && (
        <div className="multis-page__layout">
          <div className="multis-page__match-list">
            {rows.map((row) => (
              <MatchRow key={row.match_id} row={row} selected={row.match_id === selectedMatchId} onSelect={() => setSelectedMatchId(row.match_id)} />
            ))}
          </div>
          <div className="multis-page__detail">
            {selectedMatchId !== null && <MultiBuilderView matchId={selectedMatchId} />}
          </div>
        </div>
      )}

      <Disclaimer />
    </main>
  );
}

export default MultisPage;
