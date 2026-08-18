// Application display timezone (live-operations product-quality stage,
// Section 1): this product is used from Tasmania. Every user-facing
// timestamp must be rendered in this IANA zone, never the browser's local
// zone (which is what `new Date(iso).toLocaleString()` without an explicit
// `timeZone` silently does — the actual bug this module fixes) and never a
// fixed UTC offset (Australia/Hobart observes AEDT in summer, so a fixed
// "+10:00" is wrong for roughly half the year). `Intl.DateTimeFormat`'s
// IANA support handles the AEST/AEDT transition correctly on its own.
export const DISPLAY_TIMEZONE = "Australia/Hobart";
const LOCALE = "en-AU";

function toDate(iso: string | Date): Date {
  return iso instanceof Date ? iso : new Date(iso);
}

// Every formatter below builds its output from individual `formatToParts`
// fields rather than trusting a locale's combined date/time string. The
// combined form's exact punctuation and AM/PM casing vary by ICU version
// (Node's bundled ICU renders "pm" and "Fri, 21 Aug"; browsers commonly
// render "PM" and "Fri 21 Aug") — assembling the parts ourselves is what
// makes the brief's exact example format ("Saturday 22 August, 7:35 PM")
// reproducible everywhere, not just in whichever environment happened to
// write the code.
function partsOf(date: Date, options: Intl.DateTimeFormatOptions): Record<string, string> {
  const parts = new Intl.DateTimeFormat(LOCALE, { ...options, timeZone: DISPLAY_TIMEZONE }).formatToParts(date);
  const lookup: Record<string, string> = {};
  for (const part of parts) lookup[part.type] = part.value;
  return lookup;
}

function clockTime(date: Date, withSeconds = false): string {
  const p = partsOf(date, {
    hour: "numeric",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: true,
  });
  const meridiem = (p.dayPeriod ?? "").toUpperCase();
  const time = withSeconds ? `${p.hour}:${p.minute}:${p.second}` : `${p.hour}:${p.minute}`;
  return `${time} ${meridiem}`;
}

// "Saturday 22 August, 7:35 PM" — the exact example format from the brief.
// Used wherever a match kickoff or other single prominent moment is the
// main content of what's being displayed.
export function formatFullDateTime(iso: string | Date): string {
  const date = toDate(iso);
  const p = partsOf(date, { weekday: "long", day: "numeric", month: "long" });
  return `${p.weekday} ${p.day} ${p.month}, ${clockTime(date)}`;
}

// "Sat 22 Aug · 7:35 PM" — compact form for match cards, table cells, and
// anywhere space is tight but both date and time still matter.
export function formatCompactDateTime(iso: string | Date): string {
  const date = toDate(iso);
  const p = partsOf(date, { weekday: "short", day: "numeric", month: "short" });
  return `${p.weekday} ${p.day} ${p.month} · ${clockTime(date)}`;
}

// "22 Aug" — for compact date-only contexts (recent-results cards, roster
// "last played" dates) where the day-of-week and time aren't needed.
export function formatShortDate(iso: string | Date): string {
  const p = partsOf(toDate(iso), { day: "numeric", month: "short" });
  return `${p.day} ${p.month}`;
}

// "22 Aug 2026" — for contexts spanning multiple years (career game logs).
export function formatShortDateWithYear(iso: string | Date): string {
  const p = partsOf(toDate(iso), { day: "numeric", month: "short", year: "numeric" });
  return `${p.day} ${p.month} ${p.year}`;
}

// "7:35 PM" — time only, for rows that already show the date elsewhere
// (e.g. a table already grouped/headed by day).
export function formatTimeOnly(iso: string | Date): string {
  return clockTime(toDate(iso));
}

// A full timestamp including seconds, for odds/lineup/observation
// snapshots where sub-minute precision is meaningful (e.g. distinguishing
// two quotes fetched moments apart in the same refresh cycle).
export function formatPreciseDateTime(iso: string | Date): string {
  const date = toDate(iso);
  const p = partsOf(date, { day: "numeric", month: "short" });
  return `${p.day} ${p.month}, ${clockTime(date, true)}`;
}

// The calendar day name in the display timezone — "Friday" | "Saturday" |
// "Sunday" | ... — for grouping a round's matches by day (Section 20).
// Computed from the display-timezone wall-clock date, not UTC: a match at
// 9:35 UTC on a Saturday is genuinely Sunday morning in Hobart, and must
// group under Sunday, not Saturday.
export function dayGroupLabel(iso: string | Date): string {
  return partsOf(toDate(iso), { weekday: "long" }).weekday;
}

// A stable "YYYY-MM-DD"-shaped key for the display-timezone calendar day,
// safe to use as a grouping/sort key (unlike dayGroupLabel's name alone,
// which repeats every 7 days and carries no ordering information).
export function dayGroupKey(iso: string | Date): string {
  const date = toDate(iso);
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: DISPLAY_TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const lookup = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return `${lookup.year}-${lookup.month}-${lookup.day}`;
}

// "in 2d 4h" / "in 45m" / "Live now" / "Final" — TZ-agnostic (it's just a
// duration), but grouped here since every caller displaying a kickoff also
// wants a countdown alongside it, computed against the same `now`.
export function formatCountdown(iso: string | Date, now: Date = new Date()): string {
  const target = toDate(iso);
  const diffMs = target.getTime() - now.getTime();
  const diffMinutes = Math.round(diffMs / 60000);

  if (diffMinutes <= 0 && diffMinutes > -240) return "Live now";
  if (diffMinutes <= -240) return "Final";

  const days = Math.floor(diffMinutes / (60 * 24));
  const hours = Math.floor((diffMinutes % (60 * 24)) / 60);
  const minutes = diffMinutes % 60;

  if (days > 0) return `in ${days}d ${hours}h`;
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  return `in ${minutes}m`;
}

// "6:14 PM" style short clock time, used for "Last odds refresh: X" /
// "Recommended next refresh: X" (Section 19) where only the time — not a
// full date — belongs in the sentence.
export function formatClockTime(iso: string | Date): string {
  return formatTimeOnly(iso);
}
