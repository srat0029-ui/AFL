import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { fetchPlayerContext, fetchTeammateDiscovery } from "../api/client";
import ContextWindowControl from "../components/ContextWindowControl";
import TeammateDiscoveryPanel from "../components/TeammateDiscoveryPanel";
import { ContextResults } from "../pages/PlayerResearchPage";
import {
  CONTEXT_WINDOW_OPTIONS, DEFAULT_CONTEXT_WINDOW, togetherApartLabel, windowActionLabel,
  type ContextWindowInfo, type PlayerContextResearch, type TeammateCandidate, type TeammateDiscoveryResult,
} from "./playerContext";

const split = { games: 0, stat_sample_size: 0, mean: null, median: null, milestone_rates: { "25+": null }, average_time_on_ground_pct: null, time_on_ground_sample_size: 0 };
const seasonWindow: ContextWindowInfo = { key: "current_season", label: "Current season", scope_label: "2026 season", anchor_season_year: 2026, included_seasons: [2026], earliest_date: null, latest_date: null, games_considered: 21, games_excluded_missing_season: 0 };

function research(overrides: Partial<PlayerContextResearch> = {}): PlayerContextResearch {
  return {
    player_id: 1, player_name: "Nick Daicos", teammate_id: 2, teammate_name: "Josh Daicos", team_id: 5, team_name: "Collingwood", stat: "disposals", thresholds: [25],
    with_teammate: { ...split, games: 18, stat_sample_size: 18, mean: 26 }, without_teammate: { ...split, games: 3, stat_sample_size: 3, mean: 30 }, raw_difference: 4,
    adjusted_effect: { available: false, value: null, games_with_baseline_teammate_in: 0, games_with_baseline_teammate_out: 0, method: "recent_form_residual", explanation: "Not enough baseline-eligible games." },
    confounders: {}, confidence: { tier: "lower_confidence", warnings: [] }, evidence: [], role_analysis_available: false, role_analysis_explanation: "n/a",
    window: seasonWindow, season_breakdown: [], window_summaries: [], sufficiency: { sufficient: true, message: null, suggested_windows: [] },
    teammate_tenure: { first_game_at_club: null, apart_games_not_at_club: 0, apart_games: 0, note: null },
    tag_watch: { status: "insufficient_verified_data", verified_annotation_count: 0, games_played: null, tag_rate: null, explanation: "n/a" },
    ...overrides,
  };
}
const render = (data: PlayerContextResearch, onChangeWindow?: () => void) =>
  renderToStaticMarkup(createElement(MemoryRouter, null, createElement(ContextResults, { research: data, onChangeWindow })));

afterEach(() => vi.unstubAllGlobals());

describe("window API parameters", () => {
  it("defaults to the current season and can request the others", async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}", { status: 200 })));
    vi.stubGlobal("fetch", fetch);
    await fetchPlayerContext(1, 2);
    await fetchPlayerContext(1, 2, "disposals", undefined, "last_2_seasons");
    await fetchTeammateDiscovery(1, "goals", undefined, "current_club_career");
    await fetchTeammateDiscovery(1);
    const urls = fetch.mock.calls.map(c => String(c[0]));
    expect(urls[0]).toContain("window=current_season");
    expect(urls[1]).toContain("window=last_2_seasons");
    expect(urls[2]).toContain("window=current_club_career");
    expect(urls[3]).toContain("window=current_season");
    expect(DEFAULT_CONTEXT_WINDOW).toBe("current_season");
  });
});

describe("scope control", () => {
  it("shows the three scopes with the active one marked and explains what changing it does", () => {
    const html = renderToStaticMarkup(createElement(ContextWindowControl, { value: "last_2_seasons", onChange: () => {} }));
    for (const o of CONTEXT_WINDOW_OPTIONS) expect(html).toContain(o.short);
    expect(html).toMatch(/aria-pressed="true"[^>]*>Last 2 seasons/);
    expect(html).toMatch(/aria-pressed="false"[^>]*>Season/);
    expect(html).toContain("not the player, statistic or teammate");
  });
});

describe("headline scope", () => {
  it("names the window and the together/apart sample beside the headline", () => {
    const html = render(research());
    expect(html).toContain("Nick Daicos with/without Josh Daicos");
    expect(html).toContain("2026 season");
    expect(html).toContain("18 games together");
    expect(html).toContain("3 apart");
    expect(html).toContain("(2026 season)");
  });
  it("labels a club-career view as such", () => {
    const html = render(research({ window: { ...seasonWindow, key: "current_club_career", label: "Current club career", scope_label: "Current club career · 2022–2026", included_seasons: [2022, 2023, 2024, 2025, 2026] } }));
    expect(html).toContain("Current club career · 2022–2026");
  });
});

