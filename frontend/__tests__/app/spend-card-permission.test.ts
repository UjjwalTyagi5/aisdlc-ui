import { describe, expect, it } from "vitest";

import { hasPermission } from "@/lib/auth/permissions";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import type { Session } from "@/lib/auth/types";

/**
 * SpendBreakdownCard renders on /projects and calls GET /cost/spend-series, which the
 * backend gates on `cost:view`. It had no permission check, so every role WITHOUT that
 * permission opened Projects and got a 403 rendered as a red "Couldn't load spend" box
 * — an error about money they were never meant to see, on a page whose actual content
 * loaded fine underneath it.
 *
 * This pins the population that was affected. If a role gains or loses cost:view the
 * numbers move, but the card must still be gated on the permission rather than on a
 * list of roles.
 */
function sessionFor(role: string): Session {
  return {
    user: { id: "u1", name: "T", email: "t@example.com", initials: "T" },
    tenant: { id: "t1", name: "T", plan: "org" },
    role: role as Session["role"],
    mode: "mock",
    tier: "org",
    permissions: ROLE_PERMISSIONS[role as keyof typeof ROLE_PERMISSIONS] ?? [],
  } as unknown as Session;
}

const ROLES = Object.keys(ROLE_PERMISSIONS);

describe("who may see the spend breakdown", () => {
  it("only these roles hold cost:view", () => {
    const holders = ROLES.filter((r) => hasPermission(sessionFor(r), "cost:view"));
    // org_admin reaches it through admin:*, not a literal grant.
    expect(new Set(holders)).toEqual(
      new Set(["org_admin", "bu_admin", "project_admin", "security_engineer"]),
    );
  });

  it("the delivery roles that open Projects do NOT, and must not be shown the card", () => {
    for (const role of ["developer", "ba", "architect", "qa", "devops_engineer",
                        "data_engineer", "scrum_master", "contributor"]) {
      expect(
        hasPermission(sessionFor(role), "cost:view"),
        `${role} must not be asked to load spend`,
      ).toBe(false);
    }
  });
});
