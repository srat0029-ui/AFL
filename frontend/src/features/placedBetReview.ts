import type { PlacedBet } from "../api/client";

// Post-settlement review lines. A near miss is still a loss: this states how
// far a leg finished from its requirement and how many legs of a multi hit -
// it never reclassifies anything.
const SHORTFALL_UNIT: Record<string, [string, string]> = {
  player_disposals: ["disposal", "disposals"],
  player_goals: ["goal", "goals"],
};

export function missedByText(bet: PlacedBet): string | null {
  if (bet.status !== "lost" || bet.shortfall == null) return null;
  const [one, many] = SHORTFALL_UNIT[bet.market_type] ?? ["", ""];
  const n = Math.round(bet.shortfall);
  return `Missed by ${n}${many ? ` ${n === 1 ? one : many}` : ""}`;
}

export function multiGroupSummary(bet: PlacedBet, all: PlacedBet[]): string | null {
  if (!bet.multi_group_id) return null;
  const legs = all.filter((b) => b.multi_group_id === bet.multi_group_id);
  if (legs.length < 2) return null;
  const won = legs.filter((b) => b.status === "won").length;
  const lost = legs.filter((b) => b.status === "lost").length;
  const pending = legs.filter((b) => b.status === "pending").length;
  const counted = legs.length - legs.filter((b) => b.status === "void" || b.status === "push").length;
  if (lost === 0 && pending > 0) return `${won} of ${counted} legs hit so far · ${pending} pending`;
  return `${won} of ${counted} legs hit${lost > 0 && pending > 0 ? ` · ${pending} still pending` : ""}`;
}
