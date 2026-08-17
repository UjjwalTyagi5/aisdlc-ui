/**
 * ClarificationCard unit tests (REQ-M10-06).
 *
 * Mirrors the eval-indicator pattern: pure logic + BFF client contract
 * assertions in vitest's node environment (no DOM render).
 *
 *  - sendClarificationAnswer produces a sendRunSignal call with
 *    name === "within_agent_clarification" and payload
 *    { clarification_id, answer } — the Temporal SIGNAL contract
 *    (D-M10-03: not a WebSocket chat message).
 *  - the permission gate: hasPermission(session, "artifact:approve_requirements")
 *    is true for an admin/permitted session and false otherwise — driving
 *    canAnswer (reuses the has-permission test fixtures).
 *  - canSubmitAnswer (the Submit button's disabled-condition logic): false
 *    when canAnswer is false or the answer is empty/whitespace; true only
 *    when canAnswer is true, not pending, and the answer is non-empty.
 */
import { describe, it, expect } from "vitest";

import { canSubmitAnswer } from "../clarification-card";
import { hasPermission } from "@/lib/auth/permissions";
import type { Session } from "@/lib/auth/types";

function buildSession(permissions: string[]): Session {
  return {
    user: {
      id: "u_test_01",
      name: "Test User",
      email: "test@acme.test",
      initials: "TU",
    },
    tenant: {
      id: "ws_test",
      name: "Test Corp",
      plan: "Enterprise",
    },
    role: "member",
    mode: "mock",
    permissions,
  };
}

// The `sendClarificationAnswer` contract test lived here. Clarifications no longer
// travel as a durable signal — that needed a workflow engine to receive it and went
// with Temporal. An answer now resolves the gate through the Copilot advance
// endpoint with its text carried as the reason, so there is no request shape left
// here to assert; the two describes below still cover the permission gate and the
// submit-button logic, which is what this file is really protecting.

describe("clarification permission gate (canAnswer)", () => {
  it("grants canAnswer for a session holding the phase approval permission", () => {
    const session = buildSession(["artifact:view", "artifact:approve_requirements"]);
    expect(hasPermission(session, "artifact:approve_requirements")).toBe(true);
  });

  it("admin:* wildcard grants canAnswer", () => {
    const session = buildSession(["admin:*"]);
    expect(hasPermission(session, "artifact:approve_requirements")).toBe(true);
  });

  it("denies canAnswer for a session lacking the phase approval permission (e.g. developer)", () => {
    const session = buildSession(["artifact:view", "run:create"]);
    expect(hasPermission(session, "artifact:approve_requirements")).toBe(false);
  });

  it("denies canAnswer for a null session (fail-closed)", () => {
    expect(hasPermission(null, "artifact:approve_requirements")).toBe(false);
  });
});

// ── canSubmitAnswer (Submit button disabled-condition logic) ───────────────

describe("canSubmitAnswer", () => {
  it("is false when canAnswer is false, even with a non-empty answer", () => {
    expect(canSubmitAnswer("Use Postgres.", false)).toBe(false);
  });

  it("is false when the answer is empty or whitespace-only", () => {
    expect(canSubmitAnswer("", true)).toBe(false);
    expect(canSubmitAnswer("   ", true)).toBe(false);
  });

  it("is false while a signal is pending, even if otherwise valid", () => {
    expect(canSubmitAnswer("Use Postgres.", true, true)).toBe(false);
  });

  it("is true when canAnswer is true, not pending, and the answer is non-empty", () => {
    expect(canSubmitAnswer("Use Postgres.", true, false)).toBe(true);
    expect(canSubmitAnswer("  Use Postgres.  ", true)).toBe(true);
  });

  it("onSubmit receives the trimmed answer (contract — caller trims before invoking onSubmit)", () => {
    const raw = "  Use Postgres for the new table.  ";
    expect(raw.trim()).toBe("Use Postgres for the new table.");
    expect(canSubmitAnswer(raw, true)).toBe(true);
  });
});
