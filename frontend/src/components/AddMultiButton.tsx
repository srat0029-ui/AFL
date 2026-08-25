import { useState } from "react";
import { createPlacedBet, type MultiOption, type PlacedBetSourceMode } from "../api/client";
import "./AddBetButton.css";

// Item 16: adds an ENTIRE multi as one record — under the hood this posts
// one PlacedBet per leg (settlement already works correctly per-leg, see
// backend app/player_modelling/placed_bets.py's settle_placed_bets), all
// sharing a client-generated multi_group_id plus the tier/indicative-odds
// context, frozen at this exact moment exactly like every other field on a
// PlacedBet — a later model change never rewrites what was recorded here.
export function AddMultiButton({
  matchId,
  tier,
  option,
  sourceMode,
}: {
  matchId: number;
  tier: string;
  option: MultiOption;
  sourceMode: PlacedBetSourceMode;
}) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [added, setAdded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (added) {
    return <span className="add-bet-button add-bet-button--added">Whole multi added ✓</span>;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    const groupId = crypto.randomUUID();
    try {
      for (const leg of option.legs) {
        await createPlacedBet({
          match_id: matchId,
          opportunity_type: leg.opportunity_type,
          label: leg.label,
          selection: leg.selection ?? (leg.opportunity_type === "player" ? "over" : ""),
          market_type: leg.market_type,
          bookmaker: option.bookmaker,
          odds_taken: leg.bookmaker_price,
          model_probability: leg.model_probability,
          model_fair_odds: leg.model_fair_odds,
          confidence_tier: leg.confidence_tier,
          source_mode: sourceMode,
          player_id: leg.player_id,
          line_type: leg.line_type,
          threshold: leg.threshold,
          line_value: leg.line_value,
          lineup_status: leg.selection_status,
          model_version: leg.model_version,
          multi_group_id: groupId,
          multi_tier: tier,
          multi_indicative_odds: option.indicative_combined_odds,
        });
      }
      setAdded(true);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add multi");
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="add-bet-button add-bet-button--multi" onClick={() => setOpen(true)}>
        + Add whole multi
      </button>
    );
  }

  return (
    <div className="add-bet-form">
      <p className="hint">
        Records all {option.n_legs} legs as one multi at {option.bookmaker}, indicative odds $
        {option.indicative_combined_odds.toFixed(2)}. Component legs, model probabilities, and model versions are
        frozen exactly as shown now.
      </p>
      <div className="add-bet-form__actions">
        <button type="button" onClick={handleSubmit} disabled={submitting}>
          {submitting ? "Saving..." : "Confirm all legs placed"}
        </button>
        <button type="button" className="add-bet-form__cancel" onClick={() => setOpen(false)} disabled={submitting}>
          Cancel
        </button>
      </div>
      {error && <p className="add-bet-form__error">{error}</p>}
    </div>
  );
}
