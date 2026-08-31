import { useEffect, useState } from "react";
import {
  fetchModelRegistry,
  fetchProspectiveEvaluation,
  fetchSgmProspectiveEvaluation,
  type ModelRegistry,
  type ModelRunStatus,
  type ProspectiveEvaluation,
  type ProspectiveSplit,
  type SgmProspectiveEvaluation,
  type SgmProspectiveSplit,
} from "../api/client";
import "./ModelRegistryPage.css";

const STATUS_LABELS: Record<ModelRunStatus, string> = {
  champion: "Champion",
  previous_champion: "Previous Champion",
  challenger: "Challenger",
  rejected: "Rejected",
};

const STATUS_CHIP_VARIANT: Record<ModelRunStatus, string> = {
  champion: "success",
  previous_champion: "warning",
  challenger: "accent",
  rejected: "danger",
};

function fmt(n: number | null | undefined, digits = 3): string {
  return n === null || n === undefined ? "—" : n.toFixed(digits);
}

function StatusBadge({ status }: { status: ModelRunStatus }) {
  return <span className={`chip chip--${STATUS_CHIP_VARIANT[status]}`}>{STATUS_LABELS[status]}</span>;
}

function ModelRunTable({ rows }: { rows: ModelRegistry["disposal_models"] }) {
  return (
    <div className="model-registry-table__wrap">
      <table className="model-registry-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Status</th>
            <th>Run date</th>
            <th>Tune / Eval</th>
            <th>N</th>
            <th>MAE</th>
            <th>Bias</th>
            <th>25+ Brier / ECE</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.model_name}>
              <td>
                <div className="model-registry-table__name">{m.model_name}</div>
                <div className="hint">{m.model_version}</div>
              </td>
              <td>
                <StatusBadge status={m.status} />
              </td>
              <td>{new Date(m.run_at).toLocaleDateString()}</td>
              <td>
                {m.tune_start_year}-{m.tune_end_year} / {m.evaluation_start_year}-{m.evaluation_end_year}
              </td>
              <td>{m.sample_size?.toLocaleString() ?? "—"}</td>
              <td>{fmt(m.point_metrics.mae)}</td>
              <td>{fmt(m.point_metrics.bias)}</td>
              <td>
                {m.calibration_metrics["25+"] ? `${fmt(m.calibration_metrics["25+"].brier)} / ${fmt(m.calibration_metrics["25+"].ece)}` : "—"}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={8} className="hint">
                No model runs recorded for this market yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function BiasRow({ label, ridge, huber }: { label: string; ridge?: number; huber?: number }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{ridge !== undefined ? fmt(ridge, 3) : "—"}</td>
      <td>{huber !== undefined ? fmt(huber, 3) : "—"}</td>
    </tr>
  );
}

