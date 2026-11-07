import type { NbaStatName, NbaStatPrediction } from "../api/client";

export const STAT_NAMES: NbaStatName[] = ["points", "rebounds", "assists"];

export const STAT_SHORT: Record<NbaStatName, string> = { points: "PTS", rebounds: "REB", assists: "AST" };

export const BASELINE_LABELS: Record<string, string> = {
  season_to_date: "Season to date",
  last_10: "Last 10",
  ewma_4: "EWMA (4)",
  ewma_10: "EWMA (10)",
  rate_ewma: "Rate EWMA × minutes",
};

/** The frozen mean to one decimal. */
export function formatStatMean(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? "—" : value.toFixed(1);
}

/** "4–11", or null where no range may be shown (the distribution failed its
 * offline rule, so it is not displayed rather than shown with a warning). */
export function formatStatRange(pred: NbaStatPrediction | undefined): string | null {
  if (!pred || !pred.calibrated || !pred.interval_80) return null;
  return `${pred.interval_80.p10}–${pred.interval_80.p90}`;
}

/** The labels with at least one frozen row, in window order. */
export function labelsWithRows(labels: string[], windows: { label: string }[]): string[] {
  const present = new Set(windows.map((w) => w.label));
  return labels.filter((l) => present.has(l));
}

/** Plain words for an offline validation status. */
export function validationText(status: { mean: string; distribution: string; calibrated: boolean } | undefined): string {
  if (!status) return "No rows";
  const word = (s: string) => (s === "passed_offline" ? "passed" : s === "failed_offline" ? "failed" : s);
  return `Mean ${word(status.mean)} · distribution ${word(status.distribution)} offline`;
}
