import { describe, it, expect } from "vitest";

import { unusableModelKeys } from "@/components/app/project-model-selection-card";

/**
 * The card lists the GRANT cascade and used to present it as the models the project
 * "actually runs on" — its own subtitle's words. Those are different sets.
 *
 * Observed live: an Organization Admin granted four Anthropic models to a business
 * unit, the Business Unit Admin keyed a provider connection with only one of them
 * enabled, and the card showed all four identically while the Overview picker offered
 * exactly one. Nothing on the screen explained the difference.
 */
describe("unusableModelKeys", () => {
  const granted = [
    { provider: "anthropic", model_id: "claude-haiku-4-5" },
    { provider: "anthropic", model_id: "claude-opus-4-6" },
    { provider: "anthropic", model_id: "claude-opus-4-7-20260416" },
    { provider: "anthropic", model_id: "claude-sonnet-4-5" },
  ];

  it("flags every granted model with no runnable offering behind it", () => {
    // The real shape of the reported case: four granted, one keyed.
    const unusable = unusableModelKeys(granted, [
      { provider: "anthropic", model_id: "claude-sonnet-4-5" },
    ]);
    expect([...unusable].sort()).toEqual([
      "anthropic::claude-haiku-4-5",
      "anthropic::claude-opus-4-6",
      "anthropic::claude-opus-4-7-20260416",
    ]);
  });

  it("flags nothing when every granted model is runnable", () => {
    expect(unusableModelKeys(granted, granted).size).toBe(0);
  });

  it("flags everything when no offering is keyed at all", () => {
    expect(unusableModelKeys(granted, []).size).toBe(4);
  });

  it("claims nothing while the options query is still in flight", () => {
    // undefined is NOT the same as []. An unresolved request is not evidence of a
    // missing key, and marking every row mid-load is worse than saying nothing.
    expect(unusableModelKeys(granted, undefined).size).toBe(0);
  });

  it("matches on provider AND model, not model alone", () => {
    // The same model id under a different provider is a different offering, and must
    // not be counted as satisfying the grant.
    const unusable = unusableModelKeys([{ provider: "anthropic", model_id: "m-1" }], [
      { provider: "azure", model_id: "m-1" },
    ]);
    expect([...unusable]).toEqual(["anthropic::m-1"]);
  });

  it("ignores runnable offerings that were never granted", () => {
    // /model/options is already intersected with the cascade server-side, so this
    // should not happen — but an extra option must never mask a genuinely missing one.
    const unusable = unusableModelKeys(
      [{ provider: "anthropic", model_id: "granted-only" }],
      [{ provider: "anthropic", model_id: "some-other-model" }],
    );
    expect([...unusable]).toEqual(["anthropic::granted-only"]);
  });

  it("handles an empty grant list", () => {
    expect(unusableModelKeys([], []).size).toBe(0);
  });
});
