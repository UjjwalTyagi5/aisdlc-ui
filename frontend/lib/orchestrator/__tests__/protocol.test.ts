import { describe, expect, it } from "vitest";

import {
  ORCHESTRATOR_AGENT_IDS,
  OrchestratorEvent,
} from "@/lib/orchestrator/protocol";

describe("OrchestratorEvent", () => {
  it("accepts a token chunk", () => {
    const parsed = OrchestratorEvent.safeParse({
      type: "stream_chunk",
      content: "hello",
    });
    expect(parsed.success).toBe(true);
  });

  it("accepts agent.selected and carries the reason", () => {
    const parsed = OrchestratorEvent.safeParse({
      type: "agent.selected",
      agent: "requirements",
      reason: "The user asked for a PRD.",
    });
    expect(parsed.success).toBe(true);
    if (parsed.success && parsed.data.type === "agent.selected") {
      expect(parsed.data.agent).toBe("requirements");
    }
  });

  it("rejects agent.selected naming an agent that does not exist", () => {
    const parsed = OrchestratorEvent.safeParse({
      type: "agent.selected",
      agent: "not_an_agent",
      reason: "x",
    });
    expect(parsed.success).toBe(false);
  });

  // The Orchestrator has no gates (spec D5). If the backend ever emits one it is a
  // bug, and the UI must drop it rather than render a gate the user cannot action.
  it("rejects gate events", () => {
    expect(
      OrchestratorEvent.safeParse({
        type: "gate.state",
        stage: "design",
        owner_role: "architect",
        can_approve: true,
      }).success,
    ).toBe(false);
  });

  it("covers all nine agents", () => {
    expect([...ORCHESTRATOR_AGENT_IDS].sort()).toEqual(
      [
        "code_review", "deployment", "design", "development", "documentation",
        "plan", "requirements", "security", "testing",
      ].sort(),
    );
  });
});
