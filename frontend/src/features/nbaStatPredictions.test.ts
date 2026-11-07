import { describe, expect, it } from "vitest";
import type { NbaStatPrediction } from "../api/client";
import { formatStatMean, formatStatRange, labelsWithRows, validationText } from "./nbaStatPredictions";

const pred = (calibrated: boolean): NbaStatPrediction => ({
  mean: 6.4,
  rate_per_minute: 0.2,
  interval_80: calibrated ? { p10: 3, p90: 10 } : null,
  calibrated,
  baselines: {},
});

describe("formatStatRange", () => {
  it("shows a range only for a calibrated distribution", () => {
    expect(formatStatRange(pred(true))).toBe("3–10");
    expect(formatStatRange(pred(false))).toBeNull();
    expect(formatStatRange({ ...pred(false), interval_80: { p10: 1, p90: 2 } })).toBeNull();
    expect(formatStatRange(undefined)).toBeNull();
  });
});

describe("formatStatMean", () => {
  it("rounds to one decimal and dashes missing values", () => {
    expect(formatStatMean(12.345)).toBe("12.3");
    expect(formatStatMean(null)).toBe("—");
    expect(formatStatMean(undefined)).toBe("—");
  });
});

describe("labelsWithRows", () => {
  it("keeps window order and drops labels with no rows", () => {
    expect(labelsWithRows(["T-4h", "T-1h"], [{ label: "T-1h" }])).toEqual(["T-1h"]);
    expect(labelsWithRows(["T-4h", "T-1h"], [{ label: "T-1h" }, { label: "T-4h" }])).toEqual(["T-4h", "T-1h"]);
    expect(labelsWithRows(["T-4h", "T-1h"], [])).toEqual([]);
  });
});

describe("validationText", () => {
  it("states the offline result plainly", () => {
    expect(validationText({ mean: "passed_offline", distribution: "failed_offline", calibrated: false })).toBe("Mean passed · distribution failed offline");
    expect(validationText(undefined)).toBe("No rows");
  });
});
