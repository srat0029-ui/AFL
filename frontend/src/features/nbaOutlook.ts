import type { NbaAvailabilityEvidence, NbaPlayerOutlookRow, NbaWindowState } from "../api/client";

export type ChipVariant = "success" | "warning" | "danger" | "neutral" | "accent";

/** How a snapshot window's state reads on the dashboard. Only "produced"
 * is a clean frozen outlook; everything else says plainly what is missing. */
export function windowStateDisplay(state: NbaWindowState): { label: string; variant: ChipVariant; description: string } {
  switch (state) {
    case "produced":
      return { label: "Frozen", variant: "success", description: "Outlook frozen on time with fresh evidence." };
    case "stale":
      return { label: "Stale evidence", variant: "warning", description: "Frozen, but some evidence was older than its freshness limit." };
    case "partial":
      return { label: "Partial", variant: "warning", description: "Frozen for only part of the roster." };
    case "missing_data":
      return { label: "Missing data", variant: "danger", description: "Required evidence was unavailable, so no outlook was frozen." };
    case "missed_window":
      return { label: "Missed", variant: "danger", description: "The window passed before the collector could freeze it. Never back-filled." };
    case "upcoming":
      return { label: "Upcoming", variant: "neutral", description: "This window's cutoff has not arrived yet." };
    case "none":
      return { label: "Not taken", variant: "neutral", description: "Nothing was frozen for this tip-off (e.g. before snapshots were switched on)." };
    default:
      return { label: String(state), variant: "neutral", description: "Unrecognised state." };
  }
}

/** The injury-feed state in plain words. "Not listed" is no evidence either
 * way — never shown as "healthy". */
export function availabilityDisplay(ev: NbaAvailabilityEvidence | null | undefined): { label: string; variant: ChipVariant } {
  switch (ev?.state) {
    case "listed": {
      const raw = ev.source_status ?? ev.status ?? "Listed";
      const out = (ev.status ?? "").toLowerCase() === "out" || raw.toLowerCase() === "out";
      return { label: raw, variant: out ? "danger" : "warning" };
    }
    case "no_longer_listed":
      return { label: "No longer listed", variant: "neutral" };
    case "never_listed":
      return { label: "Not listed", variant: "neutral" };
    case "feed_not_read_by_cutoff":
      return { label: "Feed not read", variant: "danger" };
    default:
      return { label: "Unknown", variant: "neutral" };
  }
}

/** Whether the feed's state now differs from what the outlook saw. */
export function availabilityChanged(seen: NbaAvailabilityEvidence | null | undefined, now: NbaAvailabilityEvidence | null | undefined): boolean {
  return availabilityDisplay(seen).label !== availabilityDisplay(now).label;
}

export function formatProbability(p: number | null | undefined): string {
  return p == null || Number.isNaN(p) ? "—" : `${Math.round(p * 100)}%`;
}

export function formatMinutes(m: number | null | undefined): string {
  return m == null || Number.isNaN(m) ? "—" : m.toFixed(1);
}

export function formatMinutesRange(q: Record<string, number> | null | undefined): string | null {
  if (!q || q.p10 == null || q.p90 == null) return null;
  return `${Math.round(q.p10)}–${Math.round(q.p90)}`;
}

/** The label to show by default: the latest window that has any outlook,
 * else the latest frozen one, else the first. */
export function defaultLabel(labels: string[], rows: NbaPlayerOutlookRow[]): string {
  const withData = labels.filter((label) => rows.some((r) => label in r.outlooks));
  return withData.length ? withData[withData.length - 1] : labels[0];
}

/** Minutes since an ISO timestamp, for "x min ago" freshness. */
export function minutesSince(iso: string | null | undefined, now: Date = new Date()): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : Math.max(0, Math.round((now.getTime() - t) / 60000));
}

export function formatAge(minutes: number | null | undefined): string {
  if (minutes == null) return "never";
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  if (minutes < 48 * 60) return `${(minutes / 60).toFixed(minutes < 600 ? 1 : 0)} h ago`;
  return `${Math.round(minutes / 1440)} days ago`;
}
