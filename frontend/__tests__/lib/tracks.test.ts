import { describe, expect, it } from "vitest";
import { agentsForTrack } from "@/lib/tracks";

describe("data_engineering's portfolio matches the design doc's Portfolio 4", () => {
  it("has exactly 6 agents, in hand-off order", () => {
    expect(agentsForTrack("data_engineering")).toEqual([
      "requirements", "data_engineering", "security", "testing", "deployment", "documentation",
    ]);
  });

  it("has no design, development, or code-review stage", () => {
    const roster = agentsForTrack("data_engineering");
    expect(roster).not.toContain("design");
    expect(roster).not.toContain("development");
    expect(roster).not.toContain("review");
  });
});
