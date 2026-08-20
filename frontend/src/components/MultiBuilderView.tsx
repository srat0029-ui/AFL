import { useEffect, useState } from "react";
import { fetchMatchMultiBuilder, type MatchMultiTiers, type MultiMode, type MultiOption, type MultiTierKey } from "../api/client";
import "./MultiBuilderView.css";

const DEFAULT_MODE_BY_TIER: Record<MultiTierKey, MultiMode> = {
  conservative: "high_probability",
  balanced: "high_probability",
  higher_return: "value",
  longer_shot: "value",
};

const MODE_LABEL: Record<MultiMode, string> = {
  high_probability: "High Probability",
  value: "Best Value",
};

function MultiLegRow({ leg }: { leg: MultiOption["legs"][number] }) {
  return (
    <div className="multi-leg">
      <div className="multi-leg__main">
        <span className="multi-leg__label">{leg.label}</span>
        <span className="multi-leg__price">${leg.bookmaker_price.toFixed(2)}</span>
      </div>
      <div className="multi-leg__meta">
        <span>Model {(leg.model_probability * 100).toFixed(1)}%</span>
        <span>Fair ${leg.model_fair_odds.toFixed(2)}</span>
        <span className={leg.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
          {leg.difference_pp >= 0 ? "+" : ""}
          {(leg.difference_pp * 100).toFixed(1)}pp
        </span>
        <span className={`confidence-badge confidence-badge--${leg.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
          {leg.confidence_tier.replace("_confidence", "")}
        </span>
        {leg.opportunity_type === "player" && (
          <span className={leg.is_confirmed ? "multi-leg__lineup multi-leg__lineup--confirmed" : "multi-leg__lineup multi-leg__lineup--unconfirmed"}>
            {leg.is_confirmed ? "Confirmed" : "Unconfirmed"}
          </span>
        )}
        {leg.odds_freshness !== "fresh" && <span className="odds-table__stale">{leg.odds_freshness}</span>}
      </div>
      {leg.warnings.length > 0 && <p className="hint multi-leg__warning">⚠ {leg.warnings[0]}</p>}
      <p className="hint multi-leg__reasons">{leg.reasons.join(" · ")}</p>
    </div>
  );
}

function MultiOptionCard({ tierLabel, option }: { tierLabel: string; option: MultiOption }) {
  return (
    <div className="multi-card">
      <div className="multi-card__header">
        <div>
          <strong>
            {tierLabel} Multi — {option.option_label}
          </strong>
          <span className="hint multi-card__bookmaker"> · {option.bookmaker}</span>
          <span className="multi-card__mode-badge"> · {MODE_LABEL[option.mode]}</span>
        </div>
        {option.provisional && <span className="multi-card__provisional-badge">Provisional</span>}
      </div>

      <div className="multi-card__legs">
        {option.legs.map((leg, i) => (
          <MultiLegRow key={i} leg={leg} />
        ))}
      </div>

      <div className="multi-card__combined">
        <span>
          {option.indicative_odds_label}: <strong>${option.indicative_combined_odds.toFixed(2)}</strong>
        </span>
        <span className="hint">{option.indicative_odds_explanation}</span>
      </div>

      <div className="multi-card__footer hint">
        {option.n_legs} legs · lowest leg probability {(option.lowest_leg_probability * 100).toFixed(0)}% · average leg
        probability {(option.average_leg_probability * 100).toFixed(0)}% ·{" "}
        {option.lineup_ready ? "Lineup confirmed" : "Lineup not fully confirmed"}
      </div>

      {option.correlation_warnings.length > 0 && (
        <div className="multi-card__correlation-warning">
          {option.correlation_warnings.map((w, i) => (
            <p key={i}>⚠ {w}</p>
          ))}
        </div>
      )}
    </div>
  );
}

interface MultiBuilderViewProps {
  matchId: number;
}

/** Product feature stage: per-match Multi Builder. Every leg/probability
 * is copied unchanged from the same opportunity computation Best
 * Opportunities uses — this view only groups legs into same-bookmaker
 * combinations at different target price bands. Never labelled guaranteed/
 * safe/lock/certain; combined odds are always disclosed as indicative.
 *
 * Each tier can independently run in "High Probability" mode (ranks legs by
 * individual landing probability, confidence, calibration, lineup status and
 * freshness first, model-market edge last) or "Best Value" mode (ranks by
 * model-market edge, as before). Conservative/Balanced default to High
 * Probability; Higher Return/Longer Shot default to Best Value — either can
 * be switched per tier. Both modes' results are fetched so switching is
 * instant and never shows a combined/joint probability, only each leg's own. */
function MultiBuilderView({ matchId }: MultiBuilderViewProps) {
  const [confirmedOnly, setConfirmedOnly] = useState(true);
  const [dataByMode, setDataByMode] = useState<Record<MultiMode, MatchMultiTiers> | null>(null);
  const [tierModeOverride, setTierModeOverride] = useState<Partial<Record<MultiTierKey, MultiMode>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setTierModeOverride({});
    Promise.all([
      fetchMatchMultiBuilder(matchId, { confirmedOnly, mode: "high_probability" }),
      fetchMatchMultiBuilder(matchId, { confirmedOnly, mode: "value" }),
    ])
      .then(([highProbability, value]) => setDataByMode({ high_probability: highProbability, value }))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load multi builder"))
      .finally(() => setLoading(false));
  }, [matchId, confirmedOnly]);

  const primary = dataByMode?.high_probability ?? dataByMode?.value ?? null;
  const tierOrder = primary?.tiers.map((t) => t.tier) ?? [];

  const anyOptions = dataByMode
    ? tierOrder.some((tierKey) => {
        const mode = tierModeOverride[tierKey] ?? DEFAULT_MODE_BY_TIER[tierKey];
        const tier = dataByMode[mode].tiers.find((t) => t.tier === tierKey);
        return (tier?.options.length ?? 0) > 0;
      })
    : false;

  return (
    <section className="multi-builder">
      <div className="multi-builder__header">
        <h2>Multi Builder</h2>
        <label className="multi-builder__toggle">
          <input type="checkbox" checked={confirmedOnly} onChange={(e) => setConfirmedOnly(e.target.checked)} />
          Confirmed players only
        </label>
      </div>
      <p className="hint">
        Model-informed multi-leg combinations built from existing projections and live bookmaker markets — not a
        prediction of the result, and never guaranteed, safe, or a lock. Every leg passes the same hard integrity
        checks used everywhere else in this app. Each tier shows either its highest-probability legs or its best
        model-vs-market value legs — switch the mode per tier below.
      </p>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && primary && dataByMode && (
        <>
          {primary.n_eligible_legs === 0 && (
            <p className="empty-state">No usable markets currently support a multi for this match.</p>
          )}

          {primary.n_eligible_legs > 0 && !anyOptions && confirmedOnly && (
            <p className="empty-state">
              Confirmed-player multis will become available once teams are announced.{" "}
              <button type="button" className="btn" onClick={() => setConfirmedOnly(false)}>
                Show provisional multis
              </button>
            </p>
          )}

          {primary.n_eligible_legs > 0 && !anyOptions && !confirmedOnly && (
            <p className="empty-state">No sensible multi could be built from the currently available markets.</p>
          )}

          {anyOptions && (
            <div className="multi-builder__tiers">
              {primary.tiers.map((tierMeta) => {
                const tierKey = tierMeta.tier;
                const effectiveMode = tierModeOverride[tierKey] ?? DEFAULT_MODE_BY_TIER[tierKey];
                const tier = dataByMode[effectiveMode].tiers.find((t) => t.tier === tierKey);
                if (!tier || tier.options.length === 0) return null;
                return (
                  <div key={tierKey} className="multi-builder__tier-group">
                    <div className="multi-builder__tier-mode-switch">
                      <span className="multi-builder__tier-mode-label">{tier.label}</span>
                      {(["high_probability", "value"] as MultiMode[]).map((m) => (
                        <button
                          key={m}
                          type="button"
                          className={
                            effectiveMode === m
                              ? "multi-builder__mode-btn multi-builder__mode-btn--active"
                              : "multi-builder__mode-btn"
                          }
                          onClick={() => setTierModeOverride((prev) => ({ ...prev, [tierKey]: m }))}
                        >
                          {MODE_LABEL[m]}
                        </button>
                      ))}
                    </div>
                    <div className="multi-builder__tier-options">
                      {tier.options.map((opt) => (
                        <MultiOptionCard key={opt.option_label} tierLabel={tier.label} option={opt} />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export default MultiBuilderView;
