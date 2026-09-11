import { describe, expect, it } from "vitest";

import { isUnboundScope } from "@/lib/schemas/access-scope";

/**
 * "Bound to nothing" vs "bound to something empty" — the distinction that decides
 * which empty state a scoped page renders, and which was wrong in production.
 *
 * THE BUG THIS PINS. The projects page defined unbound as `projectIds.length === 0`
 * alone. A Business Unit Admin who had just been appointed — unit created, admin
 * appointed, no projects yet — therefore saw "You aren't assigned to any business units
 * or projects ... Ask your Business Unit Admin to add you to a project": the unit's own
 * administrator, told to ask themselves for access they already held, on a page whose
 * scope chip read "0 business units" while they administered one.
 *
 * Nothing failed. The backend was correct throughout, the page rendered without error,
 * and the only symptom was a sentence that was untrue. That is precisely the class of
 * defect a test has to catch, because no runtime check will.
 *
 * The rule: having an EMPTY scope is not the same as having NO scope, and the two need
 * opposite next steps — "create the first project" against "ask an admin to add you".
 */
const scope = (over: Partial<Parameters<typeof isUnboundScope>[0] & object> = {}) => ({
  isOrgWide: false,
  businessUnitIds: [] as string[],
  projectIds: [] as string[],
  ...over,
});

describe("isUnboundScope", () => {
  it("is false for a unit admin whose unit holds no projects yet", () => {
    // The reported bug, stated directly.
    expect(isUnboundScope(scope({ businessUnitIds: ["bu-1"], projectIds: [] }))).toBe(false);
  });

  it("is false for a contributor on a project but no unit", () => {
    expect(isUnboundScope(scope({ businessUnitIds: [], projectIds: ["p-1"] }))).toBe(false);
  });

  it("is true only when both sets are empty", () => {
    expect(isUnboundScope(scope())).toBe(true);
  });

  it("is false for an org-wide viewer regardless of the lists", () => {
    // An Organization Admin of a brand-new tenant has nothing in either list and is
    // emphatically not unbound — their authority is the role, not a membership row.
    expect(isUnboundScope(scope({ isOrgWide: true }))).toBe(false);
  });

  it("is false while the scope is unresolved", () => {
    // A request in flight or failed must never render as "you have no access". That is
    // the one wrong answer that looks authoritative — see useAccessScope's three states.
    expect(isUnboundScope(null)).toBe(false);
  });
});
