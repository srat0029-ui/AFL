import { describe, expect, it } from "vitest";
import { buildScenarioProjection, MOCK_PLAYER_CONTEXT } from "./playerContext";

describe("buildScenarioProjection", () => {
  it("raises the projection when the selected teammate is out", () => {
    const teammateIn = buildScenarioProjection(MOCK_PLAYER_CONTEXT, {
      teammateStatus: "in",
      role: "mixed",
      tagAssumption: "possible",
    });
    const teammateOut = buildScenarioProjection(MOCK_PLAYER_CONTEXT, {
      teammateStatus: "out",
      role: "mixed",
      tagAssumption: "possible",
    });

    expect(teammateOut.expected - teammateIn.expected).toBeCloseTo(MOCK_PLAYER_CONTEXT.adjustedTeammateImpact);
  });

  it("reduces the projection as the tag assumption strengthens", () => {
    const noTag = buildScenarioProjection(MOCK_PLAYER_CONTEXT, {
      teammateStatus: "in",
      role: "midfield",
      tagAssumption: "none",
    });
    const likelyTag = buildScenarioProjection(MOCK_PLAYER_CONTEXT, {
      teammateStatus: "in",
      role: "midfield",
      tagAssumption: "likely",
    });

    expect(likelyTag.expected).toBeLessThan(noTag.expected);
    expect(likelyTag.probabilities[1].probability).toBeLessThan(noTag.probabilities[1].probability);
  });

  it("keeps milestone probabilities ordered from lower to higher thresholds", () => {
    const projection = buildScenarioProjection(MOCK_PLAYER_CONTEXT, {
      teammateStatus: "out",
      role: "midfield",
      tagAssumption: "possible",
    });

    expect(projection.probabilities[0].probability).toBeGreaterThan(projection.probabilities[1].probability);
    expect(projection.probabilities[1].probability).toBeGreaterThan(projection.probabilities[2].probability);
  });
});

