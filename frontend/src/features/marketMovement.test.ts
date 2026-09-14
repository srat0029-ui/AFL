import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { fetchMarketMovementMatches, fetchMarketMovementOptions, fetchMarketMovementSeries, type MarketMovementSeries, type MatchMarketOptions } from "../api/client";
import MarketMovementChart from "../components/MarketMovementChart";
import MarketMovementSummaryCard from "../components/MarketMovementSummaryCard";
import MarketMovementExplorerView, { type MarketMovementExplorerViewProps } from "../components/MarketMovementExplorerView";
import { buildOptions, niceHourTicks, playerOptionLabel, teamOptionLabel } from "./marketMovement";

afterEach(() => vi.unstubAllGlobals());

const matchContext = { match_id: 1, home_team: "Collingwood", away_team: "Carlton", scheduled_start: "2026-08-20T09:00:00+00:00", status: "scheduled" };

describe("client fetchers", () => {
  it("fetchMarketMovementMatches requests the discovery endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await fetchMarketMovementMatches();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/market-movement/matches?limit=200"), expect.anything());
  });

  it("fetchMarketMovementOptions requests markets for the given match", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ match: matchContext, team_markets: [], player_markets: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await fetchMarketMovementOptions(42);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/market-movement/matches/42/markets"), expect.anything());
  });

  it("fetchMarketMovementSeries builds team-identity query params", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await fetchMarketMovementSeries({ identityType: "team", matchId: 1, marketType: "h2h", selection: "Collingwood" });
    const url = fetch.mock.calls[0][0] as string;
    expect(url).toContain("identity_type=team");
    expect(url).toContain("selection=Collingwood");
    expect(url).not.toContain("player_id");
  });

  it("fetchMarketMovementSeries builds player-identity query params", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await fetchMarketMovementSeries({ identityType: "player", matchId: 1, marketType: "player_disposals", playerId: 7, lineType: "over_under", threshold: 23.5 });
    const url = fetch.mock.calls[0][0] as string;
    expect(url).toContain("identity_type=player");
    expect(url).toContain("player_id=7");
    expect(url).toContain("line_type=over_under");
    expect(url).toContain("threshold=23.5");
  });

  it("surfaces HTTP errors without a mock fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "match not found" }), { status: 404 })));
    await expect(fetchMarketMovementOptions(999999)).rejects.toMatchObject({ status: 404, message: "match not found" });
  });
});

describe("teamOptionLabel / playerOptionLabel", () => {
  it("labels h2h without a line value", () => {
    expect(teamOptionLabel({ market_type: "h2h", selection: "Collingwood", line_value: null, n_quotes: 1, bookmakers: [], first_observed_at: "", latest_observed_at: "", has_model_series: true })).toBe("Collingwood — Head to head");
  });

  it("labels total with the line value", () => {
    expect(teamOptionLabel({ market_type: "total", selection: "over", line_value: 150.5, n_quotes: 1, bookmakers: [], first_observed_at: "", latest_observed_at: "", has_model_series: false })).toBe("over 150.5 — Total");
  });

  it("labels a multi_plus player market with a plus sign", () => {
    expect(playerOptionLabel({ player_id: 1, player_name: "Nick Daicos", market_type: "player_goals", line_type: "multi_plus", threshold: 2.5, n_quotes: 1, bookmakers: [], first_observed_at: "", latest_observed_at: "", has_model_series: false })).toBe("Nick Daicos — Goals 2.5+");
  });

  it("labels an over_under player market without a plus sign", () => {
    expect(playerOptionLabel({ player_id: 1, player_name: "Nick Daicos", market_type: "player_disposals", line_type: "over_under", threshold: 23.5, n_quotes: 1, bookmakers: [], first_observed_at: "", latest_observed_at: "", has_model_series: true })).toBe("Nick Daicos — Disposals 23.5");
  });
});

