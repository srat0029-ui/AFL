import type { MarketMovementIdentity, MatchMarketOptions, PlayerMarketOption, TeamMarketOption } from "../api/client";

export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

export function pctChange(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)}pp`;
}

export function hrs(v: number): string {
  return `${v.toFixed(1)}h`;
}

/** Evenly-spaced hour ticks from kickoff (0) out to maxHours, capped at
 * roughly 6 intervals so the x-axis stays readable. Kickoff is always the
 * loop's first value — never appended a second time. */
export function niceHourTicks(maxHours: number): number[] {
  if (maxHours <= 0) return [0];
  const steps = [1, 2, 3, 6, 12, 24, 48, 72, 96, 120, 168, 240];
  const step = steps.find((s) => maxHours / s <= 6) ?? Math.ceil(maxHours / 6);
  const ticks: number[] = [];
  for (let h = 0; h <= maxHours; h += step) ticks.push(h);
  return ticks;
}

export interface OptionChoice {
  key: string;
  group: "Team markets" | "Player markets";
  label: string;
  identity: MarketMovementIdentity;
  nQuotes: number;
  bookmakers: string[];
  hasModelSeries: boolean;
}

export function teamOptionLabel(o: TeamMarketOption): string {
  if (o.market_type === "h2h") return `${o.selection} — Head to head`;
  if (o.market_type === "total") return `${o.selection} ${o.line_value ?? ""} — Total`;
  return `${o.selection} ${o.line_value !== null && o.line_value >= 0 ? "+" : ""}${o.line_value ?? ""} — Line`;
}

export function playerOptionLabel(o: PlayerMarketOption): string {
  const marketLabel = o.market_type === "player_disposals" ? "Disposals" : "Goals";
  const thresholdLabel = o.line_type === "multi_plus" ? `${o.threshold}+` : `${o.threshold}`;
  return `${o.player_name} — ${marketLabel} ${thresholdLabel}`;
}

export function buildOptions(options: MatchMarketOptions, matchId: number): OptionChoice[] {
  const team: OptionChoice[] = options.team_markets.map((o) => ({
    key: `team:${o.market_type}:${o.selection}:${o.line_value ?? "null"}`,
    group: "Team markets",
    label: teamOptionLabel(o),
    identity: { identityType: "team", matchId, marketType: o.market_type, selection: o.selection, lineValue: o.line_value },
    nQuotes: o.n_quotes,
    bookmakers: o.bookmakers,
    hasModelSeries: o.has_model_series,
  }));
  const player: OptionChoice[] = options.player_markets.map((o) => ({
    key: `player:${o.player_id}:${o.market_type}:${o.line_type}:${o.threshold}`,
    group: "Player markets",
    label: playerOptionLabel(o),
    identity: { identityType: "player", matchId, marketType: o.market_type, playerId: o.player_id, lineType: o.line_type, threshold: o.threshold },
    nQuotes: o.n_quotes,
    bookmakers: o.bookmakers,
    hasModelSeries: o.has_model_series,
  }));
  return [...team, ...player];
}
