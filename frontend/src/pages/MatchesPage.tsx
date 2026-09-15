import { useEffect, useMemo, useState } from "react";
import "./MatchesPage.css";
import PageHeader from "../components/ui/PageHeader";
import EmptyState from "../components/ui/EmptyState";
import Skeleton from "../components/ui/Skeleton";
import FilterChips from "../components/ui/FilterChips";
import MatchCard from "../components/ui/MatchCard";
import { fetchMatches, type MatchSummary } from "../api/client";
import { dayGroupKey, dayGroupLabel } from "../lib/datetime";

type StatusFilter = "scheduled" | "completed" | "all";

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "scheduled", label: "Upcoming" },
  { value: "completed", label: "Completed" },
  { value: "all", label: "All" },
];

function MatchesPage() {
  const [status, setStatus] = useState<StatusFilter>("scheduled");
  const [matches, setMatches] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load(s: StatusFilter) {
    setMatches(null);
    setError(null);
    fetchMatches(s === "all" ? undefined : s)
      .then(setMatches)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load matches"));
  }

  useEffect(() => load(status), [status]);

  const groupedByDay = useMemo(() => {
    if (!matches) return [];
    const groups = new Map<string, { label: string; matches: MatchSummary[] }>();
    for (const m of matches) {
      const key = dayGroupKey(m.scheduled_start);
      if (!groups.has(key)) groups.set(key, { label: dayGroupLabel(m.scheduled_start), matches: [] });
      groups.get(key)!.matches.push(m);
    }
    const sortAsc = status !== "completed";
    return [...groups.entries()].sort(([a], [b]) => (sortAsc ? a.localeCompare(b) : b.localeCompare(a)));
  }, [matches, status]);

  return (
    <main className="matches-page">
      <PageHeader
        eyebrow="Matches"
        title="Fixtures & Results"
        description="Every AFL match with a model outlook, live bookmaker markets, and player projections — pick one to open its full match hub."
      />

      <FilterChips label="Show" options={STATUS_OPTIONS} value={status} onChange={setStatus} />

      {error && <div className="error-banner">{error}</div>}

      {matches === null && !error && (
        <div className="match-card-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card">
              <Skeleton width="60%" height="0.9rem" />
              <div style={{ height: 10 }} />
              <Skeleton width="100%" height="2.2rem" />
            </div>
          ))}
        </div>
      )}

      {matches !== null && matches.length === 0 && (
        <EmptyState
          title="No matches to show"
          description="Try a different filter, or check back once the next round is scheduled."
        />
      )}

      {groupedByDay.map(([key, group]) => (
        <div key={key} className="matches-page__day-group">
          <h2 className="matches-page__day-label">{group.label}</h2>
          <div className="match-card-grid">
            {group.matches.map((m) => (
              <MatchCard key={m.id} match={m} />
            ))}
          </div>
        </div>
      ))}
    </main>
  );
}

export default MatchesPage;