describe("buildOptions", () => {
  const options: MatchMarketOptions = {
    match: matchContext,
    team_markets: [{ market_type: "h2h", selection: "Collingwood", line_value: null, n_quotes: 5, bookmakers: ["TAB"], first_observed_at: "", latest_observed_at: "", has_model_series: true }],
    player_markets: [{ player_id: 7, player_name: "Nick Daicos", market_type: "player_disposals", line_type: "over_under", threshold: 23.5, n_quotes: 3, bookmakers: ["TAB"], first_observed_at: "", latest_observed_at: "", has_model_series: false }],
  };

  it("produces one choice per team and player market, correctly grouped", () => {
    const choices = buildOptions(options, 1);
    expect(choices).toHaveLength(2);
    expect(choices.find((c) => c.group === "Team markets")?.identity).toMatchObject({ identityType: "team", matchId: 1, marketType: "h2h", selection: "Collingwood" });
    expect(choices.find((c) => c.group === "Player markets")?.identity).toMatchObject({ identityType: "player", matchId: 1, playerId: 7, lineType: "over_under", threshold: 23.5 });
  });
});

describe("niceHourTicks", () => {
  it("never produces a duplicate tick value (regression: kickoff tick was appended twice)", () => {
    for (const maxHours of [1, 5, 6, 11, 24, 46, 48, 72, 168]) {
      const ticks = niceHourTicks(maxHours);
      expect(new Set(ticks).size).toBe(ticks.length);
      expect(ticks[0]).toBe(0);
    }
  });
});

describe("MarketMovementChart", () => {
  it("shows an empty message with no observations", () => {
    expect(renderToStaticMarkup(createElement(MarketMovementChart, { bookmakerQuotes: [], consensusSeries: [], modelObservations: [], lineupStatusChanges: [] }))).toContain("No pre-kickoff observations");
  });

  it("draws no connecting line for a single-point (sparse) series", () => {
    const html = renderToStaticMarkup(createElement(MarketMovementChart, {
      bookmakerQuotes: [],
      consensusSeries: [{ as_of: "2026-08-19T00:00:00Z", hours_to_kickoff: 33, consensus_probability: 0.55, n_bookmakers: 1, n_devigged: 0, spread: 0 }],
      modelObservations: [],
      lineupStatusChanges: [],
    }));
    expect(html).not.toContain("mme-chart__consensus-line");
    expect(html).toContain("mme-chart__consensus-point");
  });

  it("draws a connecting line for a normal (2+ point) series", () => {
    const html = renderToStaticMarkup(createElement(MarketMovementChart, {
      bookmakerQuotes: [],
      consensusSeries: [
        { as_of: "2026-08-19T00:00:00Z", hours_to_kickoff: 33, consensus_probability: 0.50, n_bookmakers: 2, n_devigged: 2, spread: 0.01 },
        { as_of: "2026-08-20T05:00:00Z", hours_to_kickoff: 4, consensus_probability: 0.58, n_bookmakers: 2, n_devigged: 2, spread: 0.01 },
      ],
      modelObservations: [],
      lineupStatusChanges: [],
    }));
    expect(html).toContain("mme-chart__consensus-line");
  });

  it("renders a lineup-status marker when a change is present", () => {
    const html = renderToStaticMarkup(createElement(MarketMovementChart, {
      bookmakerQuotes: [{ bookmaker_name: "TAB", price_decimal: 1.9, raw_implied_probability: 0.526, recorded_at: "2026-08-19T00:00:00Z", hours_to_kickoff: 20, source: "manual", is_closing_line: false }],
      consensusSeries: [],
      modelObservations: [],
      lineupStatusChanges: [{ from_status: "uncertain", to_status: "confirmed_selected", changed_at: "2026-08-19T12:00:00Z", hours_to_kickoff: 10, value_changed_at_same_observation: true, value_before: 0.4, value_after: 0.52 }],
    }));
    expect(html).toContain("mme-chart__lineup-marker");
    expect(html).toContain("uncertain");
    expect(html).toContain("confirmed_selected");
  });
});

