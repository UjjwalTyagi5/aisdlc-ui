/**
 * What "verified" is allowed to mean.
 *
 * Onboarding a provider without a key became legitimate — the organisation
 * registers the vendor so its models can be granted, and each Business Unit
 * brings its own secret. That opened a hole in the probe: verification stamped
 * every connection `valid` with a fresh timestamp, key or no key, so a
 * credential that cannot make a single call showed a green card reading
 * "verified just now". Downstream nothing would run, because credential checks
 * test `hasKey` — which is exactly the shape of bug a status field exists to
 * prevent and instead was causing.
 *
 * These pin the two halves: a key can be verified, and the absence of one
 * cannot be.
 */
import { describe, it, expect, afterEach } from "vitest";

import {
  createModelProvider,
  deleteModelProvider,
  getModelGrantMatrix,
  getModelProvider,
  updateModelProvider,
  verifyModelProvider,
} from "@/lib/mock/model-fixtures";

const ORG_ADMIN = { role: "org_admin" as const, displayName: "Organization Admin" };

/** Fixtures are module-level and shared, so every row made here is removed. */
const made: string[] = [];
afterEach(() => {
  for (const id of made.splice(0)) deleteModelProvider(id);
});

function onboard(apiKey: string) {
  const p = createModelProvider(
    {
      provider: "xai",
      display_name: `xAI — ${apiKey ? "keyed" : "keyless"} test`,
      api_key: apiKey,
      models: [{ model_id: "grok-4" }],
      workspaceId: null,
    },
    ORG_ADMIN,
  );
  made.push(p.id);
  return p;
}

describe("verifying a provider connection", () => {
  it("records a real probe against a key", () => {
    const created = onboard("sk-test-123");
    expect(created.hasKey).toBe(true);

    const result = verifyModelProvider(created.id);
    expect(result?.status).toBe("valid");
    expect(getModelProvider(created.id)?.last_verified_at).toBeTruthy();
  });

  it("refuses to call a keyless connection verified", () => {
    const created = onboard("");
    expect(created.hasKey).toBe(false);

    const result = verifyModelProvider(created.id);
    expect(result?.status).toBe("unverified");
    // No timestamp either: "verified — never" is the honest reading, and a date
    // beside an unverified status invites the eye to trust the date.
    expect(getModelProvider(created.id)?.last_verified_at).toBeNull();
  });

  it("verifies once a key is added to a connection that had none", () => {
    const created = onboard("");
    updateModelProvider(created.id, { api_key: "sk-added-later" });
    expect(getModelProvider(created.id)?.hasKey).toBe(true);

    expect(verifyModelProvider(created.id)?.status).toBe("valid");
  });

  it("un-verifies when the key is cleared, rather than keeping the old verdict", () => {
    const created = onboard("sk-test-123");
    verifyModelProvider(created.id);
    updateModelProvider(created.id, { api_key: "" });

    // The stored verdict proved a secret this connection no longer holds.
    expect(verifyModelProvider(created.id)?.status).toBe("unverified");
    expect(getModelProvider(created.id)?.last_verified_at).toBeNull();
  });
});

/**
 * What the grant matrix is allowed to imply by naming a credential.
 *
 * Once a provider could be registered with no key, "a subscription serves this
 * model" stopped meaning "this model can run". The screen names the credential
 * in a column of its own, so without a separate signal it would print a
 * perfectly ordinary subscription name beside a model nobody can call — the
 * granted-but-inert state, rendered as if it were working.
 */
describe("naming the credential that serves a model", () => {
  const rows = getModelGrantMatrix().rows;
  const row = (modelId: string) => rows.find((r) => r.model_id === modelId);

  it("flags a subscription that holds no key, while still naming it", () => {
    const bedrock = row("anthropic.claude-sonnet-4-6-v1:0");
    expect(bedrock?.credentialName).toBeTruthy();
    expect(bedrock?.credentialHasKey).toBe(false);
    // Granted to everyone AND unusable by everyone — both halves have to survive.
    expect(bedrock?.granted).toBe(true);
    expect(bedrock?.centrallyCredentialed).toBe(false);
  });

  it("does not flag a unit-scoped key, which is keyed and merely not central", () => {
    const openai = row("gpt-5.1");
    expect(openai?.credentialHasKey).toBe(true);
    // centrallyCredentialed is false here too, which is exactly why it cannot
    // be the thing the warning keys on.
    expect(openai?.centrallyCredentialed).toBe(false);
  });

  it("reports no credential at all as absent rather than keyless", () => {
    const ungoverned = row("gemini-3-flash");
    expect(ungoverned?.credentialName).toBeNull();
    expect(ungoverned?.credentialHasKey).toBeNull();
  });
});
