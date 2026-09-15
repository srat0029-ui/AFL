import { useEffect, useMemo, useState } from "react";
import Disclaimer from "../components/Disclaimer";
import MarketMovementExplorerView from "../components/MarketMovementExplorerView";
import { buildOptions } from "../features/marketMovement";
import {
  fetchMarketMovementMatches,
  fetchMarketMovementOptions,
  fetchMarketMovementSeries,
  type MarketMovementMatchWithHistory,
  type MarketMovementSeries,
  type MatchMarketOptions,
} from "../api/client";
import "./MarketMovementExplorerPage.css";

function MarketMovementExplorerPage() {
  const [matches, setMatches] = useState<MarketMovementMatchWithHistory[] | null>(null);
  const [matchesError, setMatchesError] = useState<string | null>(null);
  const [matchesLoading, setMatchesLoading] = useState(true);

  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null);
  const [options, setOptions] = useState<MatchMarketOptions | null>(null);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [optionsError, setOptionsError] = useState<string | null>(null);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [series, setSeries] = useState<MarketMovementSeries | null>(null);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [seriesError, setSeriesError] = useState<string | null>(null);

  const [bookmakerFilter, setBookmakerFilter] = useState<Set<string> | null>(null);

  function loadMatches() {
    setMatchesLoading(true);
    setMatchesError(null);
    fetchMarketMovementMatches()
      .then(setMatches)
      .catch((err) => setMatchesError(err instanceof Error ? err.message : "Failed to load matches"))
      .finally(() => setMatchesLoading(false));
  }

  useEffect(loadMatches, []);

  function loadOptions(matchId: number) {
    setOptionsLoading(true);
    setOptionsError(null);
    fetchMarketMovementOptions(matchId)
      .then(setOptions)
      .catch((err) => setOptionsError(err instanceof Error ? err.message : "Failed to load markets for this match"))
      .finally(() => setOptionsLoading(false));
  }

  function selectMatch(matchId: number | null) {
    setSelectedMatchId(matchId);
    setOptions(null);
    setSelectedKey(null);
    setSeries(null);
    setSeriesError(null);
    setBookmakerFilter(null);
    if (matchId !== null) loadOptions(matchId);
  }

  const optionChoices = useMemo(() => (options && selectedMatchId !== null ? buildOptions(options, selectedMatchId) : []), [options, selectedMatchId]);

  function loadSeries(key: string) {
    const choice = optionChoices.find((c) => c.key === key);
    if (!choice) return;
    setSeriesLoading(true);
    setSeriesError(null);
    fetchMarketMovementSeries(choice.identity)
      .then(setSeries)
      .catch((err) => setSeriesError(err instanceof Error ? err.message : "Failed to load movement history"))
      .finally(() => setSeriesLoading(false));
  }

  function selectOption(key: string | null) {
    setSelectedKey(key);
    setSeries(null);
    setSeriesError(null);
    setBookmakerFilter(null);
    if (key !== null) loadSeries(key);
  }

  const availableBookmakers = useMemo(() => (series ? Array.from(new Set(series.bookmaker_quotes.map((q) => q.bookmaker_name))).sort() : []), [series]);

  const filteredQuotes = useMemo(() => {
    if (!series) return [];
    if (bookmakerFilter === null) return series.bookmaker_quotes;
    return series.bookmaker_quotes.filter((q) => bookmakerFilter.has(q.bookmaker_name));
  }, [series, bookmakerFilter]);

  function toggleBookmaker(name: string) {
    setBookmakerFilter((prev) => {
      const base = prev ?? new Set(availableBookmakers);
      const next = new Set(base);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <main className="mme-page">
      <header className="mme-page__header">
        <h1>Market Movement Explorer</h1>
        <p className="hint">
          A descriptive research tool for inspecting how bookmaker prices and the model's own probability moved
          before a specific match/market — not a betting-recommendation engine. Bookmaker-quote and model-observation
          timestamps are never aligned or fabricated onto a shared instant; every point shown is a genuine
          observation. The latest observed price before kickoff is never presented as an official closing line unless
          the underlying data genuinely says so.
        </p>
      </header>

      <MarketMovementExplorerView
        matches={matches}
        matchesLoading={matchesLoading}
        matchesError={matchesError}
        onRetryMatches={loadMatches}
        selectedMatchId={selectedMatchId}
        onSelectMatch={selectMatch}
        options={options}
        optionsLoading={optionsLoading}
        optionsError={optionsError}
        onRetryOptions={() => selectedMatchId !== null && loadOptions(selectedMatchId)}
        optionChoices={optionChoices}
        selectedKey={selectedKey}
        onSelectOption={selectOption}
        series={series}
        seriesLoading={seriesLoading}
        seriesError={seriesError}
        onRetrySeries={() => selectedKey !== null && loadSeries(selectedKey)}
        availableBookmakers={availableBookmakers}
        bookmakerFilter={bookmakerFilter}
        onToggleBookmaker={toggleBookmaker}
        filteredQuotes={filteredQuotes}
      />

      <Disclaimer />
    </main>
  );
}

export default MarketMovementExplorerPage;
