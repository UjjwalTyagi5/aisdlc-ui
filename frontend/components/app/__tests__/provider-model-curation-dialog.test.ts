import { describe, it, expect } from "vitest";

import { toggleModelForUnit } from "@/components/app/provider-model-curation-dialog";
import type { OrgModelGrant } from "@/lib/schemas/model";

describe("toggleModelForUnit", () => {
  it("turns a model ON for a BU with no existing entry for the provider", () => {
    const next = toggleModelForUnit([], "anthropic", "claude-sonnet", "bu-1");
    expect(next).toEqual([
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: null,
        visibility: "specific",
        businessUnitIds: ["bu-1"],
      },
    ]);
  });

  it("adds a BU to an existing specific entry, keeping the others", () => {
    const grants: OrgModelGrant[] = [
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: null,
        visibility: "specific",
        businessUnitIds: ["bu-1"],
      },
    ];
    const next = toggleModelForUnit(grants, "anthropic", "claude-sonnet", "bu-2");
    expect(next).toHaveLength(1);
    expect(next[0]!.businessUnitIds.sort()).toEqual(["bu-1", "bu-2"]);
    expect(next[0]!.visibility).toBe("specific");
  });

  it("removes the entry entirely when toggling off the last BU on a specific entry", () => {
    const grants: OrgModelGrant[] = [
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: null,
        visibility: "specific",
        businessUnitIds: ["bu-1"],
      },
    ];
    const next = toggleModelForUnit(grants, "anthropic", "claude-sonnet", "bu-1");
    expect(next).toEqual([]);
  });

  it("removes just one BU from a specific entry that has several, keeping the row", () => {
    const grants: OrgModelGrant[] = [
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: null,
        visibility: "specific",
        businessUnitIds: ["bu-1", "bu-2", "bu-3"],
      },
    ];
    const next = toggleModelForUnit(grants, "anthropic", "claude-sonnet", "bu-2");
    expect(next).toHaveLength(1);
    expect(next[0]!.businessUnitIds.sort()).toEqual(["bu-1", "bu-3"]);
  });

  it("is a no-op on a model that already has a global-visibility entry", () => {
    const grants: OrgModelGrant[] = [
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: null,
        visibility: "global",
        businessUnitIds: [],
      },
    ];
    const next = toggleModelForUnit(grants, "anthropic", "claude-sonnet", "bu-1");
    expect(next).toBe(grants);
  });

  it("leaves entries for other models and other providers untouched", () => {
    const grants: OrgModelGrant[] = [
      {
        provider: "anthropic",
        model_id: "claude-opus",
        credentialId: null,
        visibility: "specific",
        businessUnitIds: ["bu-1"],
      },
      {
        provider: "openai",
        model_id: "claude-sonnet", // same model_id, different provider — must not collide
        credentialId: null,
        visibility: "specific",
        businessUnitIds: ["bu-1"],
      },
    ];
    const next = toggleModelForUnit(grants, "anthropic", "claude-sonnet", "bu-1");
    expect(next).toHaveLength(3);
    expect(next).toEqual(
      expect.arrayContaining([
        grants[0],
        grants[1],
        {
          provider: "anthropic",
          model_id: "claude-sonnet",
          credentialId: null,
          visibility: "specific",
          businessUnitIds: ["bu-1"],
        },
      ]),
    );
  });

  it("does not mutate the input array", () => {
    const grants: OrgModelGrant[] = [
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: null,
        visibility: "specific",
        businessUnitIds: ["bu-1"],
      },
    ];
    const snapshot = JSON.parse(JSON.stringify(grants));
    toggleModelForUnit(grants, "anthropic", "claude-sonnet", "bu-2");
    expect(grants).toEqual(snapshot);
  });

  it("collapses more than one pre-existing specific entry for the same (provider, model) into one, unioning their units", () => {
    // No credential picker in this dialog, so two rows distinguished only by
    // credentialId (one per subscription) can't be told apart here.
    const grants: OrgModelGrant[] = [
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: "cred-a",
        visibility: "specific",
        businessUnitIds: ["bu-1"],
      },
      {
        provider: "anthropic",
        model_id: "claude-sonnet",
        credentialId: "cred-b",
        visibility: "specific",
        businessUnitIds: ["bu-2"],
      },
    ];
    const next = toggleModelForUnit(grants, "anthropic", "claude-sonnet", "bu-3");
    expect(next).toHaveLength(1);
    expect(next[0]!.businessUnitIds.sort()).toEqual(["bu-1", "bu-2", "bu-3"]);
  });
});
