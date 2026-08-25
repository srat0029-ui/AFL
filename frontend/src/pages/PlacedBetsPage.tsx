import { Fragment, useEffect, useState } from "react";
import {
  deletePlacedBet,
  fetchPlacedBetAnalytics,
  fetchPlacedBets,
  type PlacedBet,
  type PlacedBetAnalytics,
  type PlacedBetSplit,
  type PlacedBetStatus,
} from "../api/client";
import "./PlacedBetsPage.css";

type TabKey = "pending" | "won" | "lost" | "void" | "all";

const TABS: { key: TabKey; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "won", label: "Won" },
  { key: "lost", label: "Lost" },
  { key: "void", label: "Void" },
  { key: "all", label: "All" },
];

const SOURCE_MODE_LABELS: Record<string, string> = {
  high_probability: "High Probability",
  best_value: "Best Value",
  best_opportunity: "Best Opportunity",
  final_shortlist: "Final Shortlist",
  manual: "Manual",
};

const MARKET_TYPE_LABELS: Record<string, string> = {
  player_disposals: "Disposals",
  player_goals: "Goals",
  h2h: "H2H",
  line: "Line",
  total: "Total",
};

const CONFIDENCE_TIER_LABELS: Record<string, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

function statusMatches(status: PlacedBetStatus, tab: TabKey): boolean {
  if (tab === "all") return true;
  if (tab === "void") return status === "void" || status === "push";
  return status === tab;
}

function formatOdds(n: number): string {
  return n.toFixed(2);
}

function formatPct(n: number | null): string {
  return n === null ? "—" : `${(n * 100).toFixed(1)}%`;
}

