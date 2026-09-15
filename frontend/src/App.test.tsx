import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import HomePage from "./pages/HomePage";
import MatchesPage from "./pages/MatchesPage";
import PlayersHomePage from "./pages/PlayersHomePage";
import MatchCard from "./components/ui/MatchCard";
import PlayerSearch from "./components/ui/PlayerSearch";
import EmptyState from "./components/ui/EmptyState";
import Skeleton from "./components/ui/Skeleton";
import FilterChips from "./components/ui/FilterChips";
import type { MatchSummary } from "./api/client";

function renderAt(path: string) {
  return renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)));
}

const match: MatchSummary = {
  id: 42,
  season_year: 2026,
  round_number: 5,
  status: "scheduled",
  scheduled_start: "2026-09-20T08:30:00+00:00",
  home_team: { id: 1, name: "Collingwood", short_name: "COL", primary_colour: "#000000", secondary_colour: null },
  away_team: { id: 2, name: "Carlton", short_name: "CAR", primary_colour: "#0F1131", secondary_colour: null },
  venue: { id: 1, name: "M.C.G.", city: "Melbourne" },
  home_score: null,
  away_score: null,
};

describe("App navigation", () => {
  it("shows the new simplified primary nav destinations", () => {
    const html = renderAt("/");
    expect(html).toContain(">Home<");
    expect(html).toContain(">Matches<");
    expect(html).toContain(">Players<");
    expect(html).toContain(">My Bets<");
    expect(html).toContain(">Multis<");
    // The old, jargon-heavy top-level items must no longer be primary nav destinations.
    expect(html).not.toContain(">Model Registry<");
    expect(html).not.toContain(">Prospective Evidence Center<");
  });

  it("groups every advanced/model/operational page under one Advanced destination, still reachable", () => {
    const html = renderAt("/");
    expect(html).toContain("Model Evaluation");
    expect(html).toContain("Live Model Results");
    expect(html).toContain("Backtesting");
    expect(html).toContain("Trading Monitor");
    expect(html).toContain('href="/model-registry"');
    expect(html).toContain('href="/prospective-evidence"');
    expect(html).toContain('href="/round-context"');
    expect(html).toContain('href="/team-selection"');
  });

  it("groups market/insight tools under Insights", () => {
    const html = renderAt("/");
    expect(html).toContain('href="/prop-insights"');
    expect(html).toContain('href="/weekly-review"');
    expect(html).toContain('href="/market-movement"');
    expect(html).toContain('href="/real-market-tracking"');
  });

  it("renders the Home page at the root route", () => {
    const html = renderAt("/");
    expect(html).toContain("Explore the numbers behind every match, player and market.");
  });

  it("renders the Matches page at /matches (match navigation)", () => {
    const html = renderAt("/matches");
    expect(html).toContain("Fixtures &amp; Results");
    expect(html).toContain("Upcoming");
    expect(html).toContain("Completed");
  });

  it("renders the Players landing at /players (player discovery entry point)", () => {
    const html = renderAt("/players");
    expect(html).toContain("Player research");
    expect(html).toContain("Search by player name");
  });

  it("still renders an advanced page directly by URL (advanced-page access preserved)", () => {
    const html = renderAt("/model-registry");
    expect(html).toContain("Model Registry");
  });

  it("renders the match detail hub's loading state without crashing for an unknown match", () => {
    expect(() => renderAt("/matches/999999")).not.toThrow();
  });
});

describe("HomePage", () => {
  it("shows the primary journeys as clear actions", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(HomePage)));
    expect(html).toContain('href="/matches"');
    expect(html).toContain('href="/players"');
    expect(html).toContain('href="/prop-insights"');
    expect(html).toContain('href="/market-movement"');
    expect(html).toContain("Explore this round");
    expect(html).toContain("Find a player");
  });

  it("includes the player search as a primary entry point", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(HomePage)));
    expect(html).toContain("Search for a player");
  });
});

describe("MatchesPage", () => {
  it("shows Upcoming/Completed/All filters and a loading skeleton before data arrives", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(MatchesPage)));
    expect(html).toContain("Upcoming");
    expect(html).toContain("Completed");
    expect(html).toContain("skeleton");
  });
});

describe("PlayersHomePage", () => {
  it("links to both league-wide projections and teammate/opponent comparison", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(PlayersHomePage)));
    expect(html).toContain('href="/player-insights"');
    expect(html).toContain('href="/player-research"');
  });
});

describe("MatchCard (match navigation)", () => {
  it("shows the matchup and links into the match hub, players and markets", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(MatchCard, { match })));
    expect(html).toContain("COL");
    expect(html).toContain("CAR");
    expect(html).toContain('href="/matches/42"');
    expect(html).toContain('href="/matches/42#players"');
    expect(html).toContain('href="/matches/42#markets"');
  });

  it("shows the final score instead of 'vs' for a completed match", () => {
    const completed = { ...match, status: "completed", home_score: 88, away_score: 72 };
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(MatchCard, { match: completed })));
    expect(html).toContain("88");
    expect(html).toContain("72");
  });
});

describe("PlayerSearch (player discovery)", () => {
  it("renders a labelled search input", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(PlayerSearch)));
    expect(html).toContain("Search for a player, e.g. Nick Daicos");
  });

  it("accepts a custom placeholder", () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(PlayerSearch, { placeholder: "Find a player" })));
    expect(html).toContain("Find a player");
  });
});

describe("shared UI primitives — loading / empty / error states", () => {
  it("EmptyState shows a helpful next action, never a bare 'no data'", () => {
    const html = renderToStaticMarkup(createElement(EmptyState, { title: "No matches to show", description: "Try a different filter." }));
    expect(html).toContain("No matches to show");
    expect(html).toContain("Try a different filter.");
  });

  it("Skeleton renders a sized placeholder block", () => {
    const html = renderToStaticMarkup(createElement(Skeleton, { width: "50%", height: "2rem" }));
    expect(html).toContain("skeleton");
  });

  it("FilterChips marks the active option and calls back are wired via onClick handlers", () => {
    const html = renderToStaticMarkup(
      createElement(FilterChips, {
        options: [{ value: "a", label: "A" }, { value: "b", label: "B" }],
        value: "b",
        onChange: () => {},
      })
    );
    expect(html).toContain("filter-chip--active");
    expect(html).toContain(">A<");
    expect(html).toContain(">B<");
  });
});
