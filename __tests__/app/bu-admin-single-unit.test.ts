import { describe, expect, it } from "vitest";

import { buAdminElsewhere, scopeTierConflicts } from "@/lib/roles";

/**
 * "Runs one business unit" is the role's definition. Someone holding it twice
 * is an org-wide administrator without the title — accountable for two budgets
 * and able to move work between them with nobody above either.
 */
describe("a person administers at most one business unit", () => {
  const lendingAdmin = [{ scopeId: "ws_lending", scopeName: "Lending", role: "bu_admin" }];

  it("refuses a second business unit, and names the one they already run", () => {
    const clash = buAdminElsewhere(lendingAdmin, "ws_payments", "bu_admin");
    expect(clash?.scopeId).toBe("ws_lending");
    expect(clash?.scopeName).toBe("Lending");
  });

  it("allows re-granting in the unit they already administer", () => {
    // Not a second unit — the same one. Refusing would make the state
    // unreachable from itself.
    expect(buAdminElsewhere(lendingAdmin, "ws_lending", "bu_admin")).toBeNull();
  });

  it("allows a DELIVERY role in another unit", () => {
    // The point of the rule: presence elsewhere is ordinary and useful. Only
    // administering twice is the problem.
    for (const role of ["developer", "ba", "project_admin", "qa"]) {
      expect(buAdminElsewhere(lendingAdmin, "ws_payments", role)).toBeNull();
    }
  });

  it("says nothing about someone who administers nothing yet", () => {
    const contributor = [{ scopeId: "ws_payments", scopeName: "Payments", role: "developer" }];
    expect(buAdminElsewhere(contributor, "ws_lending", "bu_admin")).toBeNull();
  });
});

describe("the two rules are independent", () => {
  it("the tier rule still bites within one scope", () => {
    // Governance and delivery in the SAME unit — a different refusal, and one
    // the single-unit rule has nothing to say about.
    expect(
      scopeTierConflicts([{ scopeId: "ws_lending", roles: ["bu_admin", "developer"] }]),
    ).toEqual(["ws_lending"]);
    expect(buAdminElsewhere([], "ws_lending", "bu_admin")).toBeNull();
  });

  it("governance in one unit and delivery in another is fine on both rules", () => {
    const bindings = [{ scopeId: "ws_lending", scopeName: "Lending", role: "bu_admin" }];
    expect(buAdminElsewhere(bindings, "ws_payments", "developer")).toBeNull();
    expect(
      scopeTierConflicts([
        { scopeId: "ws_lending", roles: ["bu_admin"] },
        { scopeId: "ws_payments", roles: ["developer"] },
      ]),
    ).toEqual([]);
  });
});
