/** Simple pill/tab filter control — "STAT: Disposals | Goals",
 * "RANGE: L5 | L10 | Season", etc. Only ever renders options actually
 * supported by the data/API — never a fake control. */
function FilterChips<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label?: string;
  options: { value: T; label: string; disabled?: boolean }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label}>
      {label && <span className="stat-tile__label" style={{ display: "block", marginBottom: "0.35rem" }}>{label}</span>}
      <div className="filter-chip-row">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className={o.value === value ? "filter-chip filter-chip--active" : "filter-chip"}
            aria-pressed={o.value === value}
            disabled={o.disabled}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default FilterChips;
