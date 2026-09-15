/** Generic pill tab bar reusing the app's existing .tab-bar styling. Used
 * to turn a long stack of unrelated-looking sections into one coherent hub
 * (e.g. the Match page: Overview / Market / Player Markets / Multis /
 * Movement / Player Stats) without changing what each section renders. */
function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="tab-bar" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={t.value === value}
          className={t.value === value ? "tab-bar__tab tab-bar__tab--active" : "tab-bar__tab"}
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export default Tabs;
