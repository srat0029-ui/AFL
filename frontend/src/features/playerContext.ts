export type ContextStat = "disposals" | "goals";
export interface ContextSplit {
  games: number;
  stat_sample_size: number;
  mean: number | null;
  median: number | null;
  milestone_rates: Record<string, number | null>;
  average_time_on_ground_pct: number | null;
  time_on_ground_sample_size: number;
}
export interface ContextEvidenceGame {
  match_id: number;
  season_year: number;
  round_number: number;
  round_name: string | null;
  scheduled_start: string;
  team_id: number;
  team_name: string;
  opponent_team_id: number | null;
  opponent_name: string | null;
  venue_name: string | null;
  teammate_played: boolean;
  stat_value: number | null;
  time_on_ground_pct: number | null;
}
export interface PlayerContextResearch {
  player_id: number;
  player_name: string;
  teammate_id: number;
  teammate_name: string;
  team_id: number | null;
  team_name: string | null;
  stat: string;
  thresholds: number[];
  with_teammate: ContextSplit;
  without_teammate: ContextSplit;
  raw_difference: number | null;
  adjusted_effect: {
    available: boolean;
    value: number | null;
    games_with_baseline_teammate_in: number;
    games_with_baseline_teammate_out: number;
    method: string;
    explanation: string;
  };
  confounders: Record<string, { considered: boolean; method: string | null; reason: string | null }>;
  confidence: { tier: string; warnings: string[] };
  evidence: ContextEvidenceGame[];
  role_analysis_available: boolean;
  role_analysis_explanation: string;
  tag_watch: { status: string; verified_annotation_count: number; games_played: number | null; tag_rate: number | null; explanation: string };
}
export const formatValue = (value: number | null | undefined, digits = 1): string =>
  value == null || !Number.isFinite(value) ? "Unavailable" : value.toFixed(digits);
export const formatRate = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "Unavailable" : `${Math.round(value * 100)}%`;
export const formatDifference = (value: number | null): string =>
  value == null || !Number.isFinite(value) ? "Unavailable" : `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
export function explainDifference(research: PlayerContextResearch): string {
  if (research.raw_difference == null) return "A comparison is unavailable because one or both groups have no recorded values for this statistic.";
  const value = research.raw_difference;
  return `${research.player_name} averaged ${Math.abs(value).toFixed(1)} ${value < 0 ? "fewer" : "more"} ${research.stat} with ${research.teammate_name} absent than present${value === 0 ? " (no difference)" : ""}. This is a historical association, not proof that the teammate caused the change or a prediction for the next game.`;
}

export type EvidenceOrder = "newest" | "oldest" | "highest" | "lowest";
export interface EvidenceFilters {
  teammate: "all" | "in" | "out";
  season: number | null;
  opponent: string;
  order: EvidenceOrder;
}
export function selectEvidence(rows: ContextEvidenceGame[], filters: EvidenceFilters): ContextEvidenceGame[] {
  const opponent = filters.opponent.trim().toLocaleLowerCase();
  return rows.filter(row =>
    (filters.teammate === "all" || row.teammate_played === (filters.teammate === "in")) &&
    (filters.season === null || row.season_year === filters.season) &&
    (!opponent || (row.opponent_name ?? "").toLocaleLowerCase().includes(opponent)),
  ).sort((a, b) => {
    if (filters.order === "highest" || filters.order === "lowest") {
      // Missing statistics remain last; they must not be treated as zero.
      if (a.stat_value === null && b.stat_value !== null) return 1;
      if (b.stat_value === null && a.stat_value !== null) return -1;
      if (a.stat_value !== null && b.stat_value !== null && a.stat_value !== b.stat_value)
        return filters.order === "highest" ? b.stat_value - a.stat_value : a.stat_value - b.stat_value;
    }
    const chronological = a.scheduled_start.localeCompare(b.scheduled_start) || a.match_id - b.match_id;
    return filters.order === "oldest" ? chronological : -chronological;
  });
}
