import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchPlayers, type PlayerSummary } from "../../api/client";
import "./PlayerSearch.css";

/** The "type a player's name, go straight to their page" search used on
 * Home and the Players landing — a user should never need to know which
 * internal analysis page holds a given player. */
function PlayerSearch({ placeholder = "Search for a player, e.g. Nick Daicos" }: { placeholder?: string }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PlayerSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults(null);
      return;
    }
    setLoading(true);
    const handle = setTimeout(() => {
      fetchPlayers({ name: query.trim(), limit: 8 })
        .then((res) => setResults(res.players))
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function select(player: PlayerSummary) {
    setQuery("");
    setResults(null);
    setOpen(false);
    navigate(`/players/${player.id}`);
  }

  return (
    <div className="player-search" ref={containerRef}>
      <input
        type="search"
        className="player-search__input"
        placeholder={placeholder}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        aria-label="Search for a player"
      />
      {open && query.trim().length >= 2 && (
        <div className="player-search__results">
          {loading && <div className="player-search__hint">Searching…</div>}
          {!loading && results && results.length === 0 && <div className="player-search__hint">No players match "{query}".</div>}
          {!loading &&
            results &&
            results.map((p) => (
              <button key={p.id} type="button" className="player-search__result" onClick={() => select(p)}>
                <span className="player-search__result-name">{p.display_name}</span>
                {p.current_team && <span className="player-search__result-team">{p.current_team.name}</span>}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

export default PlayerSearch;
