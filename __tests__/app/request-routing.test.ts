/**
 * The request ladder.
 *
 * These assert the rules PRD §33.2 states in prose, because every one of them
 * is invisible in the UI until it is wrong: an approver who should not have
 * been offered the decision, a request that stalled with nobody, or an
 * initiator quietly approving their own ask.
 */
import { describe, it, expect } from "vitest";

import {
  approverChainFrom,
  canDecideRequest,
  canEscalate,
  canRaiseRequest,
  canRaiseType,
  initialApproverRole,
  nextApproverRole,
  raisableTypesFor,
} from "@/lib/requests/routing";

describe("what each tier may ask for", () => {
  // The rule the three lists encode: you request what you cannot grant
  // yourself. Each assertion below is a case where a flat list got it wrong.
  it("does not offer a contributor asks that are not theirs to make", () => {
    expect(canRaiseType("developer", "project_creation")).toBe(false);
    expect(canRaiseType("developer", "budget_increase")).toBe(false);
    expect(canRaiseType("developer", "model_provider_access")).toBe(false);
  });

  it("routes a model request to whoever can actually grant it", () => {
    // `model_credential` used to be TYPE-routed to the Business Unit Admin,
    // which skipped the one person who can satisfy a contributor's ask by
    // ticking a box: the Project Admin owns the project's model selection.
    // Tier routing lands both cases — and a Project Admin's own ask, which is
    // for a model the unit was never granted, still climbs to the BU Admin.
    expect(initialApproverRole("model_credential", "developer")).toBe("project_admin");
    expect(initialApproverRole("model_credential", "ba")).toBe("project_admin");
    expect(initialApproverRole("model_credential", "project_admin")).toBe("bu_admin");
  });

  it("offers a contributor the things they genuinely cannot do", () => {
    for (const t of ["access_request", "model_credential", "mcp_server", "user_onboarding"] as const) {
      expect(canRaiseType("developer", t)).toBe(true);
    }
  });

  it("gives the Project Admin project-shaped asks, not org-wide ones", () => {
    expect(canRaiseType("project_admin", "project_creation")).toBe(true);
    expect(canRaiseType("project_admin", "budget_increase")).toBe(true);
    // Onboarding a provider org-wide is not a Project Admin's ask.
    expect(canRaiseType("project_admin", "model_provider_access")).toBe(false);
  });

  // A BU Admin can already grant a model to a project in their own unit, so
  // the project-scoped credential is not something they need to request.
  it("moves the Business Unit Admin's model ask up to provider access", () => {
    expect(canRaiseType("bu_admin", "model_provider_access")).toBe(true);
    expect(canRaiseType("bu_admin", "model_credential")).toBe(false);
  });

  it("offers the Organization Admin nothing — they only receive", () => {
    expect(raisableTypesFor("org_admin")).toEqual([]);
    expect(raisableTypesFor(null)).toEqual([]);
  });

  it("routes provider access to the Org Admin whoever asks", () => {
    expect(initialApproverRole("model_provider_access", "bu_admin")).toBe("org_admin");
    expect(initialApproverRole("model_provider_access", "project_admin")).toBe("org_admin");
  });
});

describe("who may raise a request", () => {
  it("excludes the Organization Admin — nothing sits above them to decide it", () => {
    expect(canRaiseRequest("org_admin")).toBe(false);
  });

  it("allows every other tier, governance and delivery alike", () => {
    for (const role of ["bu_admin", "project_admin", "developer", "ba", "qa"] as const) {
      expect(canRaiseRequest(role)).toBe(true);
    }
  });

  it("refuses an unresolved role rather than defaulting open", () => {
    expect(canRaiseRequest(null)).toBe(false);
  });
});

describe("the upward ladder", () => {
  it("climbs Project Admin → BU Admin → Org Admin", () => {
    expect(nextApproverRole("project_admin")).toBe("bu_admin");
    expect(nextApproverRole("bu_admin")).toBe("org_admin");
  });

  it("ends at the Organization Admin", () => {
    expect(nextApproverRole("org_admin")).toBeNull();
    expect(canEscalate("org_admin")).toBe(false);
  });

  // The whole point of the request lane's fallback: it never stalls.
  it("enters at the bottom for any delivery contributor", () => {
    for (const role of ["developer", "ba", "architect", "qa", "security_engineer"] as const) {
      expect(nextApproverRole(role)).toBe("project_admin");
    }
  });

  it("exposes the full remaining route, so an escalation is never a surprise", () => {
    expect(approverChainFrom("project_admin")).toEqual([
      "project_admin",
      "bu_admin",
      "org_admin",
    ]);
    expect(approverChainFrom("org_admin")).toEqual(["org_admin"]);
  });
});

describe("initial approver", () => {
  it("routes a hand-raised request one tier above the requester", () => {
    expect(initialApproverRole("access_request", "developer")).toBe("project_admin");
    expect(initialApproverRole("access_request", "project_admin")).toBe("bu_admin");
    expect(initialApproverRole("access_request", "bu_admin")).toBe("org_admin");
  });

  it("routes a type-routed request by its type, whoever asked", () => {
    expect(initialApproverRole("project_creation", "developer")).toBe("bu_admin");
    expect(initialApproverRole("project_creation", "project_admin")).toBe("bu_admin");
  });

  // "No one approves their own request — it escalates instead" (§33.2).
  it("climbs past the requester when the type would route back to them", () => {
    // project_creation routes to bu_admin; a BU Admin filing one would
    // otherwise become their own approver.
    expect(initialApproverRole("project_creation", "bu_admin")).toBe("org_admin");
    // budget_increase routes to org_admin; an Org Admin cannot raise at all,
    // but the guard must not depend on that being checked elsewhere.
    expect(initialApproverRole("budget_increase", "org_admin")).toBeNull();
  });
});

describe("who may decide", () => {
  const base = {
    currentApproverRole: "bu_admin" as const,
    requestedById: "idn_marcus",
    status: "pending_review",
    viewerRole: "bu_admin" as const,
    viewerIdentityId: "idn_noah",
  };

  it("allows the role currently holding it", () => {
    expect(canDecideRequest(base).allowed).toBe(true);
  });

  it("blocks the initiator even when their role matches", () => {
    const r = canDecideRequest({ ...base, viewerIdentityId: "idn_marcus" });
    expect(r.allowed).toBe(false);
    expect(r.reason).toMatch(/escalates rather than self-approving/i);
  });

  it("blocks a different role and names who it is waiting on", () => {
    const r = canDecideRequest({ ...base, viewerRole: "project_admin" });
    expect(r.allowed).toBe(false);
    expect(r.reason).toMatch(/Business Unit Admin/i);
  });

  it("blocks a closed request", () => {
    for (const status of ["approved", "rejected", "cancelled", "draft"]) {
      expect(canDecideRequest({ ...base, status }).allowed).toBe(false);
    }
  });

  it("allows deciding an escalated request — it is still open", () => {
    expect(
      canDecideRequest({ ...base, status: "escalated", currentApproverRole: "bu_admin" }).allowed,
    ).toBe(true);
  });
});
