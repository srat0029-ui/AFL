import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { fetchTeammateDiscovery } from "../api/client";
import TeammateDiscoveryPanel from "../components/TeammateDiscoveryPanel";
import { explainCandidateDifference, type TeammateCandidate, type TeammateDiscoveryResult } from "./playerContext";

const split = { games: 0, stat_sample_size: 0, mean: null, median: null, milestone_rates: { "15+": null }, average_time_on_ground_pct: null, time_on_ground_sample_size: 0 };

function makeCandidate(overrides: Partial<TeammateCandidate>): TeammateCandidate {
  return {
    teammate_id: 1,
    teammate_name: "Teammate One",
    with_teammate: split,
    without_teammate: split,
    raw_difference: null,
    adjusted_effect: { available: false, value: null, games_with_baseline_teammate_in: 0, games_with_baseline_teammate_out: 0, method: "recent_form_residual", explanation: "Not enough baseline-eligible games." },
    confidence: { tier: "insufficient_history", warnings: ["Fewer than 3 games in the smaller group."] },
    sufficient_evidence: false,
    ...overrides,
  };
}

const seasonWindow = { key: "current_season" as const, label: "Current season", scope_label: "2026 season", anchor_season_year: 2026, included_seasons: [2026], earliest_date: null, latest_date: null, games_considered: 22, games_excluded_missing_season: 0 };

const discoveryFixture: TeammateDiscoveryResult = {
  player_id: 101, player_name: "Player One", team_id: 5, team_name: "Example Club", stat: "disposals", thresholds: [15], explanation: "2 teammate(s) found.", window: seasonWindow, window_options: [],
  candidates: [
    makeCandidate({
      teammate_id: 201, teammate_name: "Solid Evidence",
      with_teammate: { ...split, games: 12, stat_sample_size: 12, mean: 22 },
      without_teammate: { ...split, games: 10, stat_sample_size: 10, mean: 20 },
      raw_difference: -2,
      adjusted_effect: { available: true, value: -1.5, games_with_baseline_teammate_in: 10, games_with_baseline_teammate_out: 8, method: "recent_form_residual", explanation: "Computed from trailing form residuals." },
      confidence: { tier: "moderate_confidence", warnings: [] },
      sufficient_evidence: true,
    }),
    makeCandidate({
      teammate_id: 202, teammate_name: "Barely Seen",
      with_teammate: { ...split, games: 1, stat_sample_size: 1, mean: 25 },
      without_teammate: { ...split, games: 20, stat_sample_size: 20, mean: 20 },
      raw_difference: -5,
      confidence: { tier: "insufficient_history", warnings: ["Fewer than 3 games in the smaller group - treat this split as anecdotal, not a reliable estimate."] },
      sufficient_evidence: false,
    }),
  ],
};

const renderPanel = (props: Partial<Parameters<typeof TeammateDiscoveryPanel>[0]> = {}) =>
  renderToStaticMarkup(createElement(TeammateDiscoveryPanel, {
    playerName: "Player One", stat: "disposals", loading: false, error: null, discovery: null,
    onRetry: () => {}, onSelect: () => {}, ...props,
  }));

afterEach(() => vi.unstubAllGlobals());

describe("fetchTeammateDiscovery", () => {
  it("requests the candidates endpoint for the selected player and statistic", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(discoveryFixture), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await fetchTeammateDiscovery(101, "goals")).toEqual(discoveryFixture);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/afl/players/101/context-candidates?stat=goals"), expect.anything());
  });

  it("surfaces HTTP errors without a mock fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Player not found" }), { status: 404 })));
    await expect(fetchTeammateDiscovery(999999)).rejects.toMatchObject({ status: 404, message: "Player not found" });
  });
});

describe("TeammateDiscoveryPanel", () => {
  it("shows a loading state", () => {
    expect(renderPanel({ loading: true })).toContain("Finding teammates worth investigating");
  });

  it("shows an error state with retry", () => {
    const html = renderPanel({ error: "Network error" });
    expect(html).toContain("Network error");
    expect(html).toContain("Retry");
  });

  it("shows a no-teammates-found state distinct from a loading or error state", () => {
    const html = renderPanel({ discovery: { ...discoveryFixture, candidates: [], explanation: "No other player has a recorded match statistic for Example Club." } });
    expect(html).toContain("No teammates found");
    expect(html).toContain("No other player has a recorded match statistic");
  });

  it("groups candidates into sufficient and insufficient evidence sections", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).toContain("Worth investigating (1)");
    expect(html).toContain("Insufficient evidence (1)");
    expect(html).toContain("Solid Evidence");
    expect(html).toContain("Barely Seen");
    expect(html).toContain("Compare with Solid Evidence");
    expect(html).toContain("Compare with Barely Seen");
  });

  it("shows shared-match and comparison sample sizes for every candidate", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).toContain("Shared matches");
    // Solid Evidence: 12 shared, 10 without
    expect(html).toMatch(/Shared matches<\/dt><dd>12<\/dd>/);
    expect(html).toMatch(/Games without<\/dt><dd>10<\/dd>/);
  });

  it("shows the adjusted difference only when available, and an honest 'Unavailable' otherwise", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).toContain("-1.5");
    // Barely Seen's adjusted effect is unavailable - must say so, never a fabricated number.
    const barelySeenSection = html.split("Barely Seen")[1] ?? "";
    expect(barelySeenSection).toContain("Unavailable");
  });

  it("never presents the difference as causal", () => {
    const html = renderPanel({ discovery: discoveryFixture });
    expect(html).toContain("not proof of a cause");
  });

  it("renders nothing but is not an error when there is no discovery data yet and nothing is loading", () => {
    expect(renderPanel({ discovery: null })).toBe("");
  });
});

describe("explainCandidateDifference", () => {
  it("explains an unavailable comparison honestly", () => {
    const text = explainCandidateDifference("Player One", "disposals", makeCandidate({ raw_difference: null }));
    expect(text).toContain("unavailable");
  });

  it("states direction without claiming causation", () => {
    const text = explainCandidateDifference("Player One", "disposals", makeCandidate({ teammate_name: "Teammate X", raw_difference: -3 }));
    expect(text).toContain("3.0 fewer disposals");
    expect(text).toContain("not proof of a cause");
  });
});
