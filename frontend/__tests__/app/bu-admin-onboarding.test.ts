/**
 * A Business Unit Admin onboards into their OWN unit, and nowhere else.
 *
 * This used to be impossible: `POST /onboarding` refused anyone without org-wide
 * authority, so a unit admin raised a `user_onboarding` request and waited for an
 * Organization Admin to press the button — an approval step over a decision inside a
 * unit nobody else administers.
 *
 * They now do it directly and name the working role in the same act, because they are
 * the person the `contributor` placeholder would otherwise have been waiting for.
 *
 * The fixture under test mirrors backend/shared/routers/onboarding.py branch for
 * branch, including the 404: a unit you do not administer is not confirmed to exist
 * by the error you get back. Its counterpart tests are in
 * backend/tests/test_onboarding.py.
 */
import { describe, expect, it } from "vitest";

import { onboardIntoOrganization } from "@/lib/mock/onboarding";
import { listMembershipsForIdentity } from "@/lib/mock/workspace-fixtures";
import { findOpenRoleAssignment } from "@/lib/mock/governance-approval-fixtures";
import { isUnitAssignableRole, ORG_ASSIGNABLE_ROLES, ROLE_META } from "@/lib/roles";
import { BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES } from "@/hooks/use-assignable-roles";

const PAYMENTS = "ws_payments";
const LENDING = "ws_lending";

/** A unit admin over Payments only — the array IS the authority being tested. */
const ADMINISTERS_PAYMENTS = [PAYMENTS];

let seq = 0;
const email = () => `bu-onboard-${(seq += 1)}@example.test`;

describe("a Business Unit Admin onboarding into their own unit", () => {
  it("creates the person with the role they chose, and nothing is left pending", () => {
    const addr = email();
    const { status, body } = onboardIntoOrganization({
      email: addr,
      role: "developer",
      workspaceId: PAYMENTS,
      administeredUnitIds: ADMINISTERS_PAYMENTS,
    });

    expect(status, JSON.stringify(body)).toBe(201);
    const result = body as { identityId: string };

    const memberships = listMembershipsForIdentity(result.identityId);
    expect(memberships.map((m) => m.workspaceId)).toContain(PAYMENTS);

    // The whole difference from the Org Admin's contributor path: no handover, because
    // the caller held the authority the handover would have asked for.
    expect(findOpenRoleAssignment(PAYMENTS, result.identityId)).toBeUndefined();
  });

  it("refuses a unit they do not administer, with 404 rather than 403", () => {
    const { status } = onboardIntoOrganization({
      email: email(),
      role: "developer",
      workspaceId: LENDING,
      administeredUnitIds: ADMINISTERS_PAYMENTS,
    });
    // 403 would confirm Lending exists, which is the cross-unit fact being withheld.
    expect(status).toBe(404);
  });

  it("requires a unit — leaving somebody unplaced is an organisation-level state", () => {
    const { status, body } = onboardIntoOrganization({
      email: email(),
      role: "developer",
      administeredUnitIds: ADMINISTERS_PAYMENTS,
    });
    expect(status).toBe(422);
    expect((body as { code: string }).code).toBe("unit_required");
  });

  it("cannot appoint another Business Unit Admin", () => {
    // An org-level appointment. A unit admin who could confer it could hand their own
    // unit to somebody else.
    const { status, body } = onboardIntoOrganization({
      email: email(),
      role: "bu_admin",
      workspaceId: PAYMENTS,
      administeredUnitIds: ADMINISTERS_PAYMENTS,
    });
    expect(status).toBe(422);
    expect((body as { code: string }).code).toBe("invalid_role");
  });

  it("cannot onboard a bare Contributor — it would file a request back to them", () => {
    const { status, body } = onboardIntoOrganization({
      email: email(),
      role: "contributor",
      workspaceId: PAYMENTS,
      administeredUnitIds: ADMINISTERS_PAYMENTS,
    });
    expect(status).toBe(422);
    expect((body as { code: string }).code).toBe("invalid_role");
  });

  it("accepts every role the dialog offers, and only those", () => {
    // The picker and the gate must agree. A role the dialog offers but the gate
    // refuses is a dead option; one the gate accepts but the dialog hides is a way in
    // that nobody reviewed.
    for (const role of BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES) {
      const { status, body } = onboardIntoOrganization({
        email: email(),
        role,
        workspaceId: PAYMENTS,
        administeredUnitIds: ADMINISTERS_PAYMENTS,
      });
      expect(status, `${role} was refused: ${JSON.stringify(body)}`).toBe(201);
    }

    for (const role of ORG_ASSIGNABLE_ROLES) {
      expect(isUnitAssignableRole(role), `${ROLE_META[role].label} is org-level`).toBe(false);
    }
  });
});

describe("the Organization Admin's path is untouched by the scoped one", () => {
  it("still refuses a working role", () => {
    const { status, body } = onboardIntoOrganization({
      email: email(),
      role: "developer",
      workspaceId: PAYMENTS,
      administeredUnitIds: null, // org-wide
    });
    expect(status).toBe(422);
    expect((body as { code: string }).code).toBe("invalid_role");
  });

  it("still places a Contributor and hands the role decision over", () => {
    const { status, body } = onboardIntoOrganization({
      email: email(),
      role: "contributor",
      workspaceId: PAYMENTS,
      administeredUnitIds: null,
    });
    expect(status, JSON.stringify(body)).toBe(201);
    const result = body as { identityId: string };
    expect(findOpenRoleAssignment(PAYMENTS, result.identityId)).toBeDefined();
  });

  it("defaults to the org-wide branch when no scope is supplied", () => {
    // The parameter is optional, and every existing caller omits it. Omitting it must
    // mean "org-wide", or those callers would silently change behaviour.
    const { status, body } = onboardIntoOrganization({
      email: email(),
      role: "developer",
      workspaceId: PAYMENTS,
    });
    expect(status).toBe(422);
    expect((body as { code: string }).code).toBe("invalid_role");
  });
});