function DisposalHeadToHeadCard({ h2h }: { h2h: ModelRegistry["disposal_head_to_head"] }) {
  if (!h2h.ridge && !h2h.huber) return null;
  return (
    <div className="model-registry-h2h">
      <h3>Disposal model: Ridge vs Huber</h3>
      <table className="model-registry-table">
        <thead>
          <tr>
            <th></th>
            <th>Ridge {h2h.ridge && <StatusBadge status={h2h.ridge.status} />}</th>
            <th>Huber {h2h.huber && <StatusBadge status={h2h.huber.status} />}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Overall MAE</td>
            <td>{fmt(h2h.ridge?.point_metrics.mae)}</td>
            <td>{fmt(h2h.huber?.point_metrics.mae)}</td>
          </tr>
          <tr>
            <td>Overall bias</td>
            <td>{fmt(h2h.ridge?.point_metrics.bias)}</td>
            <td>{fmt(h2h.huber?.point_metrics.bias)}</td>
          </tr>
          <tr>
            <td>25+ Brier / ECE</td>
            <td>{h2h.ridge?.calibration_metrics["25+"] ? `${fmt(h2h.ridge.calibration_metrics["25+"].brier)} / ${fmt(h2h.ridge.calibration_metrics["25+"].ece, 4)}` : "—"}</td>
            <td>{h2h.huber?.calibration_metrics["25+"] ? `${fmt(h2h.huber.calibration_metrics["25+"].brier)} / ${fmt(h2h.huber.calibration_metrics["25+"].ece, 4)}` : "—"}</td>
          </tr>
          <tr>
            <td>30+ Brier / ECE</td>
            <td>{h2h.ridge?.calibration_metrics["30+"] ? `${fmt(h2h.ridge.calibration_metrics["30+"].brier)} / ${fmt(h2h.ridge.calibration_metrics["30+"].ece, 4)}` : "—"}</td>
            <td>{h2h.huber?.calibration_metrics["30+"] ? `${fmt(h2h.huber.calibration_metrics["30+"].brier)} / ${fmt(h2h.huber.calibration_metrics["30+"].ece, 4)}` : "—"}</td>
          </tr>
          <tr className="model-registry-table__section">
            <td colSpan={3}>High-volume-player bias (predicted − actual disposals)</td>
          </tr>
          {Object.keys({ ...h2h.ridge_high_volume_bias, ...h2h.huber_high_volume_bias }).map((k) => (
            <BiasRow key={k} label={k} ridge={h2h.ridge_high_volume_bias[k]} huber={h2h.huber_high_volume_bias[k]} />
          ))}
          <tr className="model-registry-table__section">
            <td colSpan={3}>Low-history-player bias (games of history)</td>
          </tr>
          {Object.keys({ ...h2h.ridge_low_history_bias, ...h2h.huber_low_history_bias }).map((k) => (
            <BiasRow key={k} label={k} ridge={h2h.ridge_low_history_bias[k]} huber={h2h.huber_low_history_bias[k]} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PromotionAuditTrail({ events }: { events: ModelRegistry["promotion_events"] }) {
  return (
    <div className="model-registry-audit">
      <h3>Promotion audit trail</h3>
      {events.length === 0 && <p className="hint">No promotions recorded yet.</p>}
      {events.map((e, i) => (
        <div key={i} className="model-registry-audit__event">
          <div className="model-registry-audit__headline">
            <strong>{e.market}</strong>: {e.previous_champion_model_name ?? "(none)"} → <strong>{e.new_champion_model_name}</strong>
            <span className="hint"> · {new Date(e.promoted_at).toLocaleString()}</span>
          </div>
          <p className="hint">{e.evidence_summary}</p>
        </div>
      ))}
    </div>
  );
}

function ProspectiveSplitTable({ title, splits }: { title: string; splits: ProspectiveSplit[] }) {
  if (splits.length === 0) return null;
  return (
    <div className="model-registry-prospective__split">
      <h4>{title}</h4>
      <table className="model-registry-table">
        <thead>
          <tr>
            <th>Split</th>
            <th>N settled</th>
            <th>Unique events</th>
            <th>Model Brier</th>
            <th>Market Brier</th>
            <th>Model log loss</th>
            <th>Market log loss</th>
            <th>ECE</th>
          </tr>
        </thead>
        <tbody>
          {splits.map((s) => (
            <tr key={s.label}>
              <td>
                {s.label}
                {s.exploratory && <span className="model-registry-exploratory-tag">Exploratory</span>}
              </td>
              <td>{s.n_settled}</td>
              <td>{s.n_unique_events}</td>
              <td>{fmt(s.model_brier)}</td>
              <td>{fmt(s.market_brier)}</td>
              <td>{fmt(s.model_log_loss)}</td>
              <td>{fmt(s.market_log_loss)}</td>
              <td>{fmt(s.model_calibration_ece, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProspectiveEvaluationPanel({ evaluation }: { evaluation: ProspectiveEvaluation }) {
  return (
    <section className="model-registry-prospective">
      <h2>Prospective Live Evaluation</h2>
      <p className="hint">
        Predictions frozen before kickoff, settled against real outcomes, never overwritten. Strictly separate from the
        historical backtest above — never mixed into the same number.
      </p>
      {!evaluation.has_settled_data ? (
        <div className="model-registry-accumulating">
          <p>{evaluation.message}</p>
        </div>
      ) : (
        <>
          <div className="stat-strip model-registry-prospective__headline">
            <div className="stat-strip__item">
              <span className="stat-strip__label">Settled predictions</span>
              <span className="stat-strip__value">{evaluation.n_settled.toLocaleString()}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Unique player-match events</span>
              <span className="stat-strip__value">{evaluation.n_unique_player_match_events.toLocaleString()}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Model Brier</span>
              <span className="stat-strip__value">{fmt(evaluation.overall?.model_brier)}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Market Brier</span>
              <span className="stat-strip__value">{fmt(evaluation.overall?.market_brier)}</span>
            </div>
          </div>
          {evaluation.overall?.exploratory && (
            <p className="model-registry-exploratory-tag model-registry-exploratory-tag--headline">
              Exploratory — not enough prospective evidence yet to claim the model beats the market.
            </p>
          )}
          <ProspectiveSplitTable title="By market family" splits={evaluation.by_market_family} />
          <ProspectiveSplitTable title="By model probability bucket" splits={evaluation.by_probability_bucket} />
          <ProspectiveSplitTable title="By model version" splits={evaluation.by_model_version} />
        </>
      )}
    </section>
  );
}

function SgmProspectiveSplitTable({ title, splits }: { title: string; splits: SgmProspectiveSplit[] }) {
  if (splits.length === 0) return null;
  return (
    <div className="model-registry-prospective__split">
      <h4>{title}</h4>
      <table className="model-registry-table">
        <thead>
          <tr>
            <th>Split</th>
            <th>N settled</th>
            <th>Unique combos</th>
            <th>Model Brier</th>
            <th>Naive Brier</th>
            <th>Model log loss</th>
            <th>Naive log loss</th>
            <th>ECE</th>
            <th>Bookmaker Brier</th>
          </tr>
        </thead>
        <tbody>
          {splits.map((s) => (
            <tr key={s.label}>
              <td>
                {s.label}
                {s.exploratory && <span className="model-registry-exploratory-tag">Exploratory</span>}
              </td>
              <td>{s.n_settled}</td>
              <td>{s.n_unique_combos}</td>
              <td>{fmt(s.model_brier)}</td>
              <td>{fmt(s.naive_brier)}</td>
              <td>{fmt(s.model_log_loss)}</td>
              <td>{fmt(s.naive_log_loss)}</td>
              <td>{fmt(s.model_calibration_ece, 4)}</td>
              <td title={s.n_with_bookmaker_price === 0 ? "No odds provider integration exposes a genuine bookmaker Same Game Multi price yet" : undefined}>
                {s.n_with_bookmaker_price === 0 ? "No data" : fmt(s.bookmaker_brier)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SgmProspectiveEvaluationPanel({ evaluation }: { evaluation: SgmProspectiveEvaluation }) {
  return (
    <section className="model-registry-prospective">
      <h2>Same Game Multi Prospective Evaluation</h2>
      <p className="hint">
        Same Game Multi joint prices frozen before kickoff (see Multi Builder's "Same Game Pricing" panel), settled
        against real outcomes, never overwritten. Scored against naive independence always, and against a genuine
        bookmaker SGM price only when one exists — no odds provider integration exposes that today, so those columns
        read "No data" rather than a fabricated number. "Overall" and the leg/correlation splits below use each real
        combo's single closing (last-before-kickoff) snapshot, so the same combo frozen at multiple horizons is never
        counted more than once — the horizon split below is the one place that's deliberate.
      </p>
      {!evaluation.has_settled_data ? (
        <div className="model-registry-accumulating">
          <p>{evaluation.message}</p>
        </div>
      ) : (
        <>
          <div className="stat-strip model-registry-prospective__headline">
            <div className="stat-strip__item">
              <span className="stat-strip__label">Settled combos</span>
              <span className="stat-strip__value">{evaluation.n_settled.toLocaleString()}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Unique combos (closing snapshot)</span>
              <span className="stat-strip__value">{evaluation.n_unique_combos.toLocaleString()}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Model Brier</span>
              <span className="stat-strip__value">{fmt(evaluation.overall?.model_brier)}</span>
            </div>
            <div className="stat-strip__item">
              <span className="stat-strip__label">Naive independence Brier</span>
              <span className="stat-strip__value">{fmt(evaluation.overall?.naive_brier)}</span>
            </div>
          </div>
          {evaluation.overall?.exploratory && (
            <p className="model-registry-exploratory-tag model-registry-exploratory-tag--headline">
              Exploratory — not enough settled combos yet to claim the joint model beats naive independence.
            </p>
          )}
          <SgmProspectiveSplitTable title="By number of legs" splits={evaluation.by_n_legs} />
          <SgmProspectiveSplitTable title="By leg/market combination" splits={evaluation.by_leg_combination} />
          <SgmProspectiveSplitTable title="By correlation-adjustment magnitude" splits={evaluation.by_correlation_adjustment_magnitude} />
          <SgmProspectiveSplitTable title="By snapshot horizon (not deduped — tracks the model's own belief across the pre-match window)" splits={evaluation.by_snapshot_horizon} />
        </>
      )}
    </section>
  );
}

function ModelRegistryPage() {
  const [registry, setRegistry] = useState<ModelRegistry | null>(null);
  const [prospective, setProspective] = useState<ProspectiveEvaluation | null>(null);
  const [sgmProspective, setSgmProspective] = useState<SgmProspectiveEvaluation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchModelRegistry().then(setRegistry).catch((err) => setError(err instanceof Error ? err.message : "Failed to load model registry"));
    fetchProspectiveEvaluation().then(setProspective).catch(() => undefined);
    fetchSgmProspectiveEvaluation().then(setSgmProspective).catch(() => undefined);
  }, []);

  return (
    <main className="model-registry-page">
      <h1>Model Registry</h1>
      <p className="subtitle">
        Read-only view of every production/challenger model, the promotion audit trail, and live prospective evaluation.
        No model, ranking, or multi-builder logic is changed by anything on this page.
      </p>

      {error && <p className="model-registry-page__error">{error}</p>}
      {!registry && !error && <p>Loading…</p>}

      {registry && (
        <>
          <section className="model-registry-section">
            <h2>{registry.dataset_label}</h2>

            <DisposalHeadToHeadCard h2h={registry.disposal_head_to_head} />

            <h3>Disposal models</h3>
            <ModelRunTable rows={registry.disposal_models} />

            <h3>Goal models</h3>
            <ModelRunTable rows={registry.goal_models} />

            <h3>Team models</h3>
            <ModelRunTable rows={registry.team_models} />

            <PromotionAuditTrail events={registry.promotion_events} />
          </section>

          {prospective && <ProspectiveEvaluationPanel evaluation={prospective} />}
          {sgmProspective && <SgmProspectiveEvaluationPanel evaluation={sgmProspective} />}
        </>
      )}
    </main>
  );
}

export default ModelRegistryPage;
