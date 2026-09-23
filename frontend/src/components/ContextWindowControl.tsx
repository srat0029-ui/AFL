import { CONTEXT_WINDOW_OPTIONS, type ContextWindowKey } from "../features/playerContext";

/** Scope control for teammate context. Changing it changes WHICH GAMES are
 * compared - never who the player is, the statistic, or the chosen teammate. */
export default function ContextWindowControl({ value, onChange }: { value: ContextWindowKey; onChange: (next: ContextWindowKey) => void }) {
  return <div className="research-window-control">
    <span className="research-window-control__label" id="context-window-label">Time scope</span>
    <div className="research-filter-buttons" role="group" aria-labelledby="context-window-label">
      {CONTEXT_WINDOW_OPTIONS.map(option => (
        <button key={option.key} type="button" aria-pressed={value === option.key} className={value === option.key ? "is-active" : ""} onClick={() => onChange(option.key)}>
          {option.short}
        </button>
      ))}
    </div>
    <p className="hint">Changing the scope changes which games are compared, not the player, statistic or teammate.</p>
  </div>;
}
