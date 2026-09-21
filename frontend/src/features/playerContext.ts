export type ContextStat = "disposals" | "goals";

// Explicit time window for teammate context. The active window is always
// shown next to the headline result and is never broadened automatically.
export type ContextWindowKey = "current_season" | "last_2_seasons" | "current_club_career";
export const DEFAULT_CONTEXT_WINDOW: ContextWindowKey = "current_season";
export const CONTEXT_WINDOW_OPTIONS: { key: ContextWindowKey; short: string; action: string }[] = [
  { key: "current_season", short: "Season", action: "View current season" },
  { key: "last_2_seasons", short: "Last 2 seasons", action: "View last 2 seasons" },
  { key: "current_club_career", short: "Club career", action: "View current-club career" },
];
export interface ContextWindowInfo {
  key: ContextWindowKey;
  label: string;
  scope_label: string; // "2026 season" | "Last 2 seasons · 2025–2026" | "Current club career · 2022–2026"
  anchor_season_year: number | null;
  included_seasons: number[];
  earliest_date: string | null;
  latest_date: string | null;
  games_considered: number;
  games_excluded_missing_season: number;
}
export interface SeasonSplit {
  season_year: number;
  with_teammate: { games: number; mean: number | null };
  without_teammate: { games: number; mean: number | null };
}
export interface WindowSummary {
  key: ContextWindowKey;
  label: string;
  scope_label: string;
  games_with: number;
  games_without: number;
  confidence_tier: string;
  sufficient: boolean;
}
export interface WindowSufficiency {
  sufficient: boolean;
  message: string | null;
  suggested_windows: ContextWindowKey[];
}
export interface TeammateTenure {
  first_game_at_club: string | null;
  apart_games_not_at_club: number;
  apart_games: number;
  note: string | null;
}
export interface WindowOption {
  key: ContextWindowKey;
  label: string;
  scope_label: string;
  games_considered: number;
  n_sufficient_candidates: number;
}

const WINDOW_WHEN: Record<ContextWindowKey, string> = {
  current_season: "this season",
  last_2_seasons: "over the last 2 seasons",
  current_club_career: "in this club career",
};
/** "12 together / 4 apart this season" - the sample sizes behind a comparison, scoped to the window. */
export const togetherApartLabel = (withGames: number, withoutGames: number, window: ContextWindowKey): string =>
  `${withGames} together / ${withoutGames} apart ${WINDOW_WHEN[window]}`;
export const windowActionLabel = (key: ContextWindowKey): string => CONTEXT_WINDOW_OPTIONS.find(o => o.key === key)?.action ?? "View";
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
  window: ContextWindowInfo;
  season_breakdown: SeasonSplit[];
  window_summaries: WindowSummary[];
  sufficiency: WindowSufficiency;
  teammate_tenure: TeammateTenure;
  tag_watch: { status: string; verified_annotation_count: number; games_played: number | null; tag_rate: number | null; explanation: string };
}
export interface TeammateCandidate {
  teammate_id: number;
  teammate_name: string;
  with_teammate: ContextSplit;
  without_teammate: ContextSplit;
  raw_difference: number | null;
  adjusted_effect: PlayerContextResearch["adjusted_effect"];
  confidence: { tier: string; warnings: string[] };
  sufficient_evidence: boolean;
}
export interface TeammateDiscoveryResult {
  player_id: number;
  player_name: string;
  team_id: number | null;
  team_name: string | null;
  stat: string;
  thresholds: number[];
  explanation: string;
  window: ContextWindowInfo;
  window_options: WindowOption[];
  candidates: TeammateCandidate[];
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
  return `${research.player_name} averaged ${Math.abs(value).toFixed(1)} ${value < 0 ? "fewer" : "more"} ${research.stat} with ${research.teammate_name} absent than present (${research.window.scope_label})${value === 0 ? ", no difference" : ""}. This is a historical association, not proof that the teammate caused the change or a prediction for the next game.`;
}

