import { describe, expect, it } from "vitest";

import { AgentProfileVersion } from "@/lib/schemas/agent-profiles";

/**
 * The client's idea of a profile version must be the server's.
 *
 * It was not: the schema required `published_by` and `published_at`, which
 * `agent_profiles` has no column for and `_version_dict` therefore never sends.
 * Every response failed validation, and saving a draft in Agent Studio reported
 * "Server returned an unexpected shape" for a write the backend had already
 * committed — the worst kind of error message, because it describes a failure
 * that did not happen.
 */
describe("AgentProfileVersion", () => {
  /** Exactly the keys backend/shared/routers/agent_profiles.py::_version_dict emits. */
  const fromServer = {
    id: "3f1c4a4e-0f2e-4a77-9a2d-1d0b6f2a9c11",
    version: 3,
    is_active: false,
    prompt_prepend: "PAYMENTS house rules…",
    prompt_append: "",
    output_contract_extra: "",
    created_by: "someone@company.com",
    created_at: "2026-09-23T09:00:00Z",
    updated_at: "2026-09-23T09:00:00Z",
  };

  it("parses what the server actually sends", () => {
    const parsed = AgentProfileVersion.parse(fromServer);
    expect(parsed.version).toBe(3);
    expect(parsed.published_by ?? null).toBeNull();
  });

  it("still accepts a publisher once the server records one", () => {
    const parsed = AgentProfileVersion.parse({
      ...fromServer,
      published_by: "approver@company.com",
      published_at: "2026-09-23T10:00:00Z",
    });
    expect(parsed.published_by).toBe("approver@company.com");
  });

  it("rejects a response missing a field that does exist", () => {
    const { version: _dropped, ...withoutVersion } = fromServer;
    expect(() => AgentProfileVersion.parse(withoutVersion)).toThrow();
  });
});
