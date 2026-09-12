import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { formatValue, selectEvidence, type ContextEvidenceGame, type EvidenceFilters, type EvidenceOrder } from "../features/playerContext";

const PAGE_SIZE = 20;
const DEFAULT_FILTERS: EvidenceFilters = { teammate: "all", season: null, opponent: "", order: "newest" };
const dateFormat = new Intl.DateTimeFormat("en-AU", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export default function PlayerContextEvidence({ rows, stat }: { rows: ContextEvidenceGame[]; stat: string }) {
  const [filters, setFilters] = useState<EvidenceFilters>(DEFAULT_FILTERS);
  const [page, setPage] = useState(0);
  const evidence = useMemo(() => selectEvidence(rows, filters), [rows, filters]);
  const seasons = [...new Set(rows.map(row => row.season_year))].sort((a, b) => b - a);
  const pageCount = Math.max(1, Math.ceil(evidence.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const first = currentPage * PAGE_SIZE;
  const visible = evidence.slice(first, first + PAGE_SIZE);
  const update = (patch: Partial<EvidenceFilters>) => { setFilters(current => ({ ...current, ...patch })); setPage(0); };
  const reset = () => { setFilters(DEFAULT_FILTERS); setPage(0); };
  const filtered = filters.teammate !== "all" || filters.season !== null || filters.opponent.trim() !== "";

  return <section aria-labelledby="evidence-heading">
    <div className="section-row research-evidence-heading"><div><h2 id="evidence-heading">Inspect the evidence</h2><p className="hint">Filters below affect this table only. The comparison above uses the full API history.</p></div></div>
    <div className="research-evidence-controls">
      <div className="research-filter-buttons" aria-label="Filter evidence games">{(["all", "in", "out"] as const).map(value => <button key={value} type="button" aria-pressed={filters.teammate === value} className={filters.teammate === value ? "is-active" : ""} onClick={() => update({ teammate: value })}>{value === "all" ? "All games" : value === "in" ? "With teammate" : "Without teammate"} <span>({value === "all" ? rows.length : rows.filter(row => row.teammate_played === (value === "in")).length})</span></button>)}</div>
      <div className="research-evidence-fields">
        <label className="research-select-field">Season<select value={filters.season ?? ""} onChange={event => update({ season: event.target.value === "" ? null : Number(event.target.value) })}><option value="">All seasons</option>{seasons.map(season => <option key={season} value={season}>{season}</option>)}</select></label>
        <label className="research-select-field">Opponent<input type="search" placeholder="Filter opponent" value={filters.opponent} onChange={event => update({ opponent: event.target.value })} /></label>
        <label className="research-select-field">Sort games<select value={filters.order} onChange={event => update({ order: event.target.value as EvidenceOrder })}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="highest">Highest {stat}</option><option value="lowest">Lowest {stat}</option></select></label>
        {filtered && <button type="button" onClick={reset}>Clear filters</button>}
      </div>
    </div>
    <p className="hint" role="status">{evidence.length === 0 ? "No matching games" : `Showing ${first + 1}–${first + visible.length} of ${evidence.length} matching games`} · {rows.length} games in the full history</p>
    {evidence.length === 0 ? <div className="card research-empty-evidence"><h3>{rows.length === 0 ? "No evidence available" : "No games match these filters"}</h3><p>{rows.length === 0 ? "The API did not return recorded match evidence for this comparison." : "Try another season or opponent, or clear the filters to see the full history."}</p>{filtered && <button type="button" onClick={reset}>Show all games</button>}</div> : <>
      <div className="table-scroll research-evidence-table-wrap" role="region" aria-label="Player context match evidence" tabIndex={0}><table className="data-table research-evidence-table"><caption>Recorded match evidence · {stat} · dates shown in UTC</caption><thead><tr><th scope="col">Match</th><th scope="col">Date</th><th scope="col">Opponent</th><th scope="col">Venue</th><th scope="col">Teammate record</th><th scope="col">{stat}</th><th scope="col">Time on ground</th></tr></thead><tbody>{visible.map(game => <tr key={game.match_id}><td><Link to={`/matches/${game.match_id}`}>{game.round_name ?? `Round ${game.round_number}`}, {game.season_year}</Link></td><td>{Number.isNaN(Date.parse(game.scheduled_start)) ? "Unavailable" : <time dateTime={game.scheduled_start}>{dateFormat.format(new Date(game.scheduled_start))}</time>}</td><td>{game.opponent_name ?? "Unavailable"}</td><td>{game.venue_name ?? "Unavailable"}</td><td><span className={`chip ${game.teammate_played ? "chip--success" : "chip--neutral"}`}>{game.teammate_played ? "With" : "Without"}</span></td><td className="num">{formatValue(game.stat_value, 0)}</td><td>{game.time_on_ground_pct == null ? "Unavailable" : `${game.time_on_ground_pct}%`}</td></tr>)}</tbody></table></div>
      {pageCount > 1 && <nav className="research-pagination" aria-label="Evidence pages"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button><span>Page {currentPage + 1} of {pageCount}</span><button type="button" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>Next</button></nav>}
    </>}
  </section>;
}