export function explainCandidateDifference(playerName: string, stat: string, candidate: TeammateCandidate): string {
  if (candidate.raw_difference == null) return "A comparison is unavailable because one or both groups have no recorded values for this statistic.";
  const value = candidate.raw_difference;
  return `${playerName} averaged ${Math.abs(value).toFixed(1)} ${value < 0 ? "fewer" : "more"} ${stat} with ${candidate.teammate_name} absent than present${value === 0 ? " (no difference)" : ""}. This is a historical association, not proof of a cause or a prediction for the next game.`;
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

// --- Opponent context (Player Research "Opponents" mode) ---
// Reuses ContextSplit's shape (identical fields) for against_opponent /
// against_other_opponents - only the comparison semantics differ from
// the teammate with/without-split above, not the underlying stat shape.

export interface OpponentAdjustedEffect {
  available: boolean;
  value: number | null;
  games_with_baseline_against_opponent: number;
  games_with_baseline_other_opponents: number;
  method: string;
  explanation: string;
}
export interface OpponentEvidenceGame {
  match_id: number;
  season_year: number;
  round_number: number;
  round_name: string | null;
  scheduled_start: string;
  team_id: number;
  team_name: string;
  opponent_team_id: number;
  opponent_name: string;
  venue_name: string | null;
  is_home: boolean | null;
  is_selected_opponent: boolean;
  stat_value: number | null;
  time_on_ground_pct: number | null;
}
export interface OpponentContextResearch {
  player_id: number;
  player_name: string;
  opponent_team_id: number;
  opponent_team_name: string;
  team_id: number | null;
  team_name: string | null;
  stat: string;
  thresholds: number[];
  against_opponent: ContextSplit;
  against_other_opponents: ContextSplit;
  raw_difference: number | null;
  adjusted_effect: OpponentAdjustedEffect;
  confounders: Record<string, { considered: boolean; method: string | null; reason: string | null }>;
  confidence: { tier: string; warnings: string[] };
  evidence: OpponentEvidenceGame[];
  role_analysis_available: boolean;
  role_analysis_explanation: string;
  scope_explanation: string;
}
export interface OpponentCandidate {
  opponent_team_id: number;
  opponent_team_name: string;
  against_opponent: ContextSplit;
  against_other_opponents: ContextSplit;
  raw_difference: number | null;
  adjusted_effect: OpponentAdjustedEffect;
  confidence: { tier: string; warnings: string[] };
  sufficient_evidence: boolean;
}
export interface OpponentDiscoveryResult {
  player_id: number;
  player_name: string;
  team_id: number | null;
  team_name: string | null;
  stat: string;
  thresholds: number[];
  explanation: string;
  scope_explanation: string;
  candidates: OpponentCandidate[];
}

export function explainOpponentDifference(research: OpponentContextResearch): string {
  if (research.raw_difference == null) return "A comparison is unavailable because one or both groups have no recorded values for this statistic.";
  const value = research.raw_difference;
  return `${research.player_name} historically averaged ${Math.abs(value).toFixed(1)} ${value < 0 ? "fewer" : "more"} ${research.stat} against ${research.opponent_team_name} compared with other opponents${value === 0 ? " (no difference)" : ""}. This is a historical association recorded in past games, not a prediction for the next match or a matchup advantage.`;
}

export function explainOpponentCandidateDifference(playerName: string, stat: string, candidate: OpponentCandidate): string {
  if (candidate.raw_difference == null) return "A comparison is unavailable because one or both groups have no recorded values for this statistic.";
  const value = candidate.raw_difference;
  return `${playerName} historically averaged ${Math.abs(value).toFixed(1)} ${value < 0 ? "fewer" : "more"} ${stat} against ${candidate.opponent_team_name} compared with other opponents${value === 0 ? " (no difference)" : ""}. This is a historical association, not a prediction for the next match.`;
}

export type OpponentEvidenceOrder = EvidenceOrder;
export interface OpponentEvidenceFilters {
  selection: "all" | "against" | "other";
  season: number | null;
  opponent: string;
  order: OpponentEvidenceOrder;
}
export function selectOpponentEvidence(rows: OpponentEvidenceGame[], filters: OpponentEvidenceFilters): OpponentEvidenceGame[] {
  const opponent = filters.opponent.trim().toLocaleLowerCase();
  return rows.filter(row =>
    (filters.selection === "all" || row.is_selected_opponent === (filters.selection === "against")) &&
    (filters.season === null || row.season_year === filters.season) &&
    (!opponent || row.opponent_name.toLocaleLowerCase().includes(opponent)),
  ).sort((a, b) => {
    if (filters.order === "highest" || filters.order === "lowest") {
      if (a.stat_value === null && b.stat_value !== null) return 1;
      if (b.stat_value === null && a.stat_value !== null) return -1;
      if (a.stat_value !== null && b.stat_value !== null && a.stat_value !== b.stat_value)
        return filters.order === "highest" ? b.stat_value - a.stat_value : a.stat_value - b.stat_value;
    }
    const chronological = a.scheduled_start.localeCompare(b.scheduled_start) || a.match_id - b.match_id;
    return filters.order === "oldest" ? chronological : -chronological;
  });
}
