import { describe, expect, it } from "vitest";
import {
  dayGroupKey,
  dayGroupLabel,
  formatCompactDateTime,
  formatCountdown,
  formatFullDateTime,
  formatPreciseDateTime,
  formatShortDate,
  formatShortDateWithYear,
  formatTimeOnly,
} from "./datetime";

// Real AFL-season-relevant instants either side of the 2026 Australian DST
// transitions (Tasmania follows the national AEST/AEDT switchover):
//   - AEDT (UTC+11) is in effect roughly early Oct - early Apr.
//   - AEST (UTC+10) is in effect roughly early Apr - early Oct (most of the
//     home-and-away season).
// These are not the transition instants themselves (arbitrary times deep
// inside each period are enough to prove the offset is right); a separate
// pair of tests below straddles the actual switchover moments.
const AEDT_INSTANT = "2026-01-15T03:00:00Z"; // mid-January, AEDT (+11) in effect
const AEST_INSTANT = "2026-07-15T03:00:00Z"; // mid-July (finals-bound rounds), AEST (+10) in effect

describe("formatFullDateTime", () => {
  it("renders the exact brief example format", () => {
    // 2026-08-22 09:35 UTC = AEST (+10) = 2026-08-22 19:35 local = 7:35 PM.
    const result = formatFullDateTime("2026-08-22T09:35:00Z");
    expect(result).toBe("Saturday 22 August, 7:35 PM");
  });

  it("uses AEDT (UTC+11) in January, not AEST", () => {
    // 03:00 UTC + 11h = 14:00 local = 2:00 PM.
    const result = formatFullDateTime(AEDT_INSTANT);
    expect(result).toContain("2:00 PM");
  });

  it("uses AEST (UTC+10) in July, not AEDT", () => {
    // 03:00 UTC + 10h = 13:00 local = 1:00 PM.
    const result = formatFullDateTime(AEST_INSTANT);
    expect(result).toContain("1:00 PM");
  });

  it("never renders raw UTC hours for an AEDT instant", () => {
    const result = formatFullDateTime(AEDT_INSTANT);
    expect(result).not.toContain("3:00 AM"); // the raw UTC hour, i.e. the bug this module fixes
  });
});

describe("AEDT -> AEST transition (early April 2026)", () => {
  // The Australian clock-back happens at 3:00 AM AEDT -> 2:00 AM AEST on
  // the first Sunday in April. In 2026 that's April 5th. Just before the
  // transition, Hobart is UTC+11; just after, UTC+10.
  it("is still AEDT (+11) shortly before the switchover", () => {
    // 2026-04-04 15:00 UTC + 11h = 2026-04-05 02:00 local (just before 3am AEDT rolls back).
    const result = formatFullDateTime("2026-04-04T15:00:00Z");
    expect(result).toContain("2:00 AM");
  });

  it("is AEST (+10) shortly after the switchover", () => {
    // 2026-04-04 17:00 UTC + 10h = 2026-04-05 03:00 local AEST.
    const result = formatFullDateTime("2026-04-04T17:00:00Z");
    expect(result).toContain("3:00 AM");
  });
});

describe("AEST -> AEDT transition (early October 2026)", () => {
  // The clock-forward happens at 2:00 AM AEST -> 3:00 AM AEDT on the first
  // Sunday in October. In 2026 that's October 4th.
  it("is still AEST (+10) shortly before the switchover", () => {
    // 2026-10-03 15:30 UTC + 10h = 2026-10-04 01:30 local AEST.
    const result = formatFullDateTime("2026-10-03T15:30:00Z");
    expect(result).toContain("1:30 AM");
  });

  it("is AEDT (+11) shortly after the switchover", () => {
    // 2026-10-03 16:30 UTC + 11h = 2026-10-04 03:30 local AEDT.
    const result = formatFullDateTime("2026-10-03T16:30:00Z");
    expect(result).toContain("3:30 AM");
  });
});

describe("formatCompactDateTime", () => {
  it("renders the compact card format", () => {
    const result = formatCompactDateTime("2026-08-21T09:40:00Z"); // AEST -> 7:40 PM
    expect(result).toBe("Fri 21 Aug · 7:40 PM");
  });
});

describe("formatShortDate / formatShortDateWithYear", () => {
  it("renders day and short month only", () => {
    expect(formatShortDate("2026-08-22T09:35:00Z")).toBe("22 Aug");
  });

  it("includes the year when requested", () => {
    expect(formatShortDateWithYear("2026-08-22T09:35:00Z")).toBe("22 Aug 2026");
  });
});

describe("formatTimeOnly", () => {
  it("renders just the local clock time", () => {
    expect(formatTimeOnly("2026-08-22T09:35:00Z")).toBe("7:35 PM");
  });
});

describe("formatPreciseDateTime", () => {
  it("includes seconds for sub-minute precision", () => {
    const result = formatPreciseDateTime("2026-08-17T04:56:47Z"); // AEST -> 2:56:47 PM
    expect(result).toContain("2:56:47 PM");
  });
});

describe("dayGroupLabel / dayGroupKey", () => {
  it("groups a match by its LOCAL calendar day, not its UTC calendar day", () => {
    // 2026-08-23 14:35 UTC is a Sunday in UTC, but +10h AEST = 2026-08-24
    // 00:35 local - already Monday in Hobart. A grouping keyed on UTC would
    // wrongly put this match under Sunday.
    const iso = "2026-08-23T14:35:00Z";
    expect(dayGroupLabel(iso)).toBe("Monday");
    expect(dayGroupKey(iso)).toBe("2026-08-24");
  });

  it("produces a lexicographically sortable key", () => {
    const earlier = dayGroupKey("2026-08-21T09:00:00Z");
    const later = dayGroupKey("2026-08-23T09:00:00Z");
    expect(earlier < later).toBe(true);
  });
});

describe("formatCountdown", () => {
  const now = new Date("2026-08-20T09:35:00Z");

  it("shows days and hours when the match is more than a day away", () => {
    expect(formatCountdown("2026-08-22T09:40:00Z", now)).toBe("in 2d 0h");
  });

  it("shows hours and minutes when under a day away", () => {
    expect(formatCountdown("2026-08-20T15:05:00Z", now)).toBe("in 5h 30m");
  });

  it("shows minutes only when under an hour away", () => {
    expect(formatCountdown("2026-08-20T10:05:00Z", now)).toBe("in 30m");
  });

  it("reports Live now once kickoff has passed but the match is presumably still on", () => {
    expect(formatCountdown("2026-08-20T09:00:00Z", now)).toBe("Live now");
  });

  it("reports Final well after kickoff has passed", () => {
    expect(formatCountdown("2026-08-20T04:00:00Z", now)).toBe("Final");
  });
});