describe("insufficient sample is explained, never silently broadened", () => {
  const insufficient = research({
    with_teammate: { ...split, games: 19, stat_sample_size: 19, mean: 26 }, without_teammate: { ...split, games: 2, stat_sample_size: 2, mean: 30 },
    confidence: { tier: "insufficient_history", warnings: [] },
    sufficiency: { sufficient: false, message: "Only 2 games without Josh Daicos this season (2026). Current season comparison is insufficient.", suggested_windows: ["last_2_seasons", "current_club_career"] },
    window_summaries: [
      { key: "current_season", label: "Current season", scope_label: "2026 season", games_with: 19, games_without: 2, confidence_tier: "insufficient_history", sufficient: false },
      { key: "last_2_seasons", label: "Last 2 seasons", scope_label: "Last 2 seasons · 2025–2026", games_with: 30, games_without: 9, confidence_tier: "moderate_confidence", sufficient: true },
      { key: "current_club_career", label: "Current club career", scope_label: "Current club career · 2022–2026", games_with: 60, games_without: 20, confidence_tier: "higher_confidence", sufficient: true },
    ],
  });
  it("shows the honest message and obvious actions for broader scopes", () => {
    const html = render(insufficient, () => {});
    expect(html).toContain("Only 2 games without Josh Daicos this season (2026)");
    expect(html).toContain("Current season comparison is insufficient");
    expect(html).toContain("View last 2 seasons (30 together / 9 apart)");
    expect(html).toContain("View current-club career (60 together / 20 apart)");
    expect(html).toContain("Nothing has been widened automatically");
    expect(html).toContain("19 games together");   // still the current-season sample
  });
  it("shows no notice when the sample is sufficient", () => {
    const html = render(research());
    expect(html).not.toContain("comparison is insufficient");
    expect(html).not.toContain("View last 2 seasons");
  });
});

describe("teammate arrival note", () => {
  it("tells the reader when 'apart' games predate the teammate joining the club", () => {
    const note = "17 of the 26 games apart came when Nic Newman's most recent recorded game was for another club (or before their first recorded game). Those games are counted as 'apart' but may not be true absences from Carlton.";
    const html = render(research({ teammate_tenure: { first_game_at_club: "2026-03-05T00:00:00Z", apart_games_not_at_club: 17, apart_games: 26, note } }));
    expect(html).toContain("may not be true absences");
    expect(render(research())).not.toContain("may not be true absences");
  });
});

describe("season breakdown", () => {
  it("renders a descriptive per-season table only when supplied", () => {
    const withRows = render(research({ season_breakdown: [
      { season_year: 2026, with_teammate: { games: 6, mean: 25.8 }, without_teammate: { games: 2, mean: 29.1 } },
      { season_year: 2025, with_teammate: { games: 4, mean: 23.4 }, without_teammate: { games: 0, mean: null } },
    ] }));
    expect(withRows).toContain("Season by season");
    expect(withRows).toContain("25.8 · 6 games");
    expect(withRows).toContain("29.1 · 2 games");
    expect(withRows).toContain("no games");
    expect(withRows).not.toMatch(/consistency score/i);
    expect(render(research())).not.toContain("Season by season");
  });
});

describe("candidate cards and discovery scope", () => {
  const candidate = (overrides: Partial<TeammateCandidate> = {}): TeammateCandidate => ({
    teammate_id: 9, teammate_name: "Josh Daicos", with_teammate: { ...split, games: 12, stat_sample_size: 12, mean: 25 }, without_teammate: { ...split, games: 4, stat_sample_size: 4, mean: 27 },
    raw_difference: 2, adjusted_effect: { available: false, value: null, games_with_baseline_teammate_in: 0, games_with_baseline_teammate_out: 0, method: "x", explanation: "x" },
    confidence: { tier: "lower_confidence", warnings: [] }, sufficient_evidence: true, ...overrides,
  });
  const discovery = (overrides: Partial<TeammateDiscoveryResult> = {}): TeammateDiscoveryResult => ({
    player_id: 1, player_name: "Nick Daicos", team_id: 5, team_name: "Collingwood", stat: "disposals", thresholds: [25], explanation: "1 teammate(s) found.",
    window: seasonWindow, window_options: [], candidates: [candidate()], ...overrides,
  });
  const panel = (d: TeammateDiscoveryResult) => renderToStaticMarkup(createElement(TeammateDiscoveryPanel, {
    playerName: "Nick Daicos", stat: "disposals", loading: false, error: null, discovery: d, onRetry: () => {}, onSelect: () => {}, onChangeWindow: () => {},
  }));

  it("shows the sample behind each candidate in the active scope", () => {
    const html = panel(discovery());
    expect(html).toContain("12 together / 4 apart this season");
    expect(html).toContain("2026 season");
  });
  it("words the sample for the other scopes", () => {
    expect(togetherApartLabel(30, 9, "last_2_seasons")).toBe("30 together / 9 apart over the last 2 seasons");
    expect(togetherApartLabel(60, 20, "current_club_career")).toBe("60 together / 20 apart in this club career");
    expect(windowActionLabel("last_2_seasons")).toBe("View last 2 seasons");
  });
  it("when nobody is usable this scope, points at broader scopes that do have usable candidates - without switching", () => {
    const html = panel(discovery({
      candidates: [candidate({ sufficient_evidence: false, confidence: { tier: "insufficient_history", warnings: ["Fewer than 3 games in the smaller group."] }, without_teammate: { ...split, games: 1, stat_sample_size: 1, mean: 30 } })],
      window_options: [
        { key: "current_season", label: "Current season", scope_label: "2026 season", games_considered: 21, n_sufficient_candidates: 0 },
        { key: "last_2_seasons", label: "Last 2 seasons", scope_label: "Last 2 seasons · 2025–2026", games_considered: 40, n_sufficient_candidates: 7 },
        { key: "current_club_career", label: "Current club career", scope_label: "Current club career · 2022–2026", games_considered: 90, n_sufficient_candidates: 12 },
      ],
    }));
    expect(html).toContain("No teammate has enough games both together and apart in this scope");
    expect(html).toContain("View last 2 seasons (7 worth investigating)");
    expect(html).toContain("View current-club career (12 worth investigating)");
    expect(html).toContain("12 together / 1 apart this season");
  });
});
