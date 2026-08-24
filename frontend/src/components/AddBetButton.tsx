import { useState } from "react";
import { createPlacedBet, type PlacedBetCreateInput, type PlacedBetSourceMode } from "../api/client";
import "./AddBetButton.css";

// Freezes the exact model/market snapshot shown on the card at the moment
// of the click into a PlacedBet — see backend app/player_modelling/
// placed_bets.py's module docstring: nothing here feeds model training or
// ranking, this is personal record-keeping only, and no staking advice is
// offered (stake is a plain optional number the user types themselves).
export interface AddBetSnapshot {
  matchId: number;
  opportunityType: "player" | "team";
  label: string;
  selection: string;
  marketType: string;
  bookmaker: string;
  oddsTaken: number;
  modelProbability: number;
  modelFairOdds: number;
  confidenceTier: string;
  sourceMode: PlacedBetSourceMode;
  playerId?: number | null;
  lineType?: string | null;
  threshold?: number | null;
  lineValue?: number | null;
  lineupStatus?: string | null;
  modelVersion?: string | null;
}

export function AddBetButton({ snapshot }: { snapshot: AddBetSnapshot }) {
  const [open, setOpen] = useState(false);
  const [stake, setStake] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [added, setAdded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (added) {
    return <span className="add-bet-button add-bet-button--added">Added to Placed Bets ✓</span>;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    const input: PlacedBetCreateInput = {
      match_id: snapshot.matchId,
      opportunity_type: snapshot.opportunityType,
      label: snapshot.label,
      selection: snapshot.selection,
      market_type: snapshot.marketType,
      bookmaker: snapshot.bookmaker,
      odds_taken: snapshot.oddsTaken,
      model_probability: snapshot.modelProbability,
      model_fair_odds: snapshot.modelFairOdds,
      confidence_tier: snapshot.confidenceTier,
      source_mode: snapshot.sourceMode,
      player_id: snapshot.playerId ?? null,
      line_type: snapshot.lineType ?? null,
      threshold: snapshot.threshold ?? null,
      line_value: snapshot.lineValue ?? null,
      lineup_status: snapshot.lineupStatus ?? null,
      model_version: snapshot.modelVersion ?? null,
      stake: stake.trim() === "" ? null : Number(stake),
    };
    try {
      await createPlacedBet(input);
      setAdded(true);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add bet");
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="add-bet-button" onClick={() => setOpen(true)}>
        + Add to Placed Bets
      </button>
    );
  }

  return (
    <div className="add-bet-form">
      <label>
        Stake (optional)
        <input
          type="number"
          step="0.01"
          min="0"
          value={stake}
          onChange={(e) => setStake(e.target.value)}
          placeholder="e.g. 20"
        />
      </label>
      <div className="add-bet-form__actions">
        <button type="button" onClick={handleSubmit} disabled={submitting}>
          {submitting ? "Saving..." : "Confirm bet placed"}
        </button>
        <button type="button" className="add-bet-form__cancel" onClick={() => setOpen(false)} disabled={submitting}>
          Cancel
        </button>
      </div>
      {error && <p className="add-bet-form__error">{error}</p>}
    </div>
  );
}
