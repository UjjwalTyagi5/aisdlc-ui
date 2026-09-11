/**
 * A chat listed on the project overview is one click away from being resumed.
 *
 * WHAT MAKES THIS EASY TO BREAK. The link has to carry three things that live in
 * different places: the project (route), the agent (which PAGE owns that conversation)
 * and the session (which conversation to restore). Get the middle one wrong and the
 * click lands on a real page with a blank chat — no error, nothing missing on screen,
 * and the conversation looks lost rather than misrouted.
 *
 * The agent ids are the trap. `ConversationSession.agent_id` is whatever the page
 * passed to `useAgentChat`, and two of them do not match their phase: Requirements
 * says `requirement`, Code Review says `code_review`, while the routes are
 * `requirements` and `code-review`.
 */
import { describe, expect, it } from "vitest";

import { chatAgentPhase, chatSessionHref } from "@/lib/agents";

const PROJECT = "11111111-1111-1111-1111-111111111111";
const SESSION = "22222222-2222-2222-2222-222222222222";

/** Every agent id a chat session can carry, taken from the pages' useAgentChat calls. */
const AGENT_PAGE: Record<string, string> = {
  requirement: "requirements",
  design: "design",
  development: "development",
  code_review: "code-review",
  security: "security",
  testing: "testing",
  deployment: "deployment",
  documentation: "documentation",
  plan: "plan",
};

describe("a listed chat links back to itself", () => {
  for (const [agentId, route] of Object.entries(AGENT_PAGE)) {
    it(`${agentId} → /${route}`, () => {
      const href = chatSessionHref(PROJECT, agentId, SESSION);

      expect(href).toBe(`/projects/${PROJECT}/${route}?session=${SESSION}`);
    });
  }

  it("the two ids that do not match their phase are the ones to check", () => {
    // Named explicitly: if either mapping is dropped, the loop above still passes for
    // the other seven and this says which pair went.
    expect(chatAgentPhase("requirement")).toBe("requirements");
    expect(chatAgentPhase("code_review")).toBe("review");
  });

  it("the orchestrator has no stage page and goes to its own", () => {
    expect(chatSessionHref(PROJECT, "orchestrator", SESSION)).toBe(
      `/projects/${PROJECT}/orchestrator?session=${SESSION}`,
    );
  });

  it("an agent this build does not know does not produce a broken stage link", () => {
    // Better the Orchestrator, which owns every conversation, than /projects/x/undefined.
    expect(chatSessionHref(PROJECT, "some_future_agent", SESSION)).toContain("/orchestrator?");
  });

  it("the session id is encoded, not concatenated", () => {
    expect(chatSessionHref(PROJECT, "design", "a b&c")).toBe(
      `/projects/${PROJECT}/design?session=a%20b%26c`,
    );
  });
});
