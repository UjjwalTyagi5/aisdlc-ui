import { describe, expect, it } from "vitest";

import { AGENT_DEFAULT_OWNER_ROLE, canPublishAtTier } from "@/lib/governance";
import type { ProfileScope } from "@/lib/schemas/agent-profiles";

/**
 * The Org → Business Unit → Project → Personal cascade, from the publishing
 * side. `canPublishAtTier` is enforced in three places (the Agent Studio UI,
 * the Next route and the MSW handler), so it is worth pinning here rather than
 * asserting the same thing three times over.
 */
describe("canPublishAtTier", () => {
  it("gives each shared tier exactly one owner", () => {
    expect(canPublishAtTier("org_admin", "org")).toBe(true);
    expect(canPublishAtTier("bu_admin", "workspace")).toBe(true);
    expect(canPublishAtTier("project_admin", "project")).toBe(true);
  });

  it("refuses a tier you don't own, in either direction", () => {
    // Upward: a BU Admin does not publish the organization's default. They
    // propose to it — see GOVERNANCE_APPROVER_ROLE.agent_default_org.
    expect(canPublishAtTier("bu_admin", "org")).toBe(false);
    expect(canPublishAtTier("project_admin", "workspace")).toBe(false);
    // Downward: a tier below yours is its own owner's call, not yours to
    // overwrite. This is the direction a permission check alone would miss,
    // because the governance roles hold `admin:*`.
    expect(canPublishAtTier("org_admin", "workspace")).toBe(false);
    expect(canPublishAtTier("org_admin", "project")).toBe(false);
    expect(canPublishAtTier("bu_admin", "project")).toBe(false);
  });

  it("gives delivery roles a personal tier and the governance roles none", () => {
    expect(canPublishAtTier("developer", "user")).toBe(true);
    expect(canPublishAtTier("ba", "user")).toBe(true);
    // A Project Admin owns BOTH their project's default and their own personal
    // override — the two answer different questions.
    expect(canPublishAtTier("project_admin", "user")).toBe(true);
    expect(canPublishAtTier("project_admin", "project")).toBe(true);

    // Neither governance role runs an agent, so a personal override for them
    // would be instructions that can never take effect.
    expect(canPublishAtTier("org_admin", "user")).toBe(false);
    expect(canPublishAtTier("bu_admin", "user")).toBe(false);
  });

  it("refuses an unresolved role rather than defaulting open", () => {
    for (const scope of ["org", "workspace", "project", "user"] as ProfileScope[]) {
      expect(canPublishAtTier(null, scope)).toBe(false);
    }
  });

  it("stays in step with the owner map it derives from", () => {
    // Guards the shared tiers against the two drifting: a new owner in
    // AGENT_DEFAULT_OWNER_ROLE must not leave this function behind.
    for (const scope of ["org", "workspace", "project"] as ProfileScope[]) {
      const owner = AGENT_DEFAULT_OWNER_ROLE[scope];
      expect(owner).not.toBeNull();
      expect(canPublishAtTier(owner, scope)).toBe(true);
    }
  });
});
