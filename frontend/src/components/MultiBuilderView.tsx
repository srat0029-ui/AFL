import { useEffect, useState } from "react";
import {
  fetchMatchMultiBuilder,
  type MatchMultiTiers,
  type MatchReadiness,
  type MultiMode,
  type MultiOption,
  type MultiTier,
  type MultiTierKey,
  type PlacedBetSourceMode,
} from "../api/client";
import { AddBetButton } from "./AddBetButton";
import { AddMultiButton } from "./AddMultiButton";
import "./MultiBuilderView.css";

const READINESS_LABEL: Record<MatchReadiness["state"], string> = {
  READY: "Ready",
  PROVISIONAL: "Provisional",
  NOT_READY: "Not ready",
};

export function ReadinessBadge({ readiness }: { readiness: MatchReadiness }) {
  return (
    <span className={`readiness-badge readiness-badge--${readiness.state.toLowerCase()}`} title={readiness.reasons.join(" ")}>
      {READINESS_LABEL[readiness.state]}
    </span>
  );
}

// Item 3: the full per-signal readiness checklist, plus item 4's single
// "what is missing" sentence when the match isn't fully READY.
export function ReadinessBreakdown({ readiness }: { readiness: MatchReadiness }) {
  const checks: { label: string; ok: boolean }[] = [
    { label: "Team odds fresh", ok: readiness.team_odds_fresh },
    { label: "Player props available", ok: readiness.player_props_exist },
    { label: "Player identities resolved", ok: readiness.player_identities_resolved },
    { label: "Provisional roster available", ok: readiness.provisional_roster_available },
    { label: "Projections generated", ok: readiness.projections_generated },
    { label: "Official teams confirmed", ok: readiness.official_teams_confirmed },
    { label: `Usable multi legs (${readiness.usable_multi_legs})`, ok: readiness.usable_multi_legs > 0 },
  ];
  return (
    <div className="readiness-breakdown">
      {readiness.state !== "READY" && readiness.missing_explanation && (
        <p className="readiness-breakdown__missing">
          <strong>What is missing?</strong> {readiness.missing_explanation}
        </p>
      )}
      <ul className="readiness-breakdown__list">
        {checks.map((c) => (
          <li key={c.label} className={c.ok ? "readiness-breakdown__item readiness-breakdown__item--ok" : "readiness-breakdown__item readiness-breakdown__item--missing"}>
            {c.ok ? "✓" : "✗"} {c.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

const SOURCE_MODE_BY_MULTI_MODE: Record<MultiMode, PlacedBetSourceMode> = {
  high_probability: "high_probability",
  value: "best_value",
};

const DEFAULT_MODE_BY_TIER: Record<MultiTierKey, MultiMode> = {
  conservative: "high_probability",
  balanced: "high_probability",
  higher_return: "high_probability",
  longer_shot: "high_probability",
};

const MODE_LABEL: Record<MultiMode, string> = {
  high_probability: "High Probability",
  value: "Best Value",
};

function MultiLegRow({
  leg,
  matchId,
  bookmaker,
  sourceMode,
}: {
  leg: MultiOption["legs"][number];
  matchId: number;
  bookmaker: string;
  sourceMode: PlacedBetSourceMode;
}) {
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
        <span className="multi-leg__calibration" title="Whether this threshold has historical calibration data behind it">
          {leg.calibration_known ? "Calibration checked" : "No calibration data"}
        </span>
        {leg.opportunity_type === "player" && (
          <span className={leg.is_confirmed ? "multi-leg__lineup multi-leg__lineup--confirmed" : "multi-leg__lineup multi-leg__lineup--unconfirmed"}>
            {leg.is_confirmed ? "Confirmed" : "Unconfirmed"}
          </span>
        )}
        {leg.odds_freshness !== "fresh" && <span className="odds-table__stale">{leg.odds_freshness}</span>}
        {leg.model_risk_flags.length > 0 && (
          <span className="multi-leg__risk-flag" title={leg.model_risk_flags.map((f) => f.description).join(" ")}>
            Recent usage change
          </span>
        )}
      </div>
      {leg.warning_codes.length > 0 && (
        <p className="hint multi-leg__warning" title={leg.warning_codes.map((w) => w.code).join(", ")}>
          ⚠ {leg.warning_codes[0].label}
        </p>
      )}
      <p className="hint multi-leg__reasons" title={leg.reasons.map((r) => r.code).join(", ")}>
        {leg.reasons.map((r) => r.label).join(" · ")}
      </p>
      <AddBetButton
        snapshot={{
          matchId,
          opportunityType: leg.opportunity_type,
          label: leg.label,
          selection: leg.selection ?? (leg.opportunity_type === "player" ? "over" : ""),
          marketType: leg.market_type,
          bookmaker,
          oddsTaken: leg.bookmaker_price,
          modelProbability: leg.model_probability,
          modelFairOdds: leg.model_fair_odds,
          confidenceTier: leg.confidence_tier,
          sourceMode,
          playerId: leg.player_id,
          lineType: leg.line_type,
          threshold: leg.threshold,
          lineValue: leg.line_value,
          lineupStatus: leg.selection_status,
          modelVersion: leg.model_version,
        }}
      />
    </div>
  );
}

function MultiOptionCard({ tierKey, tierLabel, option, matchId }: { tierKey: string; tierLabel: string; option: MultiOption; matchId: number }) {
  // Floor to the nearest 5% so the badge never overstates what the data
  // actually supports (e.g. 76.4% lowest -> "All legs >= 75%", not 76%).
  const badgeFloor = Math.floor((option.lowest_leg_probability * 100) / 5) * 5;

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
        <div className="multi-card__badges">
          {option.mode === "high_probability" && badgeFloor >= 50 && (
            <span className="multi-card__probability-badge">All legs ≥ {badgeFloor}%</span>
          )}
          {option.reason_codes.includes("LOW_CORRELATION") && <span className="multi-card__low-correlation-badge">Low correlation</span>}
          {option.provisional && <span className="multi-card__provisional-badge">Provisional</span>}
        </div>
      </div>

      <div className="multi-card__legs">
        {option.legs.map((leg, i) => (
          <MultiLegRow key={i} leg={leg} matchId={matchId} bookmaker={option.bookmaker} sourceMode={SOURCE_MODE_BY_MULTI_MODE[option.mode]} />
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

      <AddMultiButton matchId={matchId} tier={tierKey} option={option} sourceMode={SOURCE_MODE_BY_MULTI_MODE[option.mode]} />
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
        {primary && <ReadinessBadge readiness={primary.readiness} />}
        <label className="multi-builder__toggle">
          <input type="checkbox" checked={confirmedOnly} onChange={(e) => setConfirmedOnly(e.target.checked)} />
          Confirmed players only
        </label>
      </div>
      {primary && <ReadinessBreakdown readiness={primary.readiness} />}
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

          {primary.n_eligible_legs > 0 && (
            <div className="multi-builder__tiers">
              {primary.tiers.map((tierMeta) => {
                const tierKey = tierMeta.tier;
                const effectiveMode = tierModeOverride[tierKey] ?? DEFAULT_MODE_BY_TIER[tierKey];
                const tier = dataByMode[effectiveMode].tiers.find((t) => t.tier === tierKey);
                if (!tier) return null;
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
                    {tier.options.length === 0 ? (
                      <p className="empty-state">{tier.unavailable_reason}</p>
                    ) : (
                      <>
                        {tier.bookmaker_comparison.length > 1 && (
                          <p className="hint multi-builder__bookmaker-comparison">
                            Best available component pricing:{" "}
                            <strong>{tier.bookmaker_comparison[tier.bookmaker_comparison.length - 1].bookmaker}</strong> (
                            {tier.bookmaker_comparison
                              .slice()
                              .reverse()
                              .map((c) => `${c.bookmaker} $${c.indicative_combined_odds.toFixed(2)}`)
                              .join(" · ")}
                            ) — actual bookmaker Same Game Multi pricing may differ from multiplying standalone legs.
                          </p>
                        )}
                        <div className="multi-builder__tier-options">
                          {tier.options.map((opt) => (
                            <MultiOptionCard key={opt.option_label} tierKey={tierKey} tierLabel={tier.label} option={opt} matchId={matchId} />
                          ))}
                        </div>
                      </>
                    )}
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
