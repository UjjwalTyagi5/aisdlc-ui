/**
 * The onboarding response contract, pinned against real backend bodies.
 *
 * `OnboardingResult` was written against `lib/mock/onboarding` and never matched what
 * FastAPI returned once the BFF became a passthrough: the schema wanted
 * identityId/displayName/initials/membershipStatus/notifiedBusinessUnitAdmin and the
 * endpoint sent userId/created/roleRequestId. Five fields adrift, so EVERY successful
 * onboarding threw "response did not match schema" — the request had worked, the account
 * existed, and the dialog reported failure.
 *
 * Nothing caught it because both halves were internally consistent and nothing compared
 * them. The bodies below are copied verbatim from `POST /onboarding` responses, so this
 * fails the moment the two drift again.
 */
import { describe, it, expect } from "vitest";

import { OnboardingResult } from "@/lib/schemas/onboarding";

/** Verbatim from the backend: a Contributor placed in a unit, SMTP unconfigured. */
const CONTRIBUTOR_RESPONSE = {
  identityId: "53138083-2478-4f5e-8280-8e545680201b",
  email: "farah@abcbank.com",
  displayName: "Farah",
  initials: "FA",
  workspaceId: "99225895-bc25-4680-9102-c6ef166ff551",
  role: "contributor",
  membershipStatus: "invited",
  notifiedBusinessUnitAdmin: false,
  invited: false,
  created: true,
  roleRequestId: "44a8f1c0-3e07-4a13-93f2-8c4d558993d5",
};

/** A Business Unit Admin appointed before anyone decided which unit they run. */
const BU_ADMIN_NO_UNIT_RESPONSE = {
  ...CONTRIBUTOR_RESPONSE,
  role: "bu_admin",
  workspaceId: null,
  membershipStatus: null,
  notifiedBusinessUnitAdmin: false,
  roleRequestId: null,
};

describe("OnboardingResult matches what the backend actually sends", () => {
  it("accepts a Contributor placed in a unit", () => {
    const parsed = OnboardingResult.safeParse(CONTRIBUTOR_RESPONSE);
    expect(parsed.success ? null : parsed.error.issues).toBeNull();
  });

  it("accepts a Business Unit Admin appointed with no unit", () => {
    const parsed = OnboardingResult.safeParse(BU_ADMIN_NO_UNIT_RESPONSE);
    expect(parsed.success ? null : parsed.error.issues).toBeNull();
  });

  it("requires the fields the dialog actually renders", () => {
    // displayName goes straight into the toast and notifiedBusinessUnitAdmin picks
    // which sentence follows it, so neither may quietly become optional — that is how
    // "undefined added to Payments" ships.
    for (const field of ["identityId", "displayName", "notifiedBusinessUnitAdmin"]) {
      const withoutIt = { ...CONTRIBUTOR_RESPONSE } as Record<string, unknown>;
      delete withoutIt[field];
      expect(
        OnboardingResult.safeParse(withoutIt).success,
        `${field} must stay required`,
      ).toBe(false);
    }
  });

  it("tolerates a backend that predates the invite fields", () => {
    // `invited`/`created`/`roleRequestId` are optional so a frontend deployed ahead of
    // the backend still parses rather than failing every onboarding.
    const older = { ...CONTRIBUTOR_RESPONSE } as Record<string, unknown>;
    delete older.invited;
    delete older.created;
    delete older.roleRequestId;
    expect(OnboardingResult.safeParse(older).success).toBe(true);
  });

  it("keeps membershipStatus to the one value that means anything", () => {
    // "invited" or null. An arbitrary string here would let a backend report a
    // membership state the UI has no branch for.
    expect(
      OnboardingResult.safeParse({ ...CONTRIBUTOR_RESPONSE, membershipStatus: "active" })
        .success,
    ).toBe(false);
  });
});
