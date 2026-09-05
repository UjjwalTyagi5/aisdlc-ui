import { describe, expect, it } from "vitest";

import { agentWsPath } from "@/app/api/chat/route";

/**
 * The BFF's agent → FastAPI WS mapping, pinned.
 *
 * The `default` case routes to the LEGACY orchestrator engine
 * (`/sdlc/agent/orchestrator/ws`), which Phase 5 retires. Without this test that
 * retirement silently breaks every caller that omits `agent`. If you are changing
 * the default, you are changing behaviour — update this test deliberately.
 */
describe("agentWsPath", () => {
  it.each([
    ["requirements", "/sdlc/agent/requirement/ws"],
    ["requirement", "/sdlc/agent/requirement/ws"],
    ["design", "/sdlc/agent/design/ws"],
    ["plan", "/sdlc/agent/plan/ws"],
    ["development", "/sdlc/agent/development/ws"],
    ["code_review", "/sdlc/agent/code-review/ws"],
    ["code-review", "/sdlc/agent/code-review/ws"],
    ["security", "/sdlc/agent/security/ws"],
    ["testing", "/sdlc/agent/testing/ws"],
    ["deployment", "/sdlc/agent/deployment/ws"],
    ["documentation", "/sdlc/agent/documentation/ws"],
  ])("maps %s to its own agent socket", (agent, path) => {
    expect(agentWsPath(agent)).toBe(path);
  });

  it("routes all nine agents to a dedicated socket, never the legacy engine", () => {
    const nine = [
      "requirements", "design", "plan", "development", "code_review",
      "security", "testing", "deployment", "documentation",
    ];
    for (const agent of nine) {
      expect(agentWsPath(agent)).not.toBe("/sdlc/agent/orchestrator/ws");
    }
  });

  it("falls back to the legacy orchestrator socket for unknown agents", () => {
    expect(agentWsPath(undefined)).toBe("/sdlc/agent/orchestrator/ws");
    expect(agentWsPath("nonsense")).toBe("/sdlc/agent/orchestrator/ws");
  });
});
