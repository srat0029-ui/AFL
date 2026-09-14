import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { fetchOpponentContext, fetchOpponentDiscovery } from "../api/client";
import OpponentDiscoveryPanel from "../components/OpponentDiscoveryPanel";
import { OpponentContextResults } from "../pages/PlayerResearchPage";
import { explainOpponentCandidateDifference, explainOpponentDifference, type OpponentCandidate, type OpponentContextResearch, type OpponentDiscoveryResult } from "./playerContext";

const split = { games: 0, stat_sample_size: 0, mean: null, median: null, milestone_rates: { "15+": null }, average_time_on_ground_pct: null, time_on_ground_sample_size: 0 };

function makeCandidate(overrides: Partial<OpponentCandidate>): OpponentCandidate {
  return {
    opponent_team_id: 1,
    opponent_team_name: "Opponent One",
    against_opponent: split,
    against_other_opponents: split,
    raw_difference: null,
    adjusted_effect: { available: false, value: null, games_with_baseline_against_opponent: 0, games_with_baseline_other_opponents: 0, method: "recent_form_residual", explanation: "Not enough baseline-eligible games." },
    confidence: { tier: "insufficient_history", warnings: ["Fewer than 3 games in the smaller group."] },
    sufficient_evidence: false,
    ...overrides,
  };
}

const discoveryFixture: OpponentDiscoveryResult = {
  player_id: 101, player_name: "Player One", team_id: 5, team_name: "Example Club", stat: "disposals", thresholds: [15],
  explanation: "2 opponent(s) found.", scope_explanation: "This history is restricted to the player's most recent recorded club.",
  candidates: [
    makeCandidate({
      opponent_team_id: 201, opponent_team_name: "Solid Rivals",
      against_opponent: { ...split, games: 12, stat_sample_size: 12, mean: 22 },
      against_other_opponents: { ...split, games: 10, stat_sample_size: 10, mean: 20 },
      raw_difference: 2,
      adjusted_effect: { available: true, value: 1.5, games_with_baseline_against_opponent: 10, games_with_baseline_other_opponents: 8, method: "recent_form_residual", explanation: "Computed from trailing form residuals." },
      confidence: { tier: "moderate_confidence", warnings: [] },
      sufficient_evidence: true,
    }),
    makeCandidate({
      opponent_team_id: 202, opponent_team_name: "Barely Faced",
      against_opponent: { ...split, games: 1, stat_sample_size: 1, mean: 25 },
      against_other_opponents: { ...split, games: 20, stat_sample_size: 20, mean: 20 },
      raw_difference: 5,
      confidence: { tier: "insufficient_history", warnings: ["Fewer than 3 games in the smaller group - treat this split as anecdotal, not a reliable estimate."] },
      sufficient_evidence: false,
    }),
  ],
};

const opponentResearchFixture: OpponentContextResearch = {
  player_id: 101, player_name: "Player One", opponent_team_id: 201, opponent_team_name: "Solid Rivals", team_id: 5, team_name: "Example Club",
  stat: "disposals", thresholds: [15],
  against_opponent: { ...split, games: 12, stat_sample_size: 12, mean: 22 },
  against_other_opponents: { ...split, games: 10, stat_sample_size: 10, mean: 20 },
  raw_difference: 2,
  adjusted_effect: { available: false, value: null, games_with_baseline_against_opponent: 2, games_with_baseline_other_opponents: 8, method: "recent_form_residual", explanation: "Need at least 5 games with a computable baseline in each group." },
  confounders: { recent_form: { considered: true, method: "Trailing form residual.", reason: null }, lineup_and_era_context: { considered: false, method: null, reason: "Different eras and lineups are not controlled for." } },
  confidence: { tier: "lower_confidence", warnings: ["Only 10 games in the smaller group."] },
  evidence: [
    { match_id: 55, season_year: 2025, round_number: 3, round_name: null, scheduled_start: "2025-04-01T00:00:00Z", team_id: 5, team_name: "Example Club", opponent_team_id: 201, opponent_name: "Solid Rivals", venue_name: "Example Oval", is_home: true, is_selected_opponent: true, stat_value: 24, time_on_ground_pct: 88 },
  ],
  role_analysis_available: false,
  role_analysis_explanation: "No structured position/role data exists.",
  scope_explanation: "This history is restricted to the player's most recent recorded club.",
};

const renderPanel = (props: Partial<Parameters<typeof OpponentDiscoveryPanel>[0]> = {}) =>
  renderToStaticMarkup(createElement(OpponentDiscoveryPanel, {
    playerName: "Player One", stat: "disposals", loading: false, error: null, discovery: null,
    onRetry: () => {}, onSelect: () => {}, ...props,
  }));

const renderResults = (research: OpponentContextResearch) =>
  renderToStaticMarkup(createElement(MemoryRouter, null, createElement(OpponentContextResults, { research })));

afterEach(() => vi.unstubAllGlobals());

