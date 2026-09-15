import type { ReactNode } from "react";

/** One large, readable statistic — the "large understandable statistics"
 * building block used on Home, Player, and Match pages instead of dense
 * tables as the first thing a user sees. */
export function StatTile({ label, value, meta }: { label: string; value: ReactNode; meta?: ReactNode }) {
  return (
    <div className="stat-tile">
      <span className="stat-tile__label">{label}</span>
      <span className="stat-tile__value">{value}</span>
      {meta && <span className="stat-tile__meta">{meta}</span>}
    </div>
  );
}

/** A responsive row of StatTiles — wraps to fewer columns on narrow screens. */
export function StatTileRow({ children }: { children: ReactNode }) {
  return <div className="stat-tile-row">{children}</div>;
}
