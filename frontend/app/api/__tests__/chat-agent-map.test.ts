import { describe, expect, it } from "vitest";

import { agentWsPath } from "@/app/api/chat/route";

/**
 * The BFF's agent → FastAPI WS mapping, pinned.
 *
 * The `default` case USED to route to the legacy orchestrator engine
 * (`/sdlc/agent/orchestrator/ws`). Phase 5 retired that engine, so the default now
 * REFUSES: an unmapped agent returns null and the route answers 400.
 *
 * That is deliberate rather than convenient. Every caller (`useAgentChat`) passes an
 * explicit agent, so reaching the default at all is a programming error — and an
 * unknown agent quietly answering as something else, with no error and a reply that
 * looks complete, is the exact failure class this rebuild exists to remove.
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
    // Track 3 — Code Modernization.
    ["requirements_modernization", "/sdlc/agent/requirements-modernization/ws"],
    ["discovery", "/sdlc/agent/discovery/ws"],
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

  it("refuses an unmapped agent instead of routing it somewhere", () => {
    expect(agentWsPath(undefined)).toBeNull();
    expect(agentWsPath("nonsense")).toBeNull();
  });

  it("names no retired engine anywhere in the table", () => {
    const nine = [
      "requirements", "design", "plan", "development", "code_review",
      "security", "testing", "deployment", "documentation",
    ];
    for (const agent of nine) {
      expect(agentWsPath(agent)).not.toContain("/sdlc/agent/orchestrator/ws");
      expect(agentWsPath(agent)).not.toContain("/sdlc/agent/copilot/ws");
    }
  });

  it("still maps every one of the nine", () => {
    // The other half of refusing a default: if a mapping is ever dropped, that agent
    // would start returning null and be refused outright rather than mis-routed.
    const nine = [
      "requirements", "design", "plan", "development", "code_review",
      "security", "testing", "deployment", "documentation",
    ];
    for (const agent of nine) {
      expect(agentWsPath(agent), `${agent} is unmapped`).toBeTruthy();
    }
  });
});
