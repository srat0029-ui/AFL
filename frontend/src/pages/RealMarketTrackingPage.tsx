import { useEffect, useState } from "react";
import "./RealMarketTrackingPage.css";
import Disclaimer from "../components/Disclaimer";
import { formatPreciseDateTime, formatShortDate } from "../lib/datetime";
import {
  fetchMarketMovement,
  fetchQuoteHistory,
  fetchRealMarketTracking,
  type BucketResult,
  type MarketMovement,
  type MarketOpenTiming,
  type QuoteHistoryEntry,
  type RealMarketTrackingReport,
  type SampleSizeLevel,
} from "../api/client";

const SAMPLE_LABELS: Record<SampleSizeLevel, string> = {
  exploratory: "Exploratory only — fewer than 30 settled player-matches.",
  low_confidence: "Low-confidence evidence — fewer than 100 settled player-matches.",
  still_developing: "Still developing — fewer than 300 settled player-matches.",
  informative: "Larger sample — increasingly informative, still not a formal significance test.",
};

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}
function num(v: number | null, digits = 3): string {
  return v === null ? "—" : v.toFixed(digits);
}

function BucketTable({ title, buckets }: { title: string; buckets: BucketResult[] }) {
  return (
    <div className="rmt-card">
      <h3>{title}</h3>
      <div className="rmt-table-scroll">
        <table className="rmt-table">
          <thead>
            <tr>
              <th>Group</th>
              <th>Observations</th>
              <th>Unique player-matches</th>
              <th>Settled (W/L)</th>
              <th>Hit rate</th>
              <th>Avg odds</th>
              <th>Flat $1 P/L</th>
              <th>ROI</th>
              <th>Sample</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((b) => (
              <tr key={b.label}>
                <td>{b.label}</td>
                <td>{b.n_observations}</td>
                <td>{b.n_unique_player_matches}</td>
                <td>{b.returns.n_settled_binary}</td>
                <td>{pct(b.returns.win_rate)}</td>
                <td>{b.returns.average_odds ? `$${b.returns.average_odds.toFixed(2)}` : "—"}</td>
                <td className={b.returns.total_profit_flat_stake >= 0 ? "rmt-pos" : "rmt-neg"}>
                  {b.returns.n_settled_binary ? `$${b.returns.total_profit_flat_stake.toFixed(2)}` : "—"}
                </td>
                <td className={(b.returns.roi ?? 0) >= 0 ? "rmt-pos" : "rmt-neg"}>{pct(b.returns.roi)}</td>
                <td>
                  <span className={`rmt-sample rmt-sample--${b.sample_size_level}`} title={SAMPLE_LABELS[b.sample_size_level]}>
                    {b.sample_size_level.replace("_", " ")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function QuoteHistoryDrawer({ movement, onClose }: { movement: MarketMovement; onClose: () => void }) {
  const [entries, setEntries] = useState<QuoteHistoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEntries(null);
    setError(null);
    fetchQuoteHistory({
      playerId: movement.player_id,
      matchId: movement.match_id,
      bookmakerId: movement.bookmaker_id,
      marketType: movement.market_type,
      lineType: movement.line_type,
      threshold: movement.threshold,
    })
      .then(setEntries)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load quote history"));
  }, [movement]);

  return (
    <div className="rmt-drawer__backdrop" onClick={onClose}>
      <div className="rmt-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="rmt-drawer__header">
          <h3>
            {movement.player_name} — {movement.market_type} {movement.line_type === "multi_plus" ? `${movement.threshold}+` : `${movement.threshold}`} @ {movement.bookmaker_name}
          </h3>
          <button type="button" className="rmt-drawer__close" onClick={onClose} aria-label="Close">×</button>
        </div>
        {error && <div className="error-banner">{error}</div>}
        {!error && entries === null && <p className="loading-state">Loading…</p>}
        {entries !== null && (
          <div className="rmt-table-scroll">
            <table className="rmt-table">
              <thead>
                <tr>
                  <th>Observed at</th>
                  <th>Odds</th>
                  <th>Implied prob.</th>
                  <th>No-vig prob.</th>
                  <th>Model prob.</th>
                  <th>Diff (pp)</th>
                  <th>Confidence</th>
                  <th>Lineup status</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => (
                  <tr key={i}>
                    <td>{formatPreciseDateTime(e.observed_at)}</td>
                    <td>${e.offered_odds.toFixed(2)}</td>
                    <td>{pct(e.raw_implied_probability)}</td>
                    <td>{pct(e.devigged_probability)}</td>
                    <td>{pct(e.model_probability)}</td>
                    <td className={e.difference_pp >= 0 ? "rmt-pos" : "rmt-neg"}>{(e.difference_pp * 100).toFixed(1)}</td>
                    <td>{e.confidence_tier}</td>
                    <td>{e.selection_status_at_observation}</td>
                    <td>{e.market_result ?? "pending"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function MarketOpenTimingTable({ timing }: { timing: MarketOpenTiming[] }) {
  const sorted = [...timing].sort((a, b) => b.n_price_changes - a.n_price_changes);
  const shown = sorted.slice(0, 50);
  return (
    <div className="rmt-card">
      <h3>Market-open timing</h3>
      <p className="hint">
        When did we first/last see a price for each logged line, and how much did it actually move? Sorted by number
        of price changes (most movement first). {shown.length < sorted.length && `Showing ${shown.length} of ${sorted.length}.`}
      </p>
      <div className="rmt-table-scroll">
        <table className="rmt-table">
          <thead>
            <tr>
              <th>Player</th>
              <th>Market</th>
              <th>Bookmaker</th>
              <th>First observed</th>
              <th>Hours before kickoff</th>
              <th>Latest observed</th>
              <th>Hours before kickoff</th>
              <th>Price changes</th>
              <th>Snapshots</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t, i) => (
              <tr key={i}>
                <td>{t.player_name}</td>
                <td>{t.market_type} {t.line_type === "multi_plus" ? `${t.threshold}+` : t.threshold}</td>
                <td>{t.bookmaker_name}</td>
                <td>{formatPreciseDateTime(t.first_observed_at)}</td>
                <td>{t.first_hours_before_kickoff.toFixed(1)}</td>
                <td>{formatPreciseDateTime(t.latest_observed_at)}</td>
                <td>{t.latest_hours_before_kickoff.toFixed(1)}</td>
                <td>{t.n_price_changes}</td>
                <td>{t.n_observations}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MarketMovementTable({ movements, onSelect }: { movements: MarketMovement[]; onSelect: (m: MarketMovement) => void }) {
  return (
    <div className="rmt-card">
      <h3>Market movement (per player / market / bookmaker)</h3>
      <p className="hint">First vs latest odds and model-market difference for every tracked line. Click a row for the full price history.</p>
      <div className="rmt-table-scroll">
        <table className="rmt-table rmt-table--clickable">
          <thead>
            <tr>
              <th>Player</th>
              <th>Market</th>
              <th>Bookmaker</th>
              <th>First odds</th>
              <th>Latest odds</th>
              <th>Range</th>
              <th>First diff (pp)</th>
              <th>Latest diff (pp)</th>
              <th>Quotes</th>
            </tr>
          </thead>
          <tbody>
            {movements.map((m) => (
              <tr
                key={`${m.player_id}-${m.match_id}-${m.bookmaker_id}-${m.market_type}-${m.line_type}-${m.threshold}`}
                onClick={() => onSelect(m)}
              >
                <td>{m.player_name}</td>
                <td>{m.market_type} {m.line_type === "multi_plus" ? `${m.threshold}+` : m.threshold}</td>
                <td>{m.bookmaker_name}</td>
                <td>${m.first_odds.toFixed(2)}</td>
                <td>${m.latest_odds.toFixed(2)}</td>
                <td>${m.lowest_odds.toFixed(2)} – ${m.highest_odds.toFixed(2)}</td>
                <td className={m.first_difference_pp >= 0 ? "rmt-pos" : "rmt-neg"}>{(m.first_difference_pp * 100).toFixed(1)}</td>
                <td className={m.latest_difference_pp >= 0 ? "rmt-pos" : "rmt-neg"}>{(m.latest_difference_pp * 100).toFixed(1)}</td>
                <td>{m.n_observations}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RealMarketTrackingPage() {
  const [report, setReport] = useState<RealMarketTrackingReport | null>(null);
  const [movements, setMovements] = useState<MarketMovement[] | null>(null);
  const [selectedMovement, setSelectedMovement] = useState<MarketMovement | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchRealMarketTracking(), fetchMarketMovement()])
      .then(([r, m]) => {
        setReport(r);
        setMovements(m);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load real market tracking data"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="rmt-page">
      <header className="rmt-page__header">
        <h1>Real Market Tracking</h1>
        <p className="hint">
          <strong>Real logged market observations</strong> — every real bookmaker price we've fetched, frozen against
          what the model believed at that exact moment, tracked toward eventual settlement against real player
          results. This is NOT the synthetic 2016-2025 historical backtest (see the Backtesting page for that) — this
          dataset starts empty and grows one real match at a time. Nothing here is a staking recommendation. This is
          an <strong>evaluation-only</strong> dataset — it is never used to retune the model, confidence tiers, or
          ranking weights; doing so would turn an honest holdout into a second training set.
        </p>
      </header>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && report && (
        <>
          <div className="rmt-card rmt-card--warning">
            <strong>Sample size: {report.overall_sample_level.replace("_", " ")}.</strong> {SAMPLE_LABELS[report.overall_sample_level]}{" "}
            Always look at unique player-matches, not raw observation count — one player's game generates many
            alternate-line observations that are not independent evidence.
          </div>

          <div className="rmt-card">
            <h3>Dataset summary</h3>
            <div className="rmt-grid">
              <div><span className="rmt-grid__label">Total observations</span><span className="rmt-grid__value">{report.summary.total_observations}</span></div>
              <div><span className="rmt-grid__label">Settled</span><span className="rmt-grid__value">{report.summary.settled_observations}</span></div>
              <div><span className="rmt-grid__label">Pending</span><span className="rmt-grid__value">{report.summary.pending_observations}</span></div>
              <div><span className="rmt-grid__label">Unique player-matches</span><span className="rmt-grid__value">{report.summary.unique_player_matches}</span></div>
              <div><span className="rmt-grid__label">Unique players</span><span className="rmt-grid__value">{report.summary.unique_players}</span></div>
              <div><span className="rmt-grid__label">Unique matches</span><span className="rmt-grid__value">{report.summary.unique_matches}</span></div>
              <div><span className="rmt-grid__label">Bookmakers</span><span className="rmt-grid__value">{report.summary.bookmakers.join(", ") || "—"}</span></div>
              <div><span className="rmt-grid__label">Date range</span><span className="rmt-grid__value">
                {report.summary.earliest_observed_at ? formatShortDate(report.summary.earliest_observed_at) : "—"}
                {" – "}
                {report.summary.latest_observed_at ? formatShortDate(report.summary.latest_observed_at) : "—"}
              </span></div>
            </div>
          </div>

          <div className="rmt-card">
            <h3>Collection quality</h3>
            <p className="hint">
              Is the collected dataset dense enough to be useful? Raw quotes and frozen observations can legitimately
              diverge (a quote with no live projection yet doesn't get an observation).
            </p>
            <div className="rmt-grid">
              <div><span className="rmt-grid__label">Total raw quotes</span><span className="rmt-grid__value">{report.coverage.total_raw_quotes}</span></div>
              <div><span className="rmt-grid__label">Frozen observations</span><span className="rmt-grid__value">{report.coverage.frozen_observations}</span></div>
              <div><span className="rmt-grid__label">Unique player-matches</span><span className="rmt-grid__value">{report.coverage.unique_player_matches}</span></div>
              <div><span className="rmt-grid__label">Unique matches</span><span className="rmt-grid__value">{report.coverage.unique_matches}</span></div>
              <div><span className="rmt-grid__label">Unique market lines</span><span className="rmt-grid__value">{report.coverage.unique_market_lines}</span></div>
              <div><span className="rmt-grid__label">Market families</span><span className="rmt-grid__value">{report.coverage.market_families.join(", ") || "—"}</span></div>
              <div><span className="rmt-grid__label">Bookmakers</span><span className="rmt-grid__value">{report.coverage.bookmakers.join(", ") || "—"}</span></div>
              <div><span className="rmt-grid__label">Avg snapshots / player-market</span><span className="rmt-grid__value">{num(report.coverage.average_snapshots_per_player_market, 2)}</span></div>
            </div>
          </div>

          <div className="rmt-card">
            <h3>Model vs market ({report.model_vs_market.n_settled_binary} settled)</h3>
            <p className="hint">Market probability source: {report.model_vs_market.market_probability_source}. Lower Brier/log-loss is better.</p>
            <div className="rmt-table-scroll">
              <table className="rmt-table">
                <thead><tr><th></th><th>Brier score</th><th>Log loss</th></tr></thead>
                <tbody>
                  <tr><td>Model</td><td>{num(report.model_vs_market.model_brier)}</td><td>{num(report.model_vs_market.model_log_loss)}</td></tr>
                  <tr><td>Market</td><td>{num(report.model_vs_market.market_brier)}</td><td>{num(report.model_vs_market.market_log_loss)}</td></tr>
                </tbody>
              </table>
            </div>
            <div className="rmt-calibration">
              <div>
                <h4>Model calibration</h4>
                <table className="rmt-table">
                  <thead><tr><th>Predicted range</th><th>n</th><th>Mean predicted</th><th>Mean actual</th></tr></thead>
                  <tbody>{report.model_calibration.map((c) => (
                    <tr key={c.probability_range}><td>{c.probability_range}</td><td>{c.n}</td><td>{pct(c.mean_predicted)}</td><td>{pct(c.mean_actual)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
              <div>
                <h4>Market calibration</h4>
                <table className="rmt-table">
                  <thead><tr><th>Predicted range</th><th>n</th><th>Mean predicted</th><th>Mean actual</th></tr></thead>
                  <tbody>{report.market_calibration.map((c) => (
                    <tr key={c.probability_range}><td>{c.probability_range}</td><td>{c.n}</td><td>{pct(c.mean_predicted)}</td><td>{pct(c.mean_actual)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="rmt-card">
            <h3>Real-world return (illustrative $1 flat stake — not a recommendation)</h3>
            <div className="rmt-grid">
              <div><span className="rmt-grid__label">Settled (W/L)</span><span className="rmt-grid__value">{report.overall_return.n_settled_binary}</span></div>
              <div><span className="rmt-grid__label">Pushed / Voided</span><span className="rmt-grid__value">{report.overall_return.n_pushed} / {report.overall_return.n_voided}</span></div>
              <div><span className="rmt-grid__label">Win rate</span><span className="rmt-grid__value">{pct(report.overall_return.win_rate)}</span></div>
              <div><span className="rmt-grid__label">Average odds</span><span className="rmt-grid__value">{report.overall_return.average_odds ? `$${report.overall_return.average_odds.toFixed(2)}` : "—"}</span></div>
              <div><span className="rmt-grid__label">Flat $1 P/L</span><span className={`rmt-grid__value ${report.overall_return.total_profit_flat_stake >= 0 ? "rmt-pos" : "rmt-neg"}`}>${report.overall_return.total_profit_flat_stake.toFixed(2)}</span></div>
              <div><span className="rmt-grid__label">ROI</span><span className={`rmt-grid__value ${(report.overall_return.roi ?? 0) >= 0 ? "rmt-pos" : "rmt-neg"}`}>{pct(report.overall_return.roi)}</span></div>
            </div>
          </div>

          <BucketTable title="Edge buckets (model-market difference)" buckets={report.edge_buckets} />
          <BucketTable title="Confidence buckets" buckets={report.confidence_buckets} />
          <BucketTable title="Lineup-status buckets" buckets={report.lineup_buckets} />
          <BucketTable title="Timing buckets (hours before kickoff)" buckets={report.timing_buckets} />

          {report.market_open_timing.length > 0 && <MarketOpenTimingTable timing={report.market_open_timing} />}

          {movements && movements.length > 0 && (
            <MarketMovementTable movements={movements} onSelect={setSelectedMovement} />
          )}
        </>
      )}

      {selectedMovement && <QuoteHistoryDrawer movement={selectedMovement} onClose={() => setSelectedMovement(null)} />}

      <Disclaimer />
    </main>
  );
}

export default RealMarketTrackingPage;
