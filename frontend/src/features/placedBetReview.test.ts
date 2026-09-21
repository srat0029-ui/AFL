import { describe, expect, it } from "vitest";
import type { PlacedBet } from "../api/client";
import { missedByText, multiGroupSummary } from "./placedBetReview";

function bet(overrides: Partial<PlacedBet>): PlacedBet {
  return {
    id: 1,
    status: "lost",
    market_type: "player_disposals",
    multi_group_id: "g1",
    shortfall: null,
    ...overrides,
  } as PlacedBet;
}

describe("missedByText", () => {
  it("states the shortfall for a lost player-stat leg without calling it a win", () => {
    expect(missedByText(bet({ shortfall: 2 }))).toBe("Missed by 2 disposals");
    expect(missedByText(bet({ shortfall: 1 }))).toBe("Missed by 1 disposal");
    expect(missedByText(bet({ shortfall: 1, market_type: "player_goals" }))).toBe("Missed by 1 goal");
  });

  it("is absent for won legs and legs without a shortfall", () => {
    expect(missedByText(bet({ status: "won", shortfall: null }))).toBeNull();
    expect(missedByText(bet({ shortfall: null }))).toBeNull();
  });
});

describe("multiGroupSummary", () => {
  const legs = [
    bet({ id: 1, status: "won" }),
    bet({ id: 2, status: "won" }),
    bet({ id: 3, status: "won" }),
    bet({ id: 4, status: "won" }),
    bet({ id: 5, status: "lost", shortfall: 2 }),
  ];

  it("reports how many legs of a settled multi hit", () => {
    expect(multiGroupSummary(legs[0], legs)).toBe("4 of 5 legs hit");
  });

  it("reports progress while legs are pending and nothing has lost", () => {
    const pending = [bet({ id: 1, status: "won" }), bet({ id: 2, status: "pending" })];
    expect(multiGroupSummary(pending[0], pending)).toBe("1 of 2 legs hit so far · 1 pending");
  });

  it("does nothing for single bets", () => {
    expect(multiGroupSummary(bet({ multi_group_id: null }), legs)).toBeNull();
    expect(multiGroupSummary(bet({ multi_group_id: "solo" }), [bet({ multi_group_id: "solo" })])).toBeNull();
  });
});