describe("MarketMovementSummaryCard", () => {
  it("shows an accumulating-data message with zero observations", () => {
    const html = renderToStaticMarkup(createElement(MarketMovementSummaryCard, {
      summary: { label: "Bookmaker quotes", n_observations: 0, insufficient_history: true, first_observed: null, latest_observed_pre_kickoff: null, total_probability_change: null, largest_single_movement: null },
    }));
    expect(html).toContain("No pre-kickoff observations recorded yet");
  });

  it("flags insufficient history and omits movement figures for a single observation", () => {
    const endpoint = { probability: 0.55, recorded_at: "2026-08-19T00:00:00Z", hours_to_kickoff: 10, price_decimal: 1.82, bookmaker_name: "TAB" };
    const html = renderToStaticMarkup(createElement(MarketMovementSummaryCard, {
      summary: { label: "Bookmaker quotes", n_observations: 1, insufficient_history: true, first_observed: endpoint, latest_observed_pre_kickoff: endpoint, total_probability_change: null, largest_single_movement: null },
    }));
    expect(html).toContain("Insufficient history");
    expect(html).not.toContain("Total movement");
  });

  it("shows total and largest movement for a normal multi-observation series", () => {
    const first = { probability: 0.50, recorded_at: "2026-08-18T00:00:00Z", hours_to_kickoff: 33, price_decimal: 2.0, bookmaker_name: "TAB" };
    const latest = { probability: 0.60, recorded_at: "2026-08-20T05:00:00Z", hours_to_kickoff: 4, price_decimal: 1.67, bookmaker_name: "SportsBet" };
    const html = renderToStaticMarkup(createElement(MarketMovementSummaryCard, {
      summary: {
        label: "Bookmaker consensus", n_observations: 4, insufficient_history: false, first_observed: first, latest_observed_pre_kickoff: latest,
        total_probability_change: 0.10, largest_single_movement: { from_probability: 0.50, to_probability: 0.60, absolute_change: 0.10, at: latest.recorded_at, hours_to_kickoff: 4 },
      },
    }));
    expect(html).toContain("+10.0pp");
    expect(html).not.toContain("Insufficient history");
  });
});

function baseViewProps(overrides: Partial<MarketMovementExplorerViewProps> = {}): MarketMovementExplorerViewProps {
  return {
    matches: null, matchesLoading: false, matchesError: null, onRetryMatches: () => {},
    selectedMatchId: null, onSelectMatch: () => {},
    options: null, optionsLoading: false, optionsError: null, onRetryOptions: () => {}, optionChoices: [],
    selectedKey: null, onSelectOption: () => {},
    series: null, seriesLoading: false, seriesError: null, onRetrySeries: () => {},
    availableBookmakers: [], bookmakerFilter: null, onToggleBookmaker: () => {}, filteredQuotes: [],
    ...overrides,
  };
}

