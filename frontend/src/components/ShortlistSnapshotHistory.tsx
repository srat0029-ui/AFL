import { useEffect, useState } from "react";
import {
  createShortlistSnapshot,
  fetchShortlistRoundSummary,
  fetchShortlistSnapshot,
  fetchShortlistSnapshots,
  settleShortlistSnapshot,
  type ShortlistRoundSummary,
  type ShortlistSnapshot,
  type ShortlistSnapshotSummary,
} from "../api/client";
import "./ShortlistSnapshotHistory.css";

/** Weekly Bet Review + Decision Support stage, Sections 14-17: freeze the
 * current Final Weekly Shortlist, browse past snapshots, replay one
 * exactly as it appeared, settle results once matches complete, and view
 * a round summary. Separate from real market observations (Real Market
 * Tracking page) — this tracks what the SHORTLIST showed, not raw quotes. */
function ShortlistSnapshotHistory() {
  const [snapshots, setSnapshots] = useState<ShortlistSnapshotSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<ShortlistSnapshot | null>(null);
  const [roundSummary, setRoundSummary] = useState<ShortlistRoundSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    setLoading(true);
    fetchShortlistSnapshots()
      .then(setSnapshots)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load snapshots"))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  function handleFreeze() {
    setCreating(true);
    createShortlistSnapshot({})
      .then(() => refresh())
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to freeze snapshot"))
      .finally(() => setCreating(false));
  }

  function handleOpen(id: number) {
    setRoundSummary(null);
    fetchShortlistSnapshot(id)
      .then(setSelected)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load snapshot"));
  }

  function handleSettleAndSummarize(id: number) {
    settleShortlistSnapshot(id)
      .then(() => fetchShortlistRoundSummary(id))
      .then(setRoundSummary)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to settle/summarize snapshot"));
  }

  return (
    <div className="snapshot-history">
      <div className="snapshot-history__controls">
        <button className="snapshot-history__freeze-btn" onClick={handleFreeze} disabled={creating}>
          {creating ? "Freezing…" : "Freeze current Shortlist as a snapshot"}
        </button>
        <p className="hint">
          A snapshot is a frozen, timestamped copy of the Final Weekly Shortlist — exact odds, bookmaker, model probability,
          and reasons at that moment. Freezing never overwrites an earlier snapshot.
        </p>
      </div>

      {error && <div className="prop-insights-page__error">{error}</div>}
      {loading && <p className="hint">Loading…</p>}

      {!loading && snapshots.length === 0 && <p className="hint">No snapshots yet.</p>}

      {!loading && snapshots.length > 0 && (
        <table className="snapshot-history__table">
          <thead>
            <tr>
              <th>Round</th>
              <th>Snapshot time</th>
              <th># items</th>
              <th>Label</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((s) => (
              <tr key={s.id}>
                <td>{s.round_number ?? "—"}</td>
                <td>{new Date(s.created_at).toLocaleString()}</td>
                <td>{s.n_items}</td>
                <td>{s.label ?? "—"}</td>
                <td>
                  <button className="snapshot-history__open-btn" onClick={() => handleOpen(s.id)}>
                    View
                  </button>
                  <button className="snapshot-history__open-btn" onClick={() => handleSettleAndSummarize(s.id)}>
                    Settle + Summarize
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && (
        <section className="snapshot-history__detail">
          <h3>
            Snapshot #{selected.id} — Round {selected.round_number} ({new Date(selected.created_at).toLocaleString()})
          </h3>
          <table className="prop-insights-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Label</th>
                <th>Price</th>
                <th>Model prob.</th>
                <th>Quality tier</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {selected.items.map((it) => (
                <tr key={it.id}>
                  <td>{it.rank}</td>
                  <td>{it.label}</td>
                  <td>
                    ${it.best_price.toFixed(2)} {it.best_bookmaker}
                  </td>
                  <td>{(it.model_probability * 100).toFixed(1)}%</td>
                  <td>{it.quality_tier}</td>
                  <td>{it.match_result ?? "Unresolved"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {roundSummary && (
        <section className="snapshot-history__detail">
          <h3>Round Summary — Snapshot #{roundSummary.snapshot_id}</h3>
          {roundSummary.small_sample_warning && (
            <p className="hint snapshot-history__warning">
              Small sample — {roundSummary.n_settled} settled item(s). Not a statistically meaningful result.
            </p>
          )}
          <div className="snapshot-history__summary-stats">
            <span>{roundSummary.n_won} won</span>
            <span>{roundSummary.n_lost} lost</span>
            <span>{roundSummary.n_push} push</span>
            <span>{roundSummary.n_unresolved} unresolved</span>
            <span>
              Hypothetical flat-$1 P/L:{" "}
              {roundSummary.hypothetical_flat_stake_pl !== null ? roundSummary.hypothetical_flat_stake_pl.toFixed(2) : "—"}
            </span>
          </div>
          <table className="prop-insights-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Price</th>
                <th>Model prob.</th>
                <th>Market prob.</th>
                <th>Result</th>
                <th>Actual</th>
                <th>Flat-$1 P/L</th>
              </tr>
            </thead>
            <tbody>
              {roundSummary.items.map((it, i) => (
                <tr key={i}>
                  <td>{it.label}</td>
                  <td>
                    ${it.best_price.toFixed(2)} {it.best_bookmaker}
                  </td>
                  <td>{(it.model_probability * 100).toFixed(1)}%</td>
                  <td>{(it.market_implied_probability * 100).toFixed(1)}%</td>
                  <td>{it.match_result ?? "Unresolved"}</td>
                  <td>{it.actual_stat_value ?? "—"}</td>
                  <td>{it.flat_stake_pl !== null ? it.flat_stake_pl.toFixed(2) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

export default ShortlistSnapshotHistory;
