import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { fetchPlayerContext } from "../api/client";
import PlayerResearchPage, { ContextResults } from "../pages/PlayerResearchPage";
import { selectEvidence, formatValue, formatRate, type PlayerContextResearch } from "./playerContext";
const split = { games: 0, stat_sample_size: 0, mean: null, median: null, milestone_rates: { "25+": null }, average_time_on_ground_pct: null, time_on_ground_sample_size: 0 };
const fixture: PlayerContextResearch = {
  player_id: 101, player_name: "Player One", teammate_id: 202, teammate_name: "Player Two", team_id: null, team_name: null, stat: "disposals", thresholds: [25],
  with_teammate: split, without_teammate: split, raw_difference: null,
  adjusted_effect: { available: false, value: null, games_with_baseline_teammate_in: 0, games_with_baseline_teammate_out: 0, method: "recent_form_residual", explanation: "Not enough baseline-eligible games." },
  confounders: { role: { considered: false, method: null, reason: "No historical role records." } },
  confidence: { tier: "insufficient_history", warnings: ["Fewer than 3 games in the smaller group."] }, evidence: [], role_analysis_available: false, role_analysis_explanation: "Role-conditioned analysis is unavailable.",
  tag_watch: { status: "insufficient_verified_data", verified_annotation_count: 0, games_played: null, tag_rate: null, explanation: "Not enough verified tagging annotations." },
};
const render = (data: PlayerContextResearch) => renderToStaticMarkup(createElement(MemoryRouter, null, createElement(ContextResults, { research: data })));
afterEach(() => vi.unstubAllGlobals());
describe("real player context API", () => {
  it("requests selected IDs and statistic with cancellation", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const signal = new AbortController().signal;
    expect(await fetchPlayerContext(101, 202, "goals", signal)).toEqual(fixture);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/afl/players/101/context/202?stat=goals"), expect.objectContaining({ signal }));
  });
  it.each([404, 500])("surfaces HTTP %s without mock fallback", async status => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Context unavailable" }), { status })));
    await expect(fetchPlayerContext(101, 202)).rejects.toMatchObject({ status, message: "Context unavailable" });
  });
  it("propagates network errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(fetchPlayerContext(101, 202)).rejects.toThrow("Failed to fetch");
  });
});
describe("context presentation", () => {
  it("starts without invented player IDs", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(PlayerResearchPage)));
    expect(html).toContain("Select a player and a different teammate");
    expect(html).not.toContain("Harry Sheezel");
  });
  it("shows missing history and backend explanations", () => {
    const html = render(fixture);
    for (const text of ["No recorded match evidence", "Unavailable", "insufficient history", "Fewer than 3", "Adjusted difference unavailable", "Not enough baseline-eligible games.", "Role-conditioned analysis is unavailable.", "Not enough verified tagging annotations."]) expect(html).toContain(text);
    expect(html).not.toContain("0.0");
    expect(html).not.toContain("Scenario projection");
  });
  it("keeps zero distinct from missing values", () => {
    expect(formatValue(0)).toBe("0.0"); expect(formatRate(0)).toBe("0%");
    expect(formatValue(null)).toBe("Unavailable"); expect(formatRate(null)).toBe("Unavailable");
  });
  it("renders real splits, sample sizes and evidence", () => {
    const html = render({ ...fixture, with_teammate: { ...split, games: 8, stat_sample_size: 7, mean: 25, milestone_rates: { "25+": 0 } }, without_teammate: { ...split, games: 3, stat_sample_size: 3, mean: 20 }, raw_difference: -5,
      evidence: [{ match_id: 99, season_year: 2025, round_number: 2, round_name: null, scheduled_start: "2025-03-21T00:00:00Z", team_id: 1, team_name: "Example Club", opponent_team_id: null, opponent_name: null, venue_name: null, teammate_played: false, stat_value: 0, time_on_ground_pct: null }] });
    expect(html).toContain("-5.0"); expect(html).toContain("5.0 fewer disposals"); expect(html).toContain("7 games with recorded disposals"); expect(html).toContain("0%"); expect(html).toContain('href="/matches/99"'); expect(html).toContain("not proof");
  });
  it("gates unavailable numbers even when stale values exist", () => {
    const html = render({ ...fixture, adjusted_effect: { ...fixture.adjusted_effect, value: 123.4 }, tag_watch: { ...fixture.tag_watch, tag_rate: .99 } });
    expect(html).not.toContain("123.4"); expect(html).not.toContain("99%");
  });
  it("labels tag evidence as historical", () => {
    const html = render({ ...fixture, tag_watch: { ...fixture.tag_watch, status: "available", tag_rate: .2, verified_annotation_count: 5, games_played: 25 } });
    expect(html).toContain("Historical verified tag annotation rate"); expect(html).toContain("20%"); expect(html).toContain("not a likelihood of being tagged next game");
  });
});

describe("evidence exploration", () => {
  const rows: import("./playerContext").ContextEvidenceGame[] = [
    { match_id: 1, season_year: 2024, round_number: 1, round_name: null, scheduled_start: "2024-03-01T00:00:00Z", team_id: 1, team_name: "Club", opponent_team_id: 2, opponent_name: "Carlton", venue_name: null, teammate_played: true, stat_value: 0, time_on_ground_pct: null },
    { match_id: 2, season_year: 2025, round_number: 2, round_name: null, scheduled_start: "2025-03-08T00:00:00Z", team_id: 1, team_name: "Club", opponent_team_id: 2, opponent_name: "Carlton", venue_name: null, teammate_played: false, stat_value: null, time_on_ground_pct: null },
    { match_id: 3, season_year: 2025, round_number: 1, round_name: null, scheduled_start: "2025-03-01T00:00:00Z", team_id: 1, team_name: "Club", opponent_team_id: 3, opponent_name: "Melbourne", venue_name: null, teammate_played: false, stat_value: 30, time_on_ground_pct: null },
  ];
  it("combines season, teammate and case-insensitive opponent filters", () => {
    expect(selectEvidence(rows, { teammate: "out", season: 2025, opponent: " CARL ", order: "newest" }).map(row => row.match_id)).toEqual([2]);
    expect(selectEvidence(rows, { teammate: "in", season: 2025, opponent: "", order: "newest" })).toEqual([]);
  });
  it("sorts chronologically without mutating source evidence", () => {
    expect(selectEvidence(rows, { teammate: "all", season: null, opponent: "", order: "newest" }).map(row => row.match_id)).toEqual([2, 3, 1]);
    expect(selectEvidence(rows, { teammate: "all", season: null, opponent: "", order: "oldest" }).map(row => row.match_id)).toEqual([1, 3, 2]);
    expect(rows.map(row => row.match_id)).toEqual([1, 2, 3]);
  });
  it("places missing stats last for either numeric sort while preserving real zero", () => {
    expect(selectEvidence(rows, { teammate: "all", season: null, opponent: "", order: "highest" }).map(row => row.match_id)).toEqual([3, 1, 2]);
    expect(selectEvidence(rows, { teammate: "all", season: null, opponent: "", order: "lowest" }).map(row => row.match_id)).toEqual([1, 3, 2]);
  });
  it("limits the initial table to twenty games with navigation and clear filter scope", () => {
    const evidence = Array.from({ length: 25 }, (_, i) => ({ ...rows[0], match_id: i + 1 }));
    const html = render({ ...fixture, evidence });
    expect(html).toContain("Showing 1–20 of 25 matching games");
    expect(html).toContain("Page 1 of 2");
    expect(html).toContain("comparison above uses the full API history");
    expect(html.match(/href="\/matches\//g)).toHaveLength(20);
  });
});