describe("MarketMovementExplorerView", () => {
  it("shows a loading state for matches", () => {
    const html = renderToStaticMarkup(createElement(MarketMovementExplorerView, baseViewProps({ matchesLoading: true })));
    expect(html).toContain("Loading matches");
  });

  it("shows an empty state when no matches have history", () => {
    const html = renderToStaticMarkup(createElement(MarketMovementExplorerView, baseViewProps({ matches: [] })));
    expect(html).toContain("No matches have any recorded");
  });

  it("shows an error state with a retry control", () => {
    const html = renderToStaticMarkup(createElement(MarketMovementExplorerView, baseViewProps({ matchesError: "Network error" })));
    expect(html).toContain("Network error");
    expect(html).toContain("Retry");
  });

  it("populates the match selector from the matches list (match selection)", () => {
    const matches = [
      { match: { match_id: 1, home_team: "Collingwood", away_team: "Carlton", scheduled_start: "2026-08-20T09:00:00Z", status: "scheduled" }, n_odds_quotes: 5, n_player_prop_quotes: 0, n_model_observations: 2, earliest_observed_at: null, latest_observed_at: null },
      { match: { match_id: 2, home_team: "Richmond", away_team: "Geelong", scheduled_start: "2026-08-21T09:00:00Z", status: "scheduled" }, n_odds_quotes: 0, n_player_prop_quotes: 3, n_model_observations: 0, earliest_observed_at: null, latest_observed_at: null },
    ];
    const html = renderToStaticMarkup(createElement(MarketMovementExplorerView, baseViewProps({ matches, selectedMatchId: 2 })));
    expect(html).toContain("Collingwood vs Carlton");
    expect(html).toContain("Richmond vs Geelong");
    expect(html).toContain('value="2" selected=""');
  });

  it("populates the market selector grouped by team/player once a match is selected (market selection)", () => {
    const optionChoices = [
      { key: "team:h2h:Collingwood:null", group: "Team markets" as const, label: "Collingwood — Head to head", identity: { identityType: "team" as const, matchId: 1, marketType: "h2h", selection: "Collingwood" }, nQuotes: 5, bookmakers: ["TAB"], hasModelSeries: true },
      { key: "player:7:player_disposals:over_under:23.5", group: "Player markets" as const, label: "Nick Daicos — Disposals 23.5", identity: { identityType: "player" as const, matchId: 1, marketType: "player_disposals", playerId: 7, lineType: "over_under", threshold: 23.5 }, nQuotes: 3, bookmakers: ["TAB"], hasModelSeries: false },
    ];
    const html = renderToStaticMarkup(createElement(MarketMovementExplorerView, baseViewProps({ selectedMatchId: 1, options: { match: matchContext, team_markets: [], player_markets: [] }, optionChoices })));
    expect(html).toContain("Team markets");
    expect(html).toContain("Player markets");
    expect(html).toContain("Collingwood — Head to head");
    expect(html).toContain("Nick Daicos — Disposals 23.5");
    expect(html).toContain("no model series");
  });

  it("renders a sparse series (single observation) with insufficient-history messaging and no chart line", () => {
    const series: MarketMovementSeries = {
      match: matchContext, identity_label: "Collingwood — h2h", methodology_notes: ["note"],
      bookmaker_quotes: [{ bookmaker_name: "TAB", price_decimal: 1.9, raw_implied_probability: 0.526, recorded_at: "2026-08-19T00:00:00Z", hours_to_kickoff: 10, source: "manual", is_closing_line: false }],
      bookmaker_quotes_post_kickoff: [], consensus_series: [], model_observations: [], model_observations_post_kickoff: [], model_projected_mean_observations: [], lineup_status_changes: [],
      bookmaker_summary: { label: "Bookmaker quotes", n_observations: 1, insufficient_history: true, first_observed: { probability: 0.526, recorded_at: "2026-08-19T00:00:00Z", hours_to_kickoff: 10, price_decimal: 1.9, bookmaker_name: "TAB" }, latest_observed_pre_kickoff: { probability: 0.526, recorded_at: "2026-08-19T00:00:00Z", hours_to_kickoff: 10, price_decimal: 1.9, bookmaker_name: "TAB" }, total_probability_change: null, largest_single_movement: null },
      consensus_summary: null, model_summary: null,
    };
    const html = renderToStaticMarkup(createElement(MarketMovementExplorerView, baseViewProps({ series, filteredQuotes: series.bookmaker_quotes, availableBookmakers: ["TAB"] })));
    expect(html).toContain("Insufficient history");
    expect(html).not.toContain("mme-chart__consensus-line");
  });

  it("renders a normal multi-point timeline with bookmaker, consensus and model summaries", () => {
    const series: MarketMovementSeries = {
      match: matchContext, identity_label: "Collingwood — h2h", methodology_notes: ["note"],
      bookmaker_quotes: [
        { bookmaker_name: "TAB", price_decimal: 2.0, raw_implied_probability: 0.5, recorded_at: "2026-08-18T00:00:00Z", hours_to_kickoff: 33, source: "manual", is_closing_line: false },
        { bookmaker_name: "TAB", price_decimal: 1.7, raw_implied_probability: 0.588, recorded_at: "2026-08-20T05:00:00Z", hours_to_kickoff: 4, source: "manual", is_closing_line: false },
      ],
      bookmaker_quotes_post_kickoff: [
        { bookmaker_name: "TAB", price_decimal: 1.6, raw_implied_probability: 0.625, recorded_at: "2026-08-20T09:05:00Z", hours_to_kickoff: -0.08, source: "manual", is_closing_line: false },
      ],
      consensus_series: [
        { as_of: "2026-08-18T00:00:00Z", hours_to_kickoff: 33, consensus_probability: 0.5, n_bookmakers: 1, n_devigged: 0, spread: 0 },
        { as_of: "2026-08-20T05:00:00Z", hours_to_kickoff: 4, consensus_probability: 0.588, n_bookmakers: 1, n_devigged: 0, spread: 0 },
      ],
      model_observations: [
        { value_type: "team_win_probability", value_kind: "probability", value: 0.52, model_name: "team_elo", model_version: "v1", recorded_at: "2026-08-18T06:00:00Z", hours_to_kickoff: 27, lineup_status: null },
        { value_type: "team_win_probability", value_kind: "probability", value: 0.57, model_name: "team_elo", model_version: "v1", recorded_at: "2026-08-20T02:00:00Z", hours_to_kickoff: 7, lineup_status: null },
      ],
      model_observations_post_kickoff: [], model_projected_mean_observations: [], lineup_status_changes: [],
      bookmaker_summary: { label: "Bookmaker quotes", n_observations: 2, insufficient_history: false, first_observed: { probability: 0.5, recorded_at: "2026-08-18T00:00:00Z", hours_to_kickoff: 33, price_decimal: 2.0, bookmaker_name: "TAB" }, latest_observed_pre_kickoff: { probability: 0.588, recorded_at: "2026-08-20T05:00:00Z", hours_to_kickoff: 4, price_decimal: 1.7, bookmaker_name: "TAB" }, total_probability_change: 0.088, largest_single_movement: { from_probability: 0.5, to_probability: 0.588, absolute_change: 0.088, at: "2026-08-20T05:00:00Z", hours_to_kickoff: 4 } },
      consensus_summary: { label: "Bookmaker consensus", n_observations: 2, insufficient_history: false, first_observed: { probability: 0.5, recorded_at: "2026-08-18T00:00:00Z", hours_to_kickoff: 33, price_decimal: null, bookmaker_name: null }, latest_observed_pre_kickoff: { probability: 0.588, recorded_at: "2026-08-20T05:00:00Z", hours_to_kickoff: 4, price_decimal: null, bookmaker_name: null }, total_probability_change: 0.088, largest_single_movement: null },
      model_summary: { label: "Model probability", n_observations: 2, insufficient_history: false, first_observed: { probability: 0.52, recorded_at: "2026-08-18T06:00:00Z", hours_to_kickoff: 27, price_decimal: null, bookmaker_name: null }, latest_observed_pre_kickoff: { probability: 0.57, recorded_at: "2026-08-20T02:00:00Z", hours_to_kickoff: 7, price_decimal: null, bookmaker_name: null }, total_probability_change: 0.05, largest_single_movement: null },
    };
    const html = renderToStaticMarkup(createElement(MarketMovementExplorerView, baseViewProps({ series, filteredQuotes: series.bookmaker_quotes, availableBookmakers: ["TAB"] })));
    expect(html).toContain("mme-chart__consensus-line");
    expect(html).toContain("mme-chart__model-line");
    expect(html).toContain("Evidence — bookmaker quotes (2)");
    expect(html).toContain("At/after-kickoff observations");
  });
});
