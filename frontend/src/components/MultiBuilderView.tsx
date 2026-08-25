import { useEffect, useState } from "react";
import {
  fetchMatchMultiBuilder,
  type MatchMultiTiers,
  type MatchReadiness,
  type MultiMode,
  type MultiOption,
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
    <span className={`chip chip--readiness-${readiness.state.toLowerCase()}`}>{READINESS_LABEL[readiness.state]}</span>
  );
}

// One concise line, matching the brief's own example ("Teams not confirmed
// · player markets fresh") - the full 7-signal breakdown below stays
// available on demand rather than always taking up space.
function readinessSummary(r: MatchReadiness): string {
  const parts = [
    r.team_odds_fresh ? "team odds fresh" : "team odds stale",
    r.player_props_fresh ? "player markets fresh" : r.player_props_exist ? "player markets stale" : "no player markets yet",
    r.official_teams_confirmed ? "teams confirmed" : "teams not confirmed",
  ];
  return parts.join(" · ");
}

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
    <details className="readiness-detail">
      <summary>
        {READINESS_LABEL[readiness.state]} — {readinessSummary(readiness)}
      </summary>
      <div className="readiness-detail__body">
        {readiness.state !== "READY" && readiness.missing_explanation && (
          <p className="readiness-detail__missing">
            <strong>What's missing:</strong> {readiness.missing_explanation}
          </p>
        )}
        <ul className="readiness-detail__list">
          {checks.map((c) => (
            <li key={c.label} className={c.ok ? "readiness-detail__item readiness-detail__item--ok" : "readiness-detail__item readiness-detail__item--missing"}>
              {c.ok ? "✓" : "✗"} {c.label}
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

const SOURCE_MODE_BY_MULTI_MODE: Record<MultiMode, PlacedBetSourceMode> = {
  high_probability: "high_probability",
  value: "best_value",
};

const MODE_LABEL: Record<MultiMode, string> = {
  high_probability: "High Probability",
  value: "Best Value",
};

const CONFIDENCE_LABEL: Record<string, string> = {
  higher_confidence: "Higher confidence",
  moderate_confidence: "Moderate confidence",
  lower_confidence: "Lower confidence",
  insufficient_history: "Insufficient data",
};

// Item 8: probability visually dominates in High Probability mode; edge
// dominates in Best Value mode. Same data, different emphasis — never a
// different number.
function LegHeadline({ leg, mode }: { leg: MultiOption["legs"][number]; mode: MultiMode }) {
  const probText = `${(leg.model_probability * 100).toFixed(1)}%`;
  const edgeText = `${leg.difference_pp >= 0 ? "+" : ""}${(leg.difference_pp * 100).toFixed(1)}%`;
  if (mode === "high_probability") {
    return (
      <span className="multi-leg__headline">
        <strong className="multi-leg__primary-figure">{probText}</strong>
        <span className="multi-leg__secondary-figure">{edgeText} edge</span>
      </span>
    );
  }
  return (
    <span className="multi-leg__headline">
      <strong className={leg.difference_pp >= 0 ? "multi-leg__primary-figure multi-leg__primary-figure--pos" : "multi-leg__primary-figure multi-leg__primary-figure--neg"}>
        {edgeText} edge
      </strong>
      <span className="multi-leg__secondary-figure">{probText} model</span>
    </span>
  );
}

function MultiLegRow({
  leg,
  matchId,
  bookmaker,
  mode,
  sourceMode,
}: {
  leg: MultiOption["legs"][number];
  matchId: number;
  bookmaker: string;
  mode: MultiMode;
  sourceMode: PlacedBetSourceMode;
}) {
  const [detailOpen, setDetailOpen] = useState(false);
  const warningCount = leg.warning_codes.length;
  // Player legs are formatted "Name Threshold+ Market" upstream; split into
  // a name line + selection line only when we can find the threshold digit,
  // otherwise show the label as-is (team legs, e.g. "Melbourne +2.5").
  const thresholdMatch = leg.label.match(/^(.*?)\s(\d[\d.]*\+.*)$/);

  return (
    <div className="multi-leg">
      <div className="multi-leg__row">
        <div className="multi-leg__id">
          {thresholdMatch ? (
            <>
              <span className="multi-leg__player">{thresholdMatch[1]}</span>
              <span className="multi-leg__selection">{thresholdMatch[2]}</span>
            </>
          ) : (
            <span className="multi-leg__player">{leg.label}</span>
          )}
        </div>
        <LegHeadline leg={leg} mode={mode} />
      </div>

      <div className="multi-leg__price-line">
        <span>{bookmaker}</span>
        <span className="num">${leg.bookmaker_price.toFixed(2)}</span>
        <span className="multi-leg__price-sep">Fair</span>
        <span className="num">${leg.model_fair_odds.toFixed(2)}</span>
      </div>

      <div className="multi-leg__meta-line">
        <span>{CONFIDENCE_LABEL[leg.confidence_tier] ?? leg.confidence_tier}</span>
        {leg.opportunity_type === "player" && <span>{leg.is_confirmed ? "Confirmed" : "Provisional"}</span>}
        {leg.odds_freshness !== "fresh" && <span className="multi-leg__meta-warn">{leg.odds_freshness}</span>}
        {warningCount > 0 && (
          <button type="button" className="multi-leg__warning-toggle" onClick={() => setDetailOpen((v) => !v)}>
            ⚠ {warningCount === 1 ? "1 warning" : `${warningCount} warnings`}
          </button>
        )}
        <button type="button" className="multi-leg__detail-toggle" onClick={() => setDetailOpen((v) => !v)}>
          {detailOpen ? "Hide detail" : "Detail"}
        </button>
      </div>

      {detailOpen && (
        <div className="multi-leg__detail">
          {leg.warning_codes.length > 0 && (
            <p className="multi-leg__detail-warnings">{leg.warning_codes.map((w) => w.label).join(" · ")}</p>
          )}
          <p className="multi-leg__detail-reasons">{leg.reasons.map((r) => r.label).join(" · ")}</p>
          <p className="multi-leg__detail-meta">
            {leg.calibration_known ? "Calibration checked" : "No calibration data"}
            {leg.model_version ? ` · Model ${leg.model_version.split("@")[0]}` : ""}
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
      )}
    </div>
  );
}

function MultiOptionRow({
  tierKey,
  tierLabel,
  option,
  matchId,
  mode,
  defaultOpen,
}: {
  tierKey: string;
  tierLabel: string;
  option: MultiOption;
  matchId: number;
  mode: MultiMode;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const badgeFloor = Math.floor((option.lowest_leg_probability * 100) / 5) * 5;

  return (
    <div className="tier-option">
      <button type="button" className="tier-option__head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <div className="tier-option__headline">
          <span className="tier-option__label">
            {tierLabel}
            {option.option_label !== "Option A" && <span className="tier-option__option-label"> · {option.option_label}</span>}
          </span>
          <span className="tier-option__price">
            {option.bookmaker} <strong className="num">${option.indicative_combined_odds.toFixed(2)}</strong>
          </span>
        </div>
        <div className="tier-option__meta">
          {option.n_legs} legs · weakest {(option.lowest_leg_probability * 100).toFixed(1)}% · avg{" "}
          {(option.average_leg_probability * 100).toFixed(1)}%
          {option.provisional && <span className="chip chip--warning tier-option__chip">Provisional</span>}
          {mode === "high_probability" && badgeFloor >= 50 && (
            <span className="chip chip--success tier-option__chip">all ≥{badgeFloor}%</span>
          )}
          {option.correlation_warnings.length > 0 && (
            <span className="chip chip--neutral tier-option__chip">{option.correlation_warnings.length} correlation note</span>
          )}
        </div>
        <span className="tier-option__caret">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="tier-option__body">
          {option.legs.map((leg, i) => (
            <MultiLegRow key={i} leg={leg} matchId={matchId} bookmaker={option.bookmaker} mode={mode} sourceMode={SOURCE_MODE_BY_MULTI_MODE[option.mode]} />
          ))}
          {option.correlation_warnings.length > 0 && (
            <div className="tier-option__correlation">
              {option.correlation_warnings.map((w, i) => (
                <p key={i}>{w}</p>
              ))}
            </div>
          )}
          <div className="tier-option__footer">
            <span className="hint">{option.indicative_odds_explanation}</span>
            <AddMultiButton matchId={matchId} tier={tierKey} option={option} sourceMode={SOURCE_MODE_BY_MULTI_MODE[option.mode]} />
          </div>
        </div>
      )}
    </div>
  );
}

interface MultiBuilderViewProps {
  matchId: number;
}

/** Per-match Multi Builder. Every leg/probability is copied unchanged from
 * the same opportunity computation Best Opportunities uses. One global
 * High Probability / Best Value toggle (High Probability default) applies
 * to every tier; each tier row shows its headline inline and expands to
 * full leg detail on click. Never shows a combined/joint probability. */
function MultiBuilderView({ matchId }: MultiBuilderViewProps) {
  const [confirmedOnly, setConfirmedOnly] = useState(true);
  const [mode, setMode] = useState<MultiMode>("high_probability");
  const [dataByMode, setDataByMode] = useState<Record<MultiMode, MatchMultiTiers> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchMatchMultiBuilder(matchId, { confirmedOnly, mode: "high_probability" }),
      fetchMatchMultiBuilder(matchId, { confirmedOnly, mode: "value" }),
    ])
      .then(([highProbability, value]) => setDataByMode({ high_probability: highProbability, value }))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load multi builder"))
      .finally(() => setLoading(false));
  }, [matchId, confirmedOnly]);

  const active = dataByMode?.[mode] ?? null;
  const anyOptions = active ? active.tiers.some((t) => t.options.length > 0) : false;

  return (
    <section className="multi-builder">
      <div className="section-row">
        <h2 className="section-title">Multi Builder</h2>
        {active && <ReadinessBadge readiness={active.readiness} />}
      </div>
      {active && <ReadinessBreakdown readiness={active.readiness} />}

      <div className="multi-builder__controls">
        <div className="mode-switch" role="tablist" aria-label="Multi Builder mode">
          {(["high_probability", "value"] as MultiMode[]).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              className={mode === m ? "mode-switch__btn mode-switch__btn--active" : "mode-switch__btn"}
              onClick={() => setMode(m)}
            >
              {MODE_LABEL[m]}
            </button>
          ))}
        </div>
        <label className="multi-builder__toggle">
          <input type="checkbox" checked={confirmedOnly} onChange={(e) => setConfirmedOnly(e.target.checked)} />
          Confirmed players only
        </label>
      </div>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && active && dataByMode && (
        <>
          {active.n_eligible_legs === 0 && (
            <p className="empty-state">No usable markets currently support a multi for this match.</p>
          )}

          {active.n_eligible_legs > 0 && !anyOptions && confirmedOnly && (
            <p className="empty-state">
              Confirmed-player multis will become available once teams are announced.{" "}
              <button type="button" className="btn" onClick={() => setConfirmedOnly(false)}>
                Show provisional multis
              </button>
            </p>
          )}

          {active.n_eligible_legs > 0 && (
            <div className="multi-builder__tiers">
              {active.tiers.map((tier) => (
                <div key={tier.tier} className="tier-block">
                  {tier.options.length === 0 ? (
                    <div className="tier-block__empty">
                      <span className="tier-block__empty-label">{tier.label}</span>
                      <span className="tier-block__empty-reason">{tier.unavailable_reason}</span>
                    </div>
                  ) : (
                    <>
                      {tier.bookmaker_comparison.length > 1 && (
                        <p className="hint multi-builder__bookmaker-comparison">
                          Best component pricing: <strong>{tier.bookmaker_comparison[tier.bookmaker_comparison.length - 1].bookmaker}</strong> (
                          {tier.bookmaker_comparison
                            .slice()
                            .reverse()
                            .map((c) => `${c.bookmaker} $${c.indicative_combined_odds.toFixed(2)}`)
                            .join(" · ")}
                          )
                        </p>
                      )}
                      {tier.options.map((opt, i) => (
                        <MultiOptionRow key={opt.option_label} tierKey={tier.tier} tierLabel={tier.label} option={opt} matchId={matchId} mode={mode} defaultOpen={i === 0} />
                      ))}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export default MultiBuilderView;
