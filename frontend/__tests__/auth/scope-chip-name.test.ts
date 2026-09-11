import { describe, expect, it } from "vitest";

import { scopeChipName } from "@/lib/scope";

/**
 * The scope chip, pinned per persona.
 *
 * "0 business units" was shipped three separate times, each by a different surface
 * deriving this rule for itself, and each wrong for a different person:
 *
 *   - Cost page and the app-wide scope bar counted `managedBusinessUnitIds`, which is
 *     empty for a Project Admin by design — they are bound INSIDE a unit but administer
 *     none — so the chip claimed they were nowhere.
 *   - The Projects page counted units present in the project LIST, so a newly appointed
 *     Business Unit Admin with no projects yet was told they had none.
 *
 * A chip reading zero is nearly always this bug rather than the truth: somebody with no
 * scope at all gets an empty state instead of a chip. These cases are the reason
 * `scopeChipName` exists as one function.
 */
const unit = (id: string, name: string) => ({ kind: "business_unit", scopeId: id, scopeName: name });
const project = (id: string, name: string) => ({ kind: "project", scopeId: id, scopeName: name });

describe("scopeChipName", () => {
  it("names the unit a Business Unit Admin runs, even with no projects in it", () => {
    expect(
      scopeChipName("business_unit", {
        isOrgWide: false,
        bindings: [unit("bu-1", "Agentic")],
        managedBusinessUnitIds: ["bu-1"],
      }),
    ).toBe("Agentic");
  });

  it("names the unit a Project Admin merely sits in, administering none", () => {
    // The regression: `managedBusinessUnitIds` is empty here and must not become a count.
    expect(
      scopeChipName("business_unit", {
        isOrgWide: false,
        bindings: [unit("bu-1", "Lending"), project("p-1", "Onboarding")],
        managedBusinessUnitIds: [],
      }),
    ).toBe("Lending");
  });

  it("counts units when there is more than one, rather than picking arbitrarily", () => {
    expect(
      scopeChipName("business_unit", {
        isOrgWide: false,
        bindings: [unit("bu-1", "Lending"), unit("bu-2", "Payments")],
        managedBusinessUnitIds: ["bu-1", "bu-2"],
      }),
    ).toBe("2 business units");
  });

  it("prefers the managed unit's name when one is managed among several", () => {
    expect(
      scopeChipName("business_unit", {
        isOrgWide: false,
        bindings: [unit("bu-1", "Lending"), unit("bu-2", "Payments")],
        managedBusinessUnitIds: ["bu-2"],
      }),
    ).toBe("Payments");
  });

  it("lets a caller override with a better name it already has", () => {
    // The Projects page: a single group heading names the unit the list came from.
    expect(
      scopeChipName("business_unit", {
        isOrgWide: false,
        bindings: [unit("bu-1", "Lending"), unit("bu-2", "Payments")],
        managedBusinessUnitIds: [],
        preferredName: "Payments",
      }),
    ).toBe("Payments");
  });

  it("names a sole project at project level", () => {
    expect(
      scopeChipName("project", {
        isOrgWide: false,
        bindings: [project("p-1", "Onboarding")],
        managedBusinessUnitIds: [],
      }),
    ).toBe("Onboarding");
  });

  it("says nothing for an org-wide viewer — the chip carries the tier instead", () => {
    expect(
      scopeChipName("business_unit", {
        isOrgWide: true,
        bindings: [],
        managedBusinessUnitIds: [],
      }),
    ).toBeNull();
  });

  it("never reports a zero count for somebody who is in a unit", () => {
    // The shape of every instance of this bug, asserted directly.
    for (const managed of [[], ["bu-1"]]) {
      const name = scopeChipName("business_unit", {
        isOrgWide: false,
        bindings: [unit("bu-1", "Agentic")],
        managedBusinessUnitIds: managed,
      });
      expect(name).not.toMatch(/^0 /);
    }
  });
});
