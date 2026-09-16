/** A minimal, geometric brand mark: an oval (a nod to the AFL ball/field)
 * crossed by a rising trend-line with a highlighted data point at its
 * peak — teal for the mark, muted gold for the single data-point accent.
 * Deliberately simple enough to read at nav/favicon size. */
function Logo({ size = 20 }: { size?: number }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} fill="none" aria-hidden="true">
      <ellipse cx="16" cy="17" rx="13" ry="7.5" transform="rotate(-24 16 17)" stroke="currentColor" strokeWidth="2" />
      <path d="M6 21 L12.5 13.5 L17.5 17 L26 7.5" stroke="var(--accent-gold)" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="26" cy="7.5" r="2" fill="var(--accent-gold)" />
    </svg>
  );
}

export default Logo;
