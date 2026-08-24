import { useEffect, useMemo, useState } from "react";
import {
  fetchAnomalies,
  fetchCases,
  setCaseStatus,
  type AnomalyAlert,
  type AnomalyCase,
  type TraderInbox,
} from "../api/client";
import "./MarketMonitorPage.css";

type TabKey = "priority" | "context" | "outliers" | "divergence" | "curve" | "all";

const TABS: { key: TabKey; label: string; alertType?: string }[] = [
  { key: "priority", label: "Priority Review" },
  { key: "context", label: "Context / Stale" },
  { key: "outliers", label: "Bookmaker Outliers", alertType: "BOOKMAKER_VS_CONSENSUS_OUTLIER" },
  { key: "divergence", label: "Model vs Consensus", alertType: "MODEL_VS_MARKET_DIVERGENCE" },
  { key: "curve", label: "Pricing Curve QA" },
  { key: "all", label: "All Detections" },
];

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function SeverityBadge({ label }: { label: string }) {
  return <span className={`market-monitor__severity market-monitor__severity--${label}`}>{label.replace("_", " ")}</span>;
}

function LifecycleBadge({ lifecycle }: { lifecycle: string }) {
  return <span className={`market-monitor__lifecycle market-monitor__lifecycle--${lifecycle}`}>{lifecycle.replace("_", " ")}</span>;
}

function CaseDrawer({ c, onStatusChange }: { c: AnomalyCase; onStatusChange: (status: string | null) => void }) {
  return (
    <div className="market-monitor__case-drawer">
      <p className="market-monitor__detail">{c.primary_alert.detail}</p>
      {c.supporting_alert_types.length > 0 && (
        <p className="hint">Supporting evidence: {c.supporting_alert_types.join(", ")}</p>
      )}
      <div className="market-monitor__meta">
        <span>persistence: {c.persistence_label} ({c.n_snapshots} snapshot(s))</span>
        <span>lifecycle: {c.lifecycle}</span>
        {c.model_support !== null && <span>{c.model_support ? "model-supported outlier" : "market-only anomaly"}</span>}
        <span>first detected: {new Date(c.first_detected).toLocaleString()}</span>
        <span>latest detected: {new Date(c.latest_detected).toLocaleString()}</span>
      </div>

      <table className="market-monitor__components">
        <thead>
          <tr>
            <th>Score component</th>
            <th>Raw</th>
            <th>Contribution</th>
            <th>Why</th>
          </tr>
        </thead>
        <tbody>
          {c.components
            .filter((comp) => comp.contribution > 0.01)
            .sort((a, b) => b.contribution - a.contribution)
            .map((comp) => (
              <tr key={comp.name}>
                <td>{comp.name}</td>
                <td>{comp.raw_value === null ? "—" : comp.raw_value.toFixed(2)}</td>
                <td>+{comp.contribution.toFixed(1)}</td>
                <td className="market-monitor__component-explain">{comp.explanation}</td>
              </tr>
            ))}
        </tbody>
      </table>

      {c.bookmakers.length > 0 && (
        <p className="hint">Bookmaker(s) involved: {c.bookmakers.join(", ")}</p>
      )}

      <div className="market-monitor__status-actions">
        <span className="hint">Mark this case:</span>
        {["reviewed", "acknowledged", "dismissed"].map((s) => (
          <button key={s} className={c.manual_status === s ? "market-monitor__status-btn market-monitor__status-btn--active" : "market-monitor__status-btn"} onClick={() => onStatusChange(s)}>
            {s}
          </button>
        ))}
        {c.manual_status && (
          <button className="market-monitor__status-btn" onClick={() => onStatusChange(null)}>
            clear
          </button>
        )}
      </div>
    </div>
  );
}

function CaseRow({ c, expanded, onToggle, onStatusChange }: { c: AnomalyCase; expanded: boolean; onToggle: () => void; onStatusChange: (status: string | null) => void }) {
  return (
    <>
      <tr className="market-monitor__row" onClick={onToggle}>
        <td>
          <SeverityBadge label={c.tier} />
        </td>
        <td>{c.priority_score.toFixed(1)}</td>
        <td className="market-monitor__type">{c.primary_alert.alert_type}</td>
        <td>
          {c.home_team} v {c.away_team}
        </td>
        <td>{c.player_name ?? c.selection ?? "—"}</td>
        <td>
          {c.market_type}
          {c.threshold !== null ? ` ${c.threshold}+` : ""}
        </td>
        <td>{fmtPct(c.primary_alert.model_probability)}</td>
        <td>{fmtPct(c.primary_alert.market_consensus_probability)}</td>
        <td>
          <LifecycleBadge lifecycle={c.lifecycle} />
        </td>
        <td>{c.manual_status ?? "—"}</td>
      </tr>
      {expanded && (
        <tr className="market-monitor__drawer">
          <td colSpan={10}>
            <CaseDrawer c={c} onStatusChange={onStatusChange} />
          </td>
        </tr>
      )}
    </>
  );
}

