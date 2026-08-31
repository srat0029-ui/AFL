import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchTradingMonitorOverview,
  type DataHealthFinding,
  type ModelMovement,
  type NeedsAttentionEntry,
  type TradingMonitorOverview,
} from "../api/client";
import { formatCompactDateTime } from "../lib/datetime";
import "./TradingMonitorPage.css";

const TIER_CHIP: Record<string, string> = {
  critical: "danger",
  high_priority: "warning",
  review_worthy: "accent",
  raw_detection: "neutral",
};

const SEVERITY_CHIP: Record<string, string> = {
  error: "danger",
  warning: "warning",
  info: "neutral",
};

function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

function fmtSigned(n: number, digits = 1): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;
}

function Chip({ label, variant }: { label: string; variant: string }) {
  return <span className={`chip chip--${variant}`}>{label}</span>;
}

function NeedsAttentionTable({ rows, emptyLabel }: { rows: NeedsAttentionEntry[]; emptyLabel: string }) {
  if (rows.length === 0) return <p className="empty-state">{emptyLabel}</p>;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Tier</th>
            <th>Match</th>
            <th>Market</th>
            <th>Player/Selection</th>
            <th>Detail</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.case_id}>
              <td><Chip label={r.tier.replace(/_/g, " ")} variant={TIER_CHIP[r.tier] ?? "neutral"} /></td>
              <td>
                <Link to={`/matches/${r.match_id}`}>
                  {r.home_team} v {r.away_team}
                </Link>
              </td>
              <td>{r.market_type}{r.threshold !== null ? ` ${r.threshold}+` : ""}</td>
              <td>{r.player_name ?? r.selection ?? "—"}</td>
              <td>{r.detail}</td>
              <td>{r.lifecycle}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ModelMoversTable({ rows }: { rows: ModelMovement[] }) {
  if (rows.length === 0) return <p className="empty-state">No model movements captured yet — this accumulates as the live cycle runs.</p>;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Match</th>
            <th>Value</th>
            <th className="num">Previous</th>
            <th className="num">Current</th>
            <th className="num">Change</th>
            <th>Since</th>
            <th>Lineup</th>
            <th>Flag</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m, i) => {
            const isProbability = m.value_kind === "probability";
            return (
              <tr key={i}>
                <td>
                  <Link to={`/matches/${m.match_id}`}>{m.selection ?? (m.player_id ? `Player ${m.player_id}` : "—")}</Link>
                </td>
                <td>{m.value_type.replace(/_/g, " ")}</td>
                <td className="num">{isProbability ? fmtPct(m.previous_value) : m.previous_value.toFixed(1)}</td>
                <td className="num">{isProbability ? fmtPct(m.current_value) : m.current_value.toFixed(1)}</td>
                <td className="num">{isProbability ? fmtSigned(m.absolute_change * 100, 1) + "pp" : fmtSigned(m.absolute_change)}</td>
                <td>{m.hours_between.toFixed(1)}h ago</td>
                <td>
                  {m.lineup_status_changed ? (
                    <span className="hint">
                      {m.previous_lineup_status ?? "—"} → {m.current_lineup_status ?? "—"}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  {m.is_material ? <Chip label="Material" variant="danger" /> : m.is_notable ? <Chip label="Notable" variant="warning" /> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DataHealthSection({ findings, backlog, liveCycle }: { findings: DataHealthFinding[]; backlog: TradingMonitorOverview["data_health"]["backlog"]; liveCycle: TradingMonitorOverview["data_health"]["live_cycle"] }) {
  return (
    <>
      {findings.length === 0 ? (
        <p className="empty-state">No data-quality findings — everything reads fresh or expected.</p>
      ) : (
        <ul className="trading-monitor__findings">
          {findings.map((f, i) => (
            <li key={i}>
              <Chip label={f.severity} variant={SEVERITY_CHIP[f.severity] ?? "neutral"} /> <strong>{f.label}</strong> — {f.detail}
            </li>
          ))}
        </ul>
      )}
      <div className="stat-strip trading-monitor__section-gap">
        <div className="stat-strip__item">
          <span className="stat-strip__label">Unsettled prop observations</span>
          <span className="stat-strip__value">{backlog.prop_observations_unsettled}</span>
        </div>
        <div className="stat-strip__item">
          <span className="stat-strip__label">Unsettled pricing snapshots</span>
          <span className="stat-strip__value">{backlog.pricing_snapshots_unsettled}</span>
        </div>
        <div className="stat-strip__item">
          <span className="stat-strip__label">Unsettled SGM snapshots</span>
          <span className="stat-strip__value">{backlog.sgm_snapshots_unsettled}</span>
        </div>
        <div className="stat-strip__item">
          <span className="stat-strip__label">Last live cycle</span>
          <span className="stat-strip__value">{liveCycle.last_run_status ?? "—"}</span>
        </div>
      </div>
      <p className="hint">
        Full freshness/run history: <Link to="/live-status">Live Status</Link>.
      </p>
    </>
  );
}

function TradingMonitorPage() {
  const [data, setData] = useState<TradingMonitorOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [matchFilter, setMatchFilter] = useState<number | "all">("all");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [notableOnly, setNotableOnly] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchTradingMonitorOverview()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load trading monitor"))
      .finally(() => setLoading(false));
  }, []);

  const matchOptions = useMemo(() => {
    if (!data) return [];
    const ids = new Set<number>();
    for (const r of [...data.needs_attention, ...data.market_movers, ...data.dispersion]) ids.add(r.match_id);
    for (const m of data.model_movers) ids.add(m.match_id);
    return Array.from(ids).sort((a, b) => a - b);
  }, [data]);

  const filterCases = (rows: NeedsAttentionEntry[]) =>
    rows.filter((r) => (matchFilter === "all" || r.match_id === matchFilter) && (severityFilter === "all" || r.tier === severityFilter));

  const filteredModelMovers = useMemo(() => {
    if (!data) return [];
    return data.model_movers.filter((m) => (matchFilter === "all" || m.match_id === matchFilter) && (!notableOnly || m.is_notable));
  }, [data, matchFilter, notableOnly]);

  return (
    <main className="trading-monitor-page">
      <h1 className="page-title">Trading Monitor</h1>
      <p className="subtitle">
        Operational view of what changed, what looks unusual, and what's stale or incomplete — for a pricing/trading
        analyst to investigate, not a betting-tip surface. Cases and dispersion below are computed and scored by{" "}
        <Link to="/market-monitor">Market Monitor</Link>'s own detection engine, reused here unchanged; model movement
        and SGM sections are new this phase.
      </p>

      {error && <p className="trading-monitor-page__error">{error}</p>}
      {loading && <p className="loading-state">Loading…</p>}

      {data && !loading && (
        <>
          <div className="stat-strip">
            <div className="stat-strip__item">
              <span className="stat-strip__label">Upcoming matches</span>
              <span className="stat-strip__value">{data.summary.n_upcoming_matches}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Fresh markets</span>
              <span className="stat-strip__value">{data.summary.n_fresh_markets}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Active warnings/errors</span>
              <span className="stat-strip__value">{data.summary.n_active_error_or_warning}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Material model movements</span>
              <span className="stat-strip__value">{data.summary.n_material_model_movements}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Market movement cases</span>
              <span className="stat-strip__value">{data.summary.n_market_movement_cases}</span>
            </div>
          </div>

          <div className="trading-monitor-page__filters">
            <label>
              Match
              <select value={matchFilter} onChange={(e) => setMatchFilter(e.target.value === "all" ? "all" : Number(e.target.value))}>
                <option value="all">All matches</option>
                {matchOptions.map((id) => (
                  <option key={id} value={id}>
                    Match #{id}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Tier
              <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
                <option value="all">All tiers</option>
                <option value="critical">Critical</option>
                <option value="high_priority">High priority</option>
                <option value="review_worthy">Review worthy</option>
                <option value="raw_detection">Raw detection</option>
              </select>
            </label>
            <label>
              <input type="checkbox" checked={notableOnly} onChange={(e) => setNotableOnly(e.target.checked)} />
              Notable model movements only
            </label>
          </div>

          <section className="trading-monitor-section">
            <h2>Needs Attention</h2>
            <p className="hint">Critical and high-priority cases from Market Monitor's trader inbox.</p>
            <NeedsAttentionTable rows={filterCases(data.needs_attention)} emptyLabel="Nothing critical or high-priority right now." />
          </section>

          <section className="trading-monitor-section">
            <h2>Model Movers</h2>
            <p className="hint">Team win probability and player projection movement since the previous observation.</p>
            <ModelMoversTable rows={filteredModelMovers} />
          </section>

          <section className="trading-monitor-section">
            <h2>Market Movers</h2>
            <p className="hint">Bookmaker/consensus movement cases already detected by Market Monitor.</p>
            <NeedsAttentionTable rows={filterCases(data.market_movers)} emptyLabel="No sharp market movement detected right now." />
          </section>

          <section className="trading-monitor-section">
            <h2>Bookmaker Dispersion</h2>
            <p className="hint">Markets where currently-available bookmakers disagree most.</p>
            <NeedsAttentionTable rows={filterCases(data.dispersion)} emptyLabel="No unusually large bookmaker dispersion right now." />
          </section>

          <section className="trading-monitor-section">
            <h2>SGM Monitoring</h2>
            <p className="hint">
              {data.sgm.n_recent_snapshots === 0
                ? "No Same Game Multi snapshots yet — this populates as the live cycle prices real combos."
                : `${data.sgm.n_recent_snapshots} recent SGM snapshot(s) considered.`}
            </p>
            {data.sgm.coefficient_provenance.length > 0 && (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Market</th>
                      <th className="num">Slope</th>
                      <th className="num">Intercept</th>
                      <th className="num">N observations</th>
                      <th>Fitted</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sgm.coefficient_provenance.map((c) => (
                      <tr key={c.market}>
                        <td>{c.market}</td>
                        <td className="num">{c.slope.toFixed(4)}</td>
                        <td className="num">{c.intercept.toFixed(4)}</td>
                        <td className="num">{c.n_observations.toLocaleString()}</td>
                        <td>{formatCompactDateTime(c.fitted_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {data.sgm.largest_correlation_adjustments.length > 0 && (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Match</th>
                      <th>Legs</th>
                      <th className="num">Joint</th>
                      <th className="num">Naive</th>
                      <th className="num">Correlation adj.</th>
                      <th>Magnitude</th>
                      <th>Horizon</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.sgm.largest_correlation_adjustments.map((e, i) => (
                      <tr key={i}>
                        <td>
                          <Link to={`/matches/${e.match_id}`}>Match #{e.match_id}</Link>
                        </td>
                        <td>{e.n_legs}</td>
                        <td className="num">{fmtPct(e.model_probability)}</td>
                        <td className="num">{fmtPct(e.naive_independence_probability)}</td>
                        <td className="num">{fmtSigned(e.correlation_adjustment_pp, 2)}pp</td>
                        <td>{e.correlation_adjustment_bucket}</td>
                        <td>{e.snapshot_horizon}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="trading-monitor-section">
            <h2>Data Health</h2>
            <DataHealthSection findings={data.data_health.findings} backlog={data.data_health.backlog} liveCycle={data.data_health.live_cycle} />
          </section>

          <section className="trading-monitor-section">
            <h2>Recent System Activity</h2>
            <ul className="trading-monitor__activity-list">
              {data.recent_activity.map((r, i) => (
                <li key={i}>
                  {formatCompactDateTime(r.run_at)} — <Chip label={r.overall_status} variant={r.overall_status === "ok" ? "success" : r.overall_status === "blocked" ? "danger" : "warning"} />{" "}
                  {r.n_steps_failed > 0 ? `${r.n_steps_failed} step(s) failed` : "all steps ok"}
                </li>
              ))}
            </ul>
            <p className="hint">
              Full run detail: <Link to="/live-status">Live Status</Link>.
            </p>
          </section>
        </>
      )}
    </main>
  );
}

export default TradingMonitorPage;
