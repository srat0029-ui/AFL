export type TeammateStatus = "in" | "out";
export type PlayerRole = "half_back" | "mixed" | "midfield";
export type TagAssumption = "none" | "possible" | "likely";

export interface ContextSplit {
  games: number;
  average: number;
  median: number;
  hitRate25: number;
  hitRate30: number;
  averageTog: number;
}

export interface ContextEvidenceGame {
  id: string;
  round: string;
  opponent: string;
  venue: string;
  teammateStatus: TeammateStatus;
  role: string;
  tag: "No" | "Possible" | "Likely";
  disposals: number;
}

export interface PlayerContextResearch {
  player: {
    id: number;
    name: string;
    team: string;
    initials: string;
  };
  upcomingMatch: {
    opponent: string;
    venue: string;
    startLabel: string;
  };
  baselineProjection: number;
  modelRange: [number, number];
  teammate: {
    id: number;
    name: string;
    shortName: string;
  };
  splits: Record<TeammateStatus, ContextSplit>;
  adjustedTeammateImpact: number;
  tagEstimate: {
    probability: number;
    confidence: "Low" | "Medium" | "High";
    factors: { label: string; direction: "up" | "down" | "neutral"; detail: string }[];
  };
  evidence: ContextEvidenceGame[];
}

export interface ScenarioSelection {
  teammateStatus: TeammateStatus;
  role: PlayerRole;
  tagAssumption: TagAssumption;
}

export interface ScenarioProjection {
  expected: number;
  range: [number, number];
  change: number;
  probabilities: { threshold: number; probability: number }[];
  adjustments: { label: string; value: number }[];
}

export const MOCK_PLAYER_CONTEXT: PlayerContextResearch = {
  player: {
    id: 1,
    name: "Harry Sheezel",
    team: "North Melbourne",
    initials: "HS",
  },
  upcomingMatch: {
    opponent: "Melbourne",
    venue: "MCG",
    startLabel: "Upcoming matchup · prototype",
  },
  baselineProjection: 29.8,
  modelRange: [21, 38],
  teammate: {
    id: 2,
    name: "Luke Davies-Uniacke",
    shortName: "L. Davies-Uniacke",
  },
  splits: {
    in: {
      games: 31,
      average: 27.6,
      median: 28,
      hitRate25: 0.68,
      hitRate30: 0.39,
      averageTog: 83,
    },
    out: {
      games: 7,
      average: 32.1,
      median: 33,
      hitRate25: 0.86,
      hitRate30: 0.71,
      averageTog: 86,
    },
  },
  adjustedTeammateImpact: 2.4,
  tagEstimate: {
    probability: 0.38,
    confidence: "Low",
    factors: [
      {
        label: "Opposition tendency",
        direction: "up",
        detail: "The opponent has recently used accountable midfield roles.",
      },
      {
        label: "Expected role",
        direction: "up",
        detail: "A midfield-heavy role makes direct attention more plausible.",
      },
      {
        label: "Multiple threats",
        direction: "down",
        detail: "Other high-impact midfielders make the likely target uncertain.",
      },
    ],
  },
  evidence: [
    { id: "g1", round: "R18, 2025", opponent: "Carlton", venue: "Marvel", teammateStatus: "out", role: "Midfield", tag: "Possible", disposals: 35 },
    { id: "g2", round: "R15, 2025", opponent: "Western Bulldogs", venue: "Marvel", teammateStatus: "out", role: "Midfield", tag: "No", disposals: 33 },
    { id: "g3", round: "R11, 2025", opponent: "Essendon", venue: "MCG", teammateStatus: "in", role: "Mixed", tag: "Likely", disposals: 24 },
    { id: "g4", round: "R8, 2025", opponent: "Richmond", venue: "Blundstone", teammateStatus: "in", role: "Half-back", tag: "No", disposals: 31 },
    { id: "g5", round: "R4, 2025", opponent: "Sydney", venue: "SCG", teammateStatus: "in", role: "Mixed", tag: "Possible", disposals: 26 },
    { id: "g6", round: "R22, 2024", opponent: "West Coast", venue: "Blundstone", teammateStatus: "out", role: "Midfield", tag: "No", disposals: 36 },
  ],
};

const ROLE_ADJUSTMENTS: Record<PlayerRole, number> = {
  half_back: 1.2,
  mixed: 0,
  midfield: 2.1,
};

const TAG_ADJUSTMENTS: Record<TagAssumption, number> = {
  none: 0.8,
  possible: -1.2,
  likely: -3.8,
};

export const ROLE_LABELS: Record<PlayerRole, string> = {
  half_back: "Half-back",
  mixed: "Mixed role",
  midfield: "Midfield",
};

export const TAG_LABELS: Record<TagAssumption, string> = {
  none: "No tag",
  possible: "Possible tag",
  likely: "Likely tag",
};

function milestoneProbability(mean: number, threshold: number): number {
  return 1 / (1 + Math.exp((threshold - 0.5 - mean) / 3.8));
}

export function buildScenarioProjection(
  research: PlayerContextResearch,
  selection: ScenarioSelection,
): ScenarioProjection {
  const teammateAdjustment = selection.teammateStatus === "out" ? research.adjustedTeammateImpact : 0;
  const roleAdjustment = ROLE_ADJUSTMENTS[selection.role];
  const tagAdjustment = TAG_ADJUSTMENTS[selection.tagAssumption];
  const change = teammateAdjustment + roleAdjustment + tagAdjustment;
  const expected = research.baselineProjection + change;

  return {
    expected,
    range: [research.modelRange[0] + change, research.modelRange[1] + change],
    change,
    probabilities: [25, 30, 35].map((threshold) => ({
      threshold,
      probability: milestoneProbability(expected, threshold),
    })),
    adjustments: [
      { label: `${research.teammate.shortName} ${selection.teammateStatus === "in" ? "in" : "out"}`, value: teammateAdjustment },
      { label: ROLE_LABELS[selection.role], value: roleAdjustment },
      { label: TAG_LABELS[selection.tagAssumption], value: tagAdjustment },
    ],
  };
}

