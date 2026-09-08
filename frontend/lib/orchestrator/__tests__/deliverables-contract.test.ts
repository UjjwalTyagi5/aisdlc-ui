import { describe, expect, it } from "vitest";

import { Deliverable, DeliverableReadyEvent } from "@/lib/orchestrator/deliverables";
import { OrchestratorEvent } from "@/lib/orchestrator/protocol";

/**
 * The union DROPS what fails `safeParse`. That has already cost this branch two
 * production-shaped bugs: an `ErrorEvent.agent` enum that guaranteed the one event
 * whose job was "that agent does not exist" could never arrive, and a
 * `StreamChunkEvent.content` typed as a string while the backend forwarded
 * Anthropic's block LIST — so the agent appeared to say nothing at all.
 *
 * Both were the right event TYPE with the wrong field shape, which is why these
 * tests check shapes rather than only that the type is accepted.
 */
describe("the deliverable wire contract", () => {
  const frame = {
    type: "deliverable.ready",
    run_id: "r-1",
    agent: "security",
    deliverables: [{
      id: "d-1", agent: "security", kind: "markdown",
      title: "Security Report", content: "body",
      url: null, language: null, source: null,
      created_at: "2026-09-06T10:00:00+00:00",
    }],
  };

  it("accepts exactly what the backend emits", () => {
    expect(OrchestratorEvent.safeParse(frame).success).toBe(true);
  });

  it("accepts the nullable columns the database actually returns", () => {
    // url/language/source/created_at are nullable columns and arrive as `null`. A
    // schema that accepts `undefined` but not `null` rejects the frame — and a
    // rejected frame is dropped, which on screen looks like an agent that produced
    // nothing rather than like a contract mismatch.
    expect(Deliverable.safeParse({
      id: "d", agent: "plan", kind: "markdown", title: "t",
      content: "c", url: null, language: null, source: null, created_at: null,
    }).success).toBe(true);
  });

  it("accepts every one of the nine agents, including plan", () => {
    for (const agent of ["requirements", "design", "plan", "development",
      "code_review", "security", "testing", "deployment", "documentation"]) {
      expect(Deliverable.safeParse({
        id: "d", agent, kind: "markdown", title: "t", content: "c",
      }).success, `${agent} was rejected`).toBe(true);
    }
  });

  it("accepts every renderable kind the panel can draw", () => {
    // A pointer arrives as `code-tree` / `file-tree` / `link`. Rejecting those would
    // drop the Development code tree, which is the one per-agent quirk named
    // explicitly in the requirement.
    for (const kind of ["markdown", "code-tree", "file-tree", "link", "code",
      "mermaid", "openapi", "image", "download"]) {
      expect(Deliverable.safeParse({
        id: "d", agent: "development", kind, title: "t",
      }).success, `${kind} was rejected`).toBe(true);
    }
  });

  it("rejects an unknown agent rather than rendering it under a blank heading", () => {
    expect(Deliverable.safeParse({
      id: "d", agent: "nonsense", kind: "markdown", title: "t", content: "c",
    }).success).toBe(false);
  });

  it("defaults a missing deliverables array rather than throwing", () => {
    const parsed = DeliverableReadyEvent.safeParse({
      type: "deliverable.ready", agent: "design",
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) expect(parsed.data.deliverables).toEqual([]);
  });

  it("carries no approval field, in either direction", () => {
    // The absence is the point: the Orchestrator has no gates, so an approval field
    // here would be the seam through which one gets wired into a surface that must
    // not have one.
    const parsed = Deliverable.safeParse({
      id: "d", agent: "security", kind: "markdown", title: "t",
      content: "c", approval_status: "pending",
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect("approval_status" in parsed.data).toBe(false);
    }
  });
});
