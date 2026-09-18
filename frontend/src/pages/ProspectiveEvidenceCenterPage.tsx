import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Disclaimer from "../components/Disclaimer";
import PageHeader from "../components/ui/PageHeader";
import { formatPreciseDateTime } from "../lib/datetime";
import {
  fetchProspectiveEvidenceCenter,
  type AlertTypeEffectiveness,
  type EffectivenessView,
  type ProspectiveEvidenceCenter,
  type ProspectiveSplit,
  type SampleSizeLevel,
  type SgmProspectiveSplit,
} from "../api/client";
import "./ProspectiveEvidenceCenterPage.css";

function fmt(n: number | null | undefined, digits = 3): string {
  return n === null || n === undefined ? "—" : n.toFixed(digits);
}
function pct1(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(1)}%`;
}

const SAMPLE_LEVEL_LABELS: Record<SampleSizeLevel, string> = {
  exploratory: "Exploratory only — fewer than 30 settled player-matches.",
  low_confidence: "Low-confidence evidence — fewer than 100 settled player-matches.",
  still_developing: "Still developing — fewer than 300 settled player-matches.",
  informative: "Larger sample — increasingly informative, still not a formal significance test.",
};

function SampleLevelBadge({ level }: { level: SampleSizeLevel }) {
  return (
    <span className={`pec-sample pec-sample--${level}`} title={SAMPLE_LEVEL_LABELS[level]}>
      {level.replace("_", " ")}
    </span>
  );
}

function ExploratoryBadge({ exploratory }: { exploratory: boolean }) {
  if (!exploratory) return null;
  return (
    <span className="pec-sample pec-sample--exploratory" title="Fewer than 30 settled, scored observations — too small to report a stable Brier/log-loss figure.">
      exploratory
    </span>
  );
}

// --- Dataset overview: every genuinely distinct formal prospective dataset
// in the system, normalised into one row shape purely for display — no new
// calculation, every number below is read directly off the composed report.
interface OverviewRow {
  key: string;
  label: string;
  frozenLabel: string;
  frozen: number;
  settledLabel: string;
  settled: number;
  uniqueLabel: string;
  unique: number | null;
  note: string;
}

function buildOverviewRows(data: ProspectiveEvidenceCenter): OverviewRow[] {
  const team = data.pricing_evaluation.by_market_family.find((s) => s.label === "team");
  const disposals = data.pricing_evaluation.by_market_family.find((s) => s.label === "player_disposals");
  const goals = data.pricing_evaluation.by_market_family.find((s) => s.label === "player_goals");
  const rows: OverviewRow[] = [
    {
      key: "team",
      label: "Team markets (pricing engine)",
      frozenLabel: "Frozen prices",
      frozen: team ? team.n_settled : 0,
      settledLabel: "Settled",
      settled: team ? team.n_settled : 0,
      uniqueLabel: "Unique match events",
      unique: team ? team.n_unique_events : null,
      note: "h2h/line/total prices frozen pre-kickoff by the pricing engine.",
    },
    {
      key: "player_disposals",
      label: "Player disposals (pricing engine)",
      frozenLabel: "Frozen prices",
      frozen: disposals ? disposals.n_settled : 0,
      settledLabel: "Settled",
      settled: disposals ? disposals.n_settled : 0,
      uniqueLabel: "Unique player-match events",
      unique: disposals ? disposals.n_unique_events : null,
      note: "Disposal prop prices frozen pre-kickoff by the pricing engine.",
    },
    {
      key: "player_goals",
      label: "Player goals (pricing engine)",
      frozenLabel: "Frozen prices",
      frozen: goals ? goals.n_settled : 0,
      settledLabel: "Settled",
      settled: goals ? goals.n_settled : 0,
      uniqueLabel: "Unique player-match events",
      unique: goals ? goals.n_unique_events : null,
      note: "Goal prop prices frozen pre-kickoff by the pricing engine.",
    },
    {
      key: "sgm",
      label: "Same Game Multi",
      frozenLabel: "Frozen combos",
      frozen: data.sgm_evaluation.n_frozen_total,
      settledLabel: "Settled",
      settled: data.sgm_evaluation.n_settled,
      uniqueLabel: "Unique combos (closing snapshot)",
      unique: data.sgm_evaluation.n_unique_combos,
      note: "Joint multi-leg prices frozen pre-kickoff, deduped to one closing snapshot per real combo.",
    },
    {
      key: "real_market",
      label: "Real market tracking (player props)",
      frozenLabel: "Frozen observations",
      frozen: data.real_market_tracking.summary.total_observations,
      settledLabel: "Settled",
      settled: data.real_market_tracking.summary.settled_observations,
      uniqueLabel: "Unique player-matches",
      unique: data.real_market_tracking.summary.unique_player_matches,
      note: "Real bookmaker prop prices logged and frozen against the model's live belief.",
    },
    {
      key: "market_monitor",
      label: "Market Monitor prospective cases",
      frozenLabel: "Currently open",
      frozen: data.market_monitor.coverage.n_frozen_cases,
      settledLabel: "Resolved",
      settled: data.market_monitor.prospective.summary.n_resolved,
      uniqueLabel: "Unique markets (frozen)",
      unique: data.market_monitor.prospective.summary.n_unique_markets,
      note: "Anomaly cases frozen automatically while their match was still scheduled.",
    },
  ];
  return rows;
}

function DatasetOverviewTable({ data }: { data: ProspectiveEvidenceCenter }) {
  const rows = buildOverviewRows(data);
  return (
    <div className="pec-card">
      <h2>Dataset overview</h2>
      <p className="hint">
        Every genuinely distinct formal prospective dataset in the system, side by side. "Frozen" and "Settled" counts
        can include repeated snapshots of the same player/match/market (alternate lines, multiple horizons) — always
        read the <strong>Unique</strong> column as the real, independent sample size, never the raw counts.
      </p>
      <div className="pec-table-scroll">
        <table className="pec-table">
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Frozen</th>
              <th>Settled</th>
              <th>Unique (independent sample)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td>
                  <div className="pec-table__name">{r.label}</div>
                  <div className="hint">{r.note}</div>
                </td>
                <td className="num">{r.frozen.toLocaleString()}</td>
                <td className="num">{r.settled.toLocaleString()}</td>
                <td className="num">{r.unique === null ? "—" : r.unique.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SplitMiniTable({ splits }: { splits: ProspectiveSplit[] }) {
  if (splits.length === 0) {
    return <p className="empty-state">No settled evidence for this split yet.</p>;
  }
  return (
    <div className="pec-table-scroll">
      <table className="pec-table">
        <thead>
          <tr>
            <th>Split</th>
            <th>Settled</th>
            <th>Unique events</th>
            <th title="Mean squared error between predicted probability and actual outcome. Lower is better.">Model Brier</th>
            <th>Market Brier</th>
            <th title="Penalises confident-and-wrong predictions more heavily. Lower is better.">Model log-loss</th>
            <th>Market log-loss</th>
            <th title="Expected calibration error">ECE</th>
          </tr>
        </thead>
        <tbody>
          {splits.map((s) => (
            <tr key={s.label}>
              <td>
                {s.label} <ExploratoryBadge exploratory={s.exploratory} />
              </td>
              <td className="num">{s.n_settled}</td>
              <td className="num">{s.n_unique_events}</td>
              <td className="num">{fmt(s.model_brier)}</td>
              <td className="num">{fmt(s.market_brier)}</td>
              <td className="num">{fmt(s.model_log_loss)}</td>
              <td className="num">{fmt(s.market_log_loss)}</td>
              <td className="num">{fmt(s.model_calibration_ece, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SgmMiniTable({ splits }: { splits: SgmProspectiveSplit[] }) {
  if (splits.length === 0) {
    return <p className="empty-state">No settled evidence for this split yet.</p>;
  }
  return (
    <div className="pec-table-scroll">
      <table className="pec-table">
        <thead>
          <tr>
            <th>Split</th>
            <th>Settled</th>
            <th>Unique combos</th>
            <th>Model Brier</th>
            <th>Naive Brier</th>
            <th>Model log-loss</th>
            <th>Naive log-loss</th>
          </tr>
        </thead>
        <tbody>
          {splits.map((s) => (
            <tr key={s.label}>
              <td>
                {s.label} <ExploratoryBadge exploratory={s.exploratory} />
              </td>
              <td className="num">{s.n_settled}</td>
              <td className="num">{s.n_unique_combos}</td>
              <td className="num">{fmt(s.model_brier)}</td>
              <td className="num">{fmt(s.naive_brier)}</td>
              <td className="num">{fmt(s.model_log_loss)}</td>
              <td className="num">{fmt(s.naive_log_loss)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EffectivenessMiniTable({ byAlertType }: { byAlertType: AlertTypeEffectiveness[] }) {
  if (byAlertType.every((a) => a.n_resolved === 0)) {
    return <p className="empty-state">No resolved cases of any alert type yet.</p>;
  }
  return (
    <div className="pec-table-scroll">
      <table className="pec-table">
        <thead>
          <tr>
            <th>Alert type</th>
            <th>Resolved</th>
            <th>Toward model</th>
            <th>Away from model</th>
            <th>Persisted</th>
            <th>Inconclusive</th>
          </tr>
        </thead>
        <tbody>
          {byAlertType.map((a) => (
            <tr key={a.alert_type_family}>
              <td>
                {a.alert_type_family.replace(/_/g, " ")} {a.sample_label && <span className="pec-sample pec-sample--exploratory">{a.sample_label}</span>}
              </td>
              <td className="num">{a.n_resolved}</td>
              <td className="num">{pct1(a.pct_market_moved_toward_model)}</td>
              <td className="num">{pct1(a.pct_market_moved_away_from_model)}</td>
              <td className="num">{pct1(a.pct_persisted_to_kickoff)}</td>
              <td className="num">{pct1(a.pct_inconclusive)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EffectivenessSummaryGrid({ view }: { view: EffectivenessView }) {
  const s = view.summary;
  return (
    <div className="pec-grid">
      <div><span className="pec-grid__label">Resolved (settled)</span><span className="pec-grid__value">{s.n_resolved}</span></div>
      <div><span className="pec-grid__label">Unique markets</span><span className="pec-grid__value">{s.n_unique_markets}</span></div>
      <div><span className="pec-grid__label">Median time to resolution</span><span className="pec-grid__value">{s.median_time_to_resolution_hours === null ? "—" : `${s.median_time_to_resolution_hours.toFixed(1)}h`}</span></div>
      <div><span className="pec-grid__label">Consensus moved toward model</span><span className="pec-grid__value">{pct1(s.pct_consensus_moved_toward_model)}</span></div>
      <div><span className="pec-grid__label">Consensus moved away from model</span><span className="pec-grid__value">{pct1(s.pct_consensus_moved_away_from_model)}</span></div>
      <div><span className="pec-grid__label">Persisted to kickoff</span><span className="pec-grid__value">{pct1(s.pct_persisted_to_kickoff)}</span></div>
      <div><span className="pec-grid__label">Outlier bookmaker converged ({s.n_outlier_eligible} eligible)</span><span className="pec-grid__value">{pct1(s.pct_outlier_converged)}</span></div>
      <div><span className="pec-grid__label">Stale-after-context repriced ({s.n_stale_context_eligible} eligible)</span><span className="pec-grid__value">{pct1(s.pct_stale_context_repriced)}</span></div>
    </div>
  );
}

function ProspectiveEvidenceCenterPage() {
  const [data, setData] = useState<ProspectiveEvidenceCenter | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchProspectiveEvidenceCenter()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load prospective evidence"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="pec-page">
      <PageHeader
        eyebrow="Advanced"
        title="Live Model Results"
        description="Every formal, post-boundary prospective result the system has produced — predictions and prices frozen before kickoff, then settled against real outcomes, never overwritten or retuned. Composes the pricing engine, Same Game Multi, real market tracking, and Market Monitor datasets; it computes nothing new of its own."
      />

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && data && (
        <>
          <div className={`pec-card pec-boundary ${data.boundary.boundary_active ? "pec-boundary--active" : "pec-boundary--inactive"}`}>
            <h2>Formal prospective boundary</h2>
            {data.boundary.boundary_active ? (
              <p>
                Formal prospective tracking began <strong>{formatPreciseDateTime(data.boundary.tracking_start_at!)}</strong> (Australia/Hobart).
                Every dataset below is scoped to predictions, prices, and cases frozen on or after that moment — this
                timestamp is the single source of truth for what counts as formal prospective evidence and is never
                moved to make results look better.
              </p>
            ) : (
              <p>{data.boundary.environment_note}</p>
            )}
          </div>

          <DatasetOverviewTable data={data} />

          <section className="pec-section">
            <h2>Player prop prospective evidence</h2>
            <p className="hint">
              Two independent views of the same underlying question — does the model beat the market on player props —
              scored two different ways: the pricing engine's own frozen prices vs. frozen market consensus, and real
              logged bookmaker prices vs. the model's live belief at that moment.
            </p>

            <h3>Pricing engine — model vs. frozen market consensus</h3>
            {!data.pricing_evaluation.has_settled_data ? (
              <p className="empty-state">{data.pricing_evaluation.message}</p>
            ) : (
              <SplitMiniTable
                splits={data.pricing_evaluation.by_market_family.filter((s) => s.label !== "team")}
              />
            )}

            <h3>Real market tracking — model vs. real bookmaker prices</h3>
            {data.real_market_tracking.summary.total_observations === 0 ? (
              <p className="empty-state">
                No real market observations logged yet — this dataset grows automatically as live odds are captured
                each round.
              </p>
            ) : (
              <>
                <div className="pec-grid">
                  <div><span className="pec-grid__label">Settled (W/L)</span><span className="pec-grid__value">{data.real_market_tracking.model_vs_market.n_settled_binary}</span></div>
                  <div><span className="pec-grid__label">Unique player-matches</span><span className="pec-grid__value">{data.real_market_tracking.summary.unique_player_matches}</span></div>
                  <div><span className="pec-grid__label">Model Brier</span><span className="pec-grid__value">{fmt(data.real_market_tracking.model_vs_market.model_brier)}</span></div>
                  <div><span className="pec-grid__label">Market Brier</span><span className="pec-grid__value">{fmt(data.real_market_tracking.model_vs_market.market_brier)}</span></div>
                  <div><span className="pec-grid__label">Model log-loss</span><span className="pec-grid__value">{fmt(data.real_market_tracking.model_vs_market.model_log_loss)}</span></div>
                  <div><span className="pec-grid__label">Market log-loss</span><span className="pec-grid__value">{fmt(data.real_market_tracking.model_vs_market.market_log_loss)}</span></div>
                  <div><span className="pec-grid__label">Sample</span><span className="pec-grid__value"><SampleLevelBadge level={data.real_market_tracking.overall_sample_level} /></span></div>
                </div>
                <p className="hint">
                  Market probability source: {data.real_market_tracking.model_vs_market.market_probability_source}.{" "}
                  <Link to="/real-market-tracking">View full Real Market Tracking breakdown →</Link>
                </p>
              </>
            )}
          </section>

          <section className="pec-section">
            <h2>Team market prospective evidence</h2>
            <p className="hint">h2h/line/total prices frozen pre-kickoff by the pricing engine, scored against frozen market consensus.</p>
            {!data.pricing_evaluation.has_settled_data ? (
              <p className="empty-state">{data.pricing_evaluation.message}</p>
            ) : (
              <SplitMiniTable splits={data.pricing_evaluation.by_market_family.filter((s) => s.label === "team")} />
            )}
            <p className="hint">
              <Link to="/model-registry">View full Model Registry breakdown →</Link>
            </p>
          </section>

          <section className="pec-section">
            <h2>Same Game Multi (SGM) prospective evidence</h2>
            <p className="hint">
              Joint multi-leg prices frozen pre-kickoff, scored against naive independence (always available) and a
              genuine bookmaker SGM price (not yet available from any odds provider integration — shown as "no data"
              rather than a fabricated number where it applies).
            </p>
            {!data.sgm_evaluation.has_settled_data ? (
              <p className="empty-state">{data.sgm_evaluation.message}</p>
            ) : (
              <>
                <div className="pec-grid">
                  <div><span className="pec-grid__label">Settled combos</span><span className="pec-grid__value">{data.sgm_evaluation.n_settled}</span></div>
                  <div><span className="pec-grid__label">Unique combos</span><span className="pec-grid__value">{data.sgm_evaluation.n_unique_combos}</span></div>
                  <div><span className="pec-grid__label">Model Brier</span><span className="pec-grid__value">{fmt(data.sgm_evaluation.overall?.model_brier)}</span></div>
                  <div><span className="pec-grid__label">Naive independence Brier</span><span className="pec-grid__value">{fmt(data.sgm_evaluation.overall?.naive_brier)}</span></div>
                </div>
                <h3>By number of legs</h3>
                <SgmMiniTable splits={data.sgm_evaluation.by_n_legs} />
              </>
            )}
            <p className="hint">
              <Link to="/model-registry">View full Same Game Multi breakdown →</Link>
            </p>
          </section>

          <section className="pec-section">
            <h2>Market Monitor — prospective coverage &amp; effectiveness</h2>
            <p className="hint">
              Is the anomaly-detection system actually watching upcoming matches, and were its high-priority alerts
              useful? Purely descriptive — nothing here retunes a threshold, weight, or probability.
            </p>

            <h3>
              Genuine prospective effectiveness{" "}
              {data.market_monitor.prospective.summary.sample_label && (
                <span className="pec-sample pec-sample--exploratory">{data.market_monitor.prospective.summary.sample_label}</span>
              )}
            </h3>
            {data.market_monitor.prospective.summary.n_resolved === 0 ? (
              <p className="empty-state">No genuinely prospective cases have resolved (settled) yet.</p>
            ) : (
              <>
                <EffectivenessSummaryGrid view={data.market_monitor.prospective} />
                <EffectivenessMiniTable byAlertType={data.market_monitor.prospective.by_alert_type} />
              </>
            )}
            <p className="hint">
              <Link to="/market-monitor">View full Market Monitor Effectiveness dashboard →</Link>
            </p>

            <details className="disclosure">
              <summary>Coverage detail &amp; historical backfill (not formal prospective evidence)</summary>
              <h3>Coverage (operational health)</h3>
              <div className="pec-grid">
                <div><span className="pec-grid__label">Upcoming matches monitored</span><span className="pec-grid__value">{data.market_monitor.coverage.n_upcoming_matches_monitored}</span></div>
                <div><span className="pec-grid__label">High/Critical cases currently frozen</span><span className="pec-grid__value">{data.market_monitor.coverage.n_frozen_cases}</span></div>
                <div><span className="pec-grid__label">Cases with 2+ follow-ups</span><span className="pec-grid__value">{data.market_monitor.coverage.n_cases_with_2plus_followups}</span></div>
                <div><span className="pec-grid__label">Cases with 3+ follow-ups</span><span className="pec-grid__value">{data.market_monitor.coverage.n_cases_with_3plus_followups}</span></div>
                <div><span className="pec-grid__label">Earliest hours-before-kickoff captured</span><span className="pec-grid__value">{data.market_monitor.coverage.earliest_hours_before_kickoff_captured === null ? "—" : `${data.market_monitor.coverage.earliest_hours_before_kickoff_captured.toFixed(1)}h`}</span></div>
                <div><span className="pec-grid__label">Latest pre-kickoff capture</span><span className="pec-grid__value">{data.market_monitor.coverage.latest_pre_kickoff_capture_hours === null ? "—" : `${data.market_monitor.coverage.latest_pre_kickoff_capture_hours.toFixed(1)}h`}</span></div>
              </div>

              <div className="pec-historical">
                <h3>Historical backfill (not formal prospective evidence)</h3>
                <p className="hint">
                  Cases backfilled by a one-off historical script against already-completed matches. Useful for
                  exercising the pipeline, but explicitly excluded from any judgement about real alert quality — kept
                  here strictly separate from the genuine prospective evidence above.
                </p>
                {data.market_monitor.retrospective.summary.n_resolved === 0 ? (
                  <p className="empty-state">No retrospective backfill cases have resolved yet.</p>
                ) : (
                  <EffectivenessSummaryGrid view={data.market_monitor.retrospective} />
                )}
              </div>
            </details>
          </section>

          <div className="pec-card pec-footnote">
            <h2>Formal prospective evidence vs. historical/backtested analysis</h2>
            <p className="hint">
              Everything on this page is <strong>formal, post-boundary, prospective</strong> evidence — predictions
              frozen before an outcome was known, matched to that outcome after the fact. It is never mixed with, and
              should never be confused with, the separate <strong>historical backtest</strong> datasets elsewhere in
              this product: <Link to="/model-registry">Model Registry's historical backtest</Link>, the{" "}
              <Link to="/backtest">Backtesting</Link> page's synthetic 2016-2025 evaluation, and Market Monitor's own
              retrospective backfill view (shown separately above). Those datasets answer "how would this have
              performed historically" — this page answers "what has actually happened, live, since tracking began."
            </p>
          </div>
        </>
      )}

      <Disclaimer />
    </main>
  );
}

export default ProspectiveEvidenceCenterPage;
