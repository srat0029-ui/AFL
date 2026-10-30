import { describe, expect, it } from "vitest";
import type { NbaPlayerOutlookRow } from "../api/client";
import {
  availabilityChanged,
  availabilityDisplay,
  defaultLabel,
  formatAge,
  formatMinutes,
  formatMinutesRange,
  formatProbability,
  minutesSince,
  windowStateDisplay,
} from "./nbaOutlook";

describe("windowStateDisplay", () => {
  it("only calls a produced snapshot clean", () => {
    expect(windowStateDisplay("produced").variant).toBe("success");
    for (const s of ["stale", "partial"]) expect(windowStateDisplay(s).variant).toBe("warning");
    for (const s of ["missing_data", "missed_window"]) expect(windowStateDisplay(s).variant).toBe("danger");
    expect(windowStateDisplay("upcoming").label).toBe("Upcoming");
    expect(windowStateDisplay("none").label).toBe("Not taken");
    expect(windowStateDisplay("something_new").label).toBe("something_new");
  });
});

describe("availabilityDisplay", () => {
  it("shows the feed's own words and never implies health", () => {
    expect(availabilityDisplay({ state: "listed", status: "out", source_status: "Out" })).toEqual({ label: "Out", variant: "danger" });
    expect(availabilityDisplay({ state: "listed", status: null, source_status: "Day-To-Day" })).toEqual({ label: "Day-To-Day", variant: "warning" });
    expect(availabilityDisplay({ state: "never_listed" }).label).toBe("Not listed");
    expect(availabilityDisplay({ state: "no_longer_listed" }).label).toBe("No longer listed");
    expect(availabilityDisplay({ state: "feed_not_read_by_cutoff" }).variant).toBe("danger");
    expect(availabilityDisplay(undefined).label).toBe("Unknown");
  });

  it("detects a change between what the outlook saw and now", () => {
    expect(availabilityChanged({ state: "listed", source_status: "Day-To-Day" }, { state: "listed", status: "out", source_status: "Out" })).toBe(true);
    expect(availabilityChanged({ state: "never_listed" }, { state: "never_listed" })).toBe(false);
  });
});

describe("formatting", () => {
  it("formats probabilities, minutes and ranges, with honest blanks", () => {
    expect(formatProbability(0.934)).toBe("93%");
    expect(formatProbability(null)).toBe("—");
    expect(formatMinutes(34.06)).toBe("34.1");
    expect(formatMinutes(null)).toBe("—");
    expect(formatMinutesRange({ p10: 26.4, p90: 39.6 })).toBe("26–40");
    expect(formatMinutesRange(null)).toBeNull();
  });

  it("formats ages", () => {
    expect(formatAge(null)).toBe("never");
    expect(formatAge(12)).toBe("12 min ago");
    expect(formatAge(90)).toBe("1.5 h ago");
    expect(formatAge(3 * 1440)).toBe("3 days ago");
    expect(minutesSince("2026-10-30T10:00:00Z", new Date("2026-10-30T10:30:00Z"))).toBe(30);
    expect(minutesSince(null)).toBeNull();
  });
});

describe("defaultLabel", () => {
  const row = (labels: string[]) => ({ outlooks: Object.fromEntries(labels.map((l) => [l, {}])) }) as unknown as NbaPlayerOutlookRow;
  it("picks the latest window that has outlooks", () => {
    const labels = ["T-24h", "T-4h", "T-1h", "T-30m"];
    expect(defaultLabel(labels, [row(["T-24h", "T-4h"]), row(["T-24h"])])).toBe("T-4h");
    expect(defaultLabel(labels, [])).toBe("T-24h");
  });
});