function formatUnits(n: number | null): string {
  if (n === null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}u`;
}

// Per-bet return isn't stored on the backend (only portfolio-level analytics
// are) — derive it from stake/odds/status already on the row so the ledger
// can show it without waiting on a settled-status recompute elsewhere.
function betReturn(bet: PlacedBet): number | null {
  if (bet.stake === null || bet.stake === undefined) return null;
  if (bet.status === "won") return bet.stake * (bet.odds_taken - 1);
  if (bet.status === "lost") return -bet.stake;
  return 0;
}

function formatReturn(bet: PlacedBet): string {
  if (bet.status === "pending") return "—";
  const r = betReturn(bet);
  if (r === null) return "—";
  return `${r >= 0 ? "+" : ""}${r.toFixed(2)}`;
}

// A single split row (e.g. one source mode, one probability bucket) — the
// same shape/labeling rules apply everywhere: n is always shown, and a
// split below the backend's MIN_SAMPLE_FOR_LABELED threshold is flagged
// Exploratory rather than presented as if it means anything about hit rate.
function SplitTable({ title, splits, labelMap }: { title: string; splits: PlacedBetSplit[]; labelMap?: Record<string, string> }) {
  if (splits.length === 0) return null;
  return (
    <div className="placed-bets-summary__split">
      <h3>{title}</h3>
      <table className="placed-bets-summary__split-table">
        <thead>
          <tr>
            <th>Split</th>
            <th>N</th>
            <th>W-L-V</th>
            <th>Hit rate</th>
          </tr>
        </thead>
        <tbody>
          {splits.map((s) => (
            <tr key={s.label}>
              <td>{labelMap?.[s.label] ?? s.label}</td>
              <td>{s.n_settled}</td>
              <td>
                {s.wins}-{s.losses}-{s.voids}
              </td>
              <td>
                {formatPct(s.hit_rate)}
                {s.exploratory && <span className="placed-bets-summary__exploratory-tag">Exploratory</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlacedBetsSummary({ analytics }: { analytics: PlacedBetAnalytics }) {
  if (analytics.n_total_settled === 0) return null;
  return (
    <section className="placed-bets-summary">
      <h2>
        Summary
        {analytics.exploratory && (
          <span className="placed-bets-summary__exploratory-tag placed-bets-summary__exploratory-tag--headline">
            Exploratory — fewer than {analytics.min_sample_for_labeled} settled bets. Not enough data to say anything about
            hit rate or profitability yet.
          </span>
        )}
      </h2>
      <div className="placed-bets-summary__headline">
        <div>
          <span className="placed-bets-summary__stat-label">Settled</span>
          <span className="placed-bets-summary__stat-value">{analytics.n_total_settled}</span>
        </div>
        <div>
          <span className="placed-bets-summary__stat-label">W-L-V</span>
          <span className="placed-bets-summary__stat-value">
            {analytics.wins}-{analytics.losses}-{analytics.voids}
          </span>
        </div>
        <div>
          <span className="placed-bets-summary__stat-label">Hit rate</span>
          <span className="placed-bets-summary__stat-value">{formatPct(analytics.hit_rate)}</span>
        </div>
        <div>
          <span className="placed-bets-summary__stat-label">Avg. odds taken</span>
          <span className="placed-bets-summary__stat-value">{analytics.avg_odds_taken?.toFixed(2) ?? "—"}</span>
        </div>
        <div>
          <span className="placed-bets-summary__stat-label">Flat $1 return</span>
          <span className="placed-bets-summary__stat-value">{formatUnits(analytics.flat_stake_units)}</span>
        </div>
        <div>
          <span className="placed-bets-summary__stat-label">Flat $1 ROI</span>
          <span className="placed-bets-summary__stat-value">
            {analytics.flat_stake_roi_pct === null ? "—" : `${analytics.flat_stake_roi_pct >= 0 ? "+" : ""}${analytics.flat_stake_roi_pct.toFixed(1)}%`}
          </span>
        </div>
      </div>
      <p className="hint">
        Hypothetical only — a flat $1 on every decided (won/lost) bet, stake back on void/push. Not staking advice, and
        not a claim about future results.
      </p>
      <div className="placed-bets-summary__splits">
        <SplitTable title="By source mode" splits={analytics.by_source_mode} labelMap={SOURCE_MODE_LABELS} />
        <SplitTable title="By market type" splits={analytics.by_market_type} labelMap={MARKET_TYPE_LABELS} />
        <SplitTable title="By model probability" splits={analytics.by_probability_bucket} />
        <SplitTable title="By confidence tier" splits={analytics.by_confidence_tier} labelMap={CONFIDENCE_TIER_LABELS} />
      </div>
    </section>
  );
}

// Research/model context for a bet, one level down from the ledger row —
// keeps the main table to the tracking-ledger columns the page is meant to
// be (Section 5) while still making this evidence available on demand.
function BetDetail({ bet }: { bet: PlacedBet }) {
  return (
    <tr className="placed-bets-table__detail-row">
      <td colSpan={9}>
        <div className="placed-bets-detail">
          <div>
            <span className="placed-bets-detail__label">Model probability (at placement)</span>
            <span className="placed-bets-detail__value">{formatPct(bet.model_probability)}</span>
          </div>
          <div>
            <span className="placed-bets-detail__label">Model fair odds (at placement)</span>
            <span className="placed-bets-detail__value">{formatOdds(bet.model_fair_odds)}</span>
          </div>
          <div>
            <span className="placed-bets-detail__label">Confidence</span>
            <span className="placed-bets-detail__value">{CONFIDENCE_TIER_LABELS[bet.confidence_tier] ?? bet.confidence_tier}</span>
          </div>
          <div>
            <span className="placed-bets-detail__label">Lineup status at placement</span>
            <span className="placed-bets-detail__value">{bet.lineup_status ?? "—"}</span>
          </div>
          <div>
            <span className="placed-bets-detail__label">Source</span>
            <span className="placed-bets-detail__value">{SOURCE_MODE_LABELS[bet.source_mode] ?? bet.source_mode}</span>
          </div>
          <div>
            <span className="placed-bets-detail__label">Actual result</span>
            <span className="placed-bets-detail__value">
              {bet.actual_stat_value ?? "—"} · {bet.status}
            </span>
          </div>
          <div>
            <span className="placed-bets-detail__label">Settled</span>
            <span className="placed-bets-detail__value">{bet.settled_at ? new Date(bet.settled_at).toLocaleString() : "—"}</span>
          </div>
          {bet.notes && (
            <div className="placed-bets-detail__notes">
              <span className="placed-bets-detail__label">Notes</span>
              <span className="placed-bets-detail__value">{bet.notes}</span>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

function PlacedBetsPage() {
  const [bets, setBets] = useState<PlacedBet[] | null>(null);
  const [analytics, setAnalytics] = useState<PlacedBetAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("pending");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  function load() {
    fetchPlacedBets()
      .then(setBets)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load placed bets"));
    fetchPlacedBetAnalytics()
      .then(setAnalytics)
      .catch(() => undefined); // summary is supplementary — a failure here shouldn't block the bet list
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDelete(id: number) {
    await deletePlacedBet(id);
    load();
  }

  const filtered = bets?.filter((b) => statusMatches(b.status, tab)) ?? [];
  const counts = bets
    ? {
        pending: bets.filter((b) => b.status === "pending").length,
        won: bets.filter((b) => b.status === "won").length,
        lost: bets.filter((b) => b.status === "lost").length,
        void: bets.filter((b) => b.status === "void" || b.status === "push").length,
        all: bets.length,
      }
    : null;

  return (
    <main className="placed-bets-page">
      <header>
        <h1 className="page-title">Placed Bets</h1>
        <p className="page-subtitle">
          A record of bets you actually placed — kept separate from everything the app merely surfaced. No staking
          advice; results settle automatically once match/player results arrive.
        </p>
      </header>

      {error && <div className="error-banner">{error}</div>}

      {analytics && <PlacedBetsSummary analytics={analytics} />}

      <nav className="placed-bets-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={t.key === tab ? "placed-bets-tabs__btn placed-bets-tabs__btn--active" : "placed-bets-tabs__btn"}
            onClick={() => setTab(t.key)}
          >
            {t.label}
            {counts ? ` (${counts[t.key]})` : ""}
          </button>
        ))}
      </nav>

      {bets === null && !error && <p className="loading-state">Loading…</p>}

      {bets !== null && filtered.length === 0 && <p className="empty-state">No bets in this view yet.</p>}

      {filtered.length > 0 && (
        <div className="placed-bets-table__wrap">
          <table className="placed-bets-table">
            <thead>
              <tr>
                <th>Selection</th>
                <th>Bookmaker</th>
                <th className="num">Odds</th>
                <th className="num">Stake</th>
                <th>Placed</th>
                <th>Status</th>
                <th className="num">Return</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((bet) => {
                const expanded = expandedId === bet.id;
                const ret = betReturn(bet);
                return (
                  <Fragment key={bet.id}>
                    <tr className="placed-bets-table__row-clickable" onClick={() => setExpandedId(expanded ? null : bet.id)}>
                      <td>
                        {bet.label}
                        <span className="hint"> · {MARKET_TYPE_LABELS[bet.market_type] ?? bet.market_type}</span>
                      </td>
                      <td>{bet.bookmaker}</td>
                      <td className="num">{formatOdds(bet.odds_taken)}</td>
                      <td className="num">{bet.stake ?? "—"}</td>
                      <td>{bet.placed_at ? new Date(bet.placed_at).toLocaleString() : "—"}</td>
                      <td>
                        <span className={`placed-bets-status placed-bets-status--${bet.status}`}>{bet.status}</span>
                      </td>
                      <td className={ret !== null && ret !== 0 ? `num ${ret > 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}` : "num"}>
                        {formatReturn(bet)}
                      </td>
                      <td>
                        {bet.status === "pending" && (
                          <button
                            type="button"
                            className="placed-bets-table__delete"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDelete(bet.id);
                            }}
                          >
                            Remove
                          </button>
                        )}
                      </td>
                    </tr>
                    {expanded && <BetDetail bet={bet} />}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

export default PlacedBetsPage;
