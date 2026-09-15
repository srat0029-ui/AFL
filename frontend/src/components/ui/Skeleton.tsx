/** A polished loading placeholder sized like the real content, so the
 * layout doesn't jump once data arrives. Use several stacked/side-by-side
 * for card/list skeletons rather than a single generic spinner. */
function Skeleton({ width = "100%", height = "1rem", radius }: { width?: string | number; height?: string | number; radius?: string }) {
  return (
    <span
      className="skeleton"
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

export default Skeleton;
