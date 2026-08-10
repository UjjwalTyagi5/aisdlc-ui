/**
 * The four counts across the top of Models.
 *
 * The trap this guards: a model can be granted twice — Sonnet reaches everyone
 * on the shared platform key and Lending alone on the EU gateway. Counting
 * grants would report more models than the organisation has, so a reader sees a
 * total larger than the estate. The same applies to providers, which have one
 * row per (model, subscription) and so appear many times over.
 */
import { describe, it, expect } from "vitest";

import { countModelGovernance } from "@/components/app/model-governance-summary";
import { getModelGrantMatrix } from "@/lib/mock/model-fixtures";
import type { ModelGrantMatrixRow } from "@/lib/schemas/model";

const row = (over: Partial<ModelGrantMatrixRow>): ModelGrantMatrixRow => ({
  provider: "anthropic",
  model_id: "m",
  credentialId: "prov_1",
  credentialName: "Sub",
  credentialHasKey: true,
  granted: true,
  visibility: "global",
  centrallyCredentialed: true,
  units: [],
  ...over,
});

describe("counting the model estate", () => {
  it("counts a model once however many subscriptions serve it", () => {
    const counts = countModelGovernance([
      row({ model_id: "sonnet", credentialId: "a", visibility: "global" }),
      row({ model_id: "sonnet", credentialId: "b", visibility: "specific" }),
    ]);
    expect(counts.onboarded).toBe(1);
  });

  it("counts a provider once however many models and keys it carries", () => {
    // The matrix has a row per (model, subscription); a vendor with two keys
    // and three models appears six times and is still one provider.
    const counts = countModelGovernance([
      row({ provider: "anthropic", model_id: "sonnet", credentialId: "a" }),
      row({ provider: "anthropic", model_id: "opus", credentialId: "a" }),
      row({ provider: "anthropic", model_id: "sonnet", credentialId: "b" }),
      row({ provider: "openai", model_id: "gpt", credentialId: "c" }),
    ]);
    expect(counts.providers).toBe(2);
  });

  it("counts a twice-granted model as org-wide when either grant is global", () => {
    // Everyone can already use Sonnet via the shared key; a second, narrower
    // key does not take that away.
    const counts = countModelGovernance([
      row({ model_id: "sonnet", credentialId: "a", visibility: "specific" }),
      row({ model_id: "sonnet", credentialId: "b", visibility: "global" }),
    ]);
    expect(counts.global).toBe(1);
    expect(counts.onboarded).toBe(1);
  });

  it("never reports more org-wide models than models", () => {
    const counts = countModelGovernance([
      row({ model_id: "a", visibility: "global" }),
      row({ model_id: "b", visibility: "specific" }),
      row({ model_id: "c", granted: false, visibility: null }),
    ]);
    expect(counts.global).toBeLessThanOrEqual(counts.onboarded);
    expect(counts.global).toBe(1);
  });

  it("ignores catalogue models nobody has onboarded", () => {
    const counts = countModelGovernance([
      row({
        model_id: "unowned",
        credentialId: null,
        credentialName: null,
        credentialHasKey: null,
        granted: false,
        visibility: null,
      }),
    ]);
    expect(counts.onboarded).toBe(0);
    // And an un-onboarded catalogue entry is not evidence of a provider.
    expect(counts.providers).toBe(0);
  });

  it("still counts a keyless model — this row reports size, not health", () => {
    // Onboarded and granted org-wide; that it cannot run is a fact the
    // provider's status pill and the detail table's "Holds no key" carry,
    // against the subscription you would have to open to fix it.
    const counts = countModelGovernance([
      row({ model_id: "bedrock-sonnet", credentialHasKey: false, visibility: "global" }),
    ]);
    expect(counts.onboarded).toBe(1);
    expect(counts.global).toBe(1);
  });

  it("counts an ungranted model as onboarded but not org-wide", () => {
    const counts = countModelGovernance([
      row({ model_id: "x", credentialHasKey: false, granted: false, visibility: null }),
    ]);
    expect(counts.onboarded).toBe(1);
    expect(counts.global).toBe(0);
  });
});

describe("against the seeded estate", () => {
  const counts = countModelGovernance(getModelGrantMatrix().rows);

  it("reports a plausible estate on real fixtures, not just hand-built rows", () => {
    expect(counts.providers).toBeGreaterThan(0);
    expect(counts.onboarded).toBeGreaterThan(0);
    // More models than vendors, and no bucket larger than the whole.
    expect(counts.onboarded).toBeGreaterThanOrEqual(counts.providers);
    expect(counts.global).toBeLessThanOrEqual(counts.onboarded);
  });
});