describe("fetchOpponentDiscovery", () => {
  it("requests the opponent-context-candidates endpoint for the selected player and statistic", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(discoveryFixture), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await fetchOpponentDiscovery(101, "goals")).toEqual(discoveryFixture);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/afl/players/101/opponent-context-candidates?stat=goals"), expect.anything());
  });

  it("surfaces HTTP errors without a mock fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Player not found" }), { status: 404 })));
    await expect(fetchOpponentDiscovery(999999)).rejects.toMatchObject({ status: 404, message: "Player not found" });
  });
});

describe("fetchOpponentContext", () => {
  it("requests the opponent-context endpoint for the selected player, opponent and statistic", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(opponentResearchFixture), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await fetchOpponentContext(101, 201, "disposals")).toEqual(opponentResearchFixture);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/afl/players/101/opponent-context/201?stat=disposals"), expect.anything());
  });

  it("propagates network errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(fetchOpponentContext(101, 201)).rejects.toThrow("Failed to fetch");
  });
});

describe("OpponentDiscoveryPanel", () => {
  it("shows a loading state", () => {
    expect(renderPanel({ loading: true })).toContain("Finding opponents worth investigating");
  });

  it("shows an error state with retry", () => {
    const html = renderPanel({ error: "Network error" });
    expect(html).toContain("Network error");
    expect(html).toContain("Retry");
  });

  it("shows a no-opponent-history state distinct from loading or error", () => {
    const html = renderPanel({ discovery: { ...discoveryFixture, candidates: [], explanation: "No recorded opponent information exists for Example Club." } });
    expect(html).toContain("No opponent history found");
    expect(html).toContain("No recorded opponent information exists");
  });

  it("groups candidates into sufficient and insufficient evidence sections", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).toContain("Worth investigating (1)");
    expect(html).toContain("Insufficient evidence (1)");
    expect(html).toContain("Solid Rivals");
    expect(html).toContain("Barely Faced");
    expect(html).toContain("Compare against Solid Rivals");
    expect(html).toContain("Compare against Barely Faced");
  });

  it("shows games-against and comparison sample sizes for every candidate", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).toMatch(/Games against<\/dt><dd>12<\/dd>/);
    expect(html).toMatch(/Games vs others<\/dt><dd>10<\/dd>/);
  });

  it("shows the adjusted difference only when available, honest 'Unavailable' otherwise", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).toContain("+1.5");
    const barelyFacedSection = html.split("Barely Faced")[1] ?? "";
    expect(barelyFacedSection).toContain("Unavailable");
  });

  it("never presents the difference as a matchup advantage or prediction", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).not.toContain("matchup advantage");
    expect(html).not.toContain("betting edge");
    expect(html).not.toContain("will average");
    expect(html).toContain("not a prediction for the next match");
  });

  it("renders nothing but is not an error when there is no discovery data yet", () => {
    expect(renderPanel({ discovery: null })).toBe("");
  });
});

describe("OpponentContextResults", () => {
  it("shows the honest scope restriction and never claims causation", () => {
    const html = renderResults(opponentResearchFixture);
    expect(html).toContain("most recent recorded club");
    expect(html).toContain("historical association");
    expect(html).not.toContain("causes");
    // Explicitly disclaims matchup-advantage/prediction framing - the
    // phrase appears only inside that denial, never as a positive claim.
    expect(html).toContain("not a prediction for the next match or a matchup advantage");
    expect(html).not.toContain("will score more");
  });

  it("shows adjusted-effect-unavailable honestly rather than fabricating a number", () => {
    const html = renderResults(opponentResearchFixture);
    expect(html).toContain("Adjusted difference unavailable");
    expect(html).toContain("Need at least 5 games");
  });

  it("renders auditable evidence including home/away and selected-opponent marker", () => {
    const html = renderResults(opponentResearchFixture);
    expect(html).toContain("Solid Rivals");
    expect(html).toContain("Home");
    expect(html).toContain("Selected");
    expect(html).toContain('href="/matches/55"');
  });

  it("shows a no-evidence state when evidence is empty", () => {
    const html = renderResults({ ...opponentResearchFixture, evidence: [] });
    expect(html).toContain("No recorded match evidence is available for this comparison.");
  });
});

describe("explainOpponentDifference and explainOpponentCandidateDifference", () => {
  it("explains an unavailable comparison honestly", () => {
    expect(explainOpponentDifference({ ...opponentResearchFixture, raw_difference: null })).toContain("unavailable");
    expect(explainOpponentCandidateDifference("Player One", "disposals", makeCandidate({ raw_difference: null }))).toContain("unavailable");
  });

  it("states direction without claiming causation or predicting the next match", () => {
    const text = explainOpponentDifference({ ...opponentResearchFixture, raw_difference: -3 });
    expect(text).toContain("3.0 fewer disposals");
    expect(text).toContain("not a prediction for the next match");
    // "matchup advantage" appears only inside the explicit denial above.
    expect(text).toContain("not a prediction for the next match or a matchup advantage");

    const candidateText = explainOpponentCandidateDifference("Player One", "disposals", makeCandidate({ opponent_team_name: "Rival FC", raw_difference: 4 }));
    expect(candidateText).toContain("4.0 more disposals");
    expect(candidateText).toContain("not a prediction for the next match");
  });
});