function AlertRow({ alert, expanded, onToggle }: { alert: AnomalyAlert; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="market-monitor__row" onClick={onToggle}>
        <td>
          <SeverityBadge label={alert.severity} />
        </td>
        <td className="market-monitor__type">{alert.alert_type}</td>
        <td>
          {alert.home_team} v {alert.away_team}
        </td>
        <td>{alert.player_name ?? alert.selection ?? "—"}</td>
        <td>
          {alert.market_type}
          {alert.threshold !== null ? ` ${alert.threshold}+` : ""}
        </td>
        <td>{fmtPct(alert.model_probability)}</td>
        <td>{fmtPct(alert.market_consensus_probability)}</td>
        <td>{alert.freshness ?? "—"}</td>
        <td>{new Date(alert.generated_at).toLocaleString()}</td>
      </tr>
      {expanded && (
        <tr className="market-monitor__drawer">
          <td colSpan={9}>
            <p className="market-monitor__detail">{alert.detail}</p>
            <div className="market-monitor__meta">
              <span>reason_code: {alert.reason_code}</span>
              <span>model_version: {alert.model_version ?? "—"}</span>
              <span>lineup_status: {alert.lineup_status ?? "—"}</span>
              {alert.context_state && <span>context: {alert.context_state}</span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function MarketMonitorPage() {
  const [tab, setTab] = useState<TabKey>("priority");
  const [inbox, setInbox] = useState<TraderInbox | null>(null);
  const [allAlerts, setAllAlerts] = useState<AnomalyAlert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setExpandedKey(null);
    setError(null);
    if (tab === "all") {
      fetchAnomalies({ limit: 2000 })
        .then((r) => setAllAlerts(r.alerts))
        .catch((err) => setError(err instanceof Error ? err.message : "Failed to load detections"))
        .finally(() => setLoading(false));
      return;
    }
    const tabDef = TABS.find((t) => t.key === tab)!;
    const params = tab === "context" ? {} : tabDef.alertType ? { alertType: tabDef.alertType, limit: 100 } : { limit: 20 };
    fetchCases(params)
      .then((r) => {
        if (tab === "context") {
          r.cases = r.cases.filter((c) => c.primary_alert.alert_type.startsWith("STALE_") || c.supporting_alert_types.some((t) => t.startsWith("STALE_")));
        }
        if (tab === "curve") {
          r.cases = r.cases.filter((c) => ["NON_MONOTONIC_PLAYER_PRICE_CURVE", "ADJACENT_THRESHOLD_JUMP"].some((t) => c.primary_alert.alert_type === t || c.supporting_alert_types.includes(t)));
        }
        setInbox(r);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load cases"))
      .finally(() => setLoading(false));
  }, [tab]);

  const handleStatusChange = (caseId: string, status: string | null) => {
    setCaseStatus(caseId, status).then((r) => {
      setInbox((prev) => (prev ? { ...prev, cases: prev.cases.map((c) => (c.case_id === caseId ? { ...c, manual_status: r.manual_status } : c)) } : prev));
    });
  };

  const isCaseTab = tab !== "all";
  const cases = useMemo(() => (tab === "context" || tab === "curve" ? inbox?.cases.slice(0, tab === "context" ? 100 : 100) : inbox?.cases) ?? [], [inbox, tab]);

  return (
    <main className="market-monitor-page">
      <h1>Trader Inbox — Market Monitor</h1>
      <p className="subtitle">
        A neutral, read-only comparison of this engine's own pricing against real bookmaker markets — deduplicated
        into cases and ranked by a transparent, rule-based priority score (every component visible below). Not a
        "find me a bet" surface, and nothing here adjusts a probability or confidence value.
      </p>

      {inbox && (
        <p className="hint">
          {inbox.total_raw_alerts} raw detections → {inbox.total_cases} cases ·{" "}
          {inbox.tier_counts.map((t) => `${t.count} ${t.tier}`).join(" · ")} · generated{" "}
          {new Date(inbox.generated_at).toLocaleString()}
        </p>
      )}

      <div className="market-monitor__tabs">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? "market-monitor__tab market-monitor__tab--active" : "market-monitor__tab"} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {error && <p className="market-monitor-page__error">{error}</p>}
      {loading && <p>Loading…</p>}

      {isCaseTab && !loading && (
        <>
          {cases.length === 0 && <p className="empty-state">No cases in this view right now.</p>}
          {cases.length > 0 && (
            <div className="market-monitor__table-wrap">
              <table className="market-monitor__table">
                <thead>
                  <tr>
                    <th>Tier</th>
                    <th>Score</th>
                    <th>Primary type</th>
                    <th>Match</th>
                    <th>Player/Selection</th>
                    <th>Market</th>
                    <th>Model %</th>
                    <th>Consensus %</th>
                    <th>Lifecycle</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => (
                    <CaseRow key={c.case_id} c={c} expanded={expandedKey === c.case_id} onToggle={() => setExpandedKey(expandedKey === c.case_id ? null : c.case_id)} onStatusChange={(s) => handleStatusChange(c.case_id, s)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {tab === "all" && !loading && (
        <>
          <p className="hint">{allAlerts.length} raw detections (every underlying finding is preserved here, never deleted).</p>
          {allAlerts.length > 0 && (
            <div className="market-monitor__table-wrap">
              <table className="market-monitor__table">
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Type</th>
                    <th>Match</th>
                    <th>Player/Selection</th>
                    <th>Market</th>
                    <th>Model %</th>
                    <th>Consensus %</th>
                    <th>Freshness</th>
                    <th>Generated</th>
                  </tr>
                </thead>
                <tbody>
                  {allAlerts.map((a, i) => {
                    const key = `${a.match_id}-${a.alert_type}-${a.reason_code}-${i}`;
                    return <AlertRow key={key} alert={a} expanded={expandedKey === key} onToggle={() => setExpandedKey(expandedKey === key ? null : key)} />;
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}

export default MarketMonitorPage;
