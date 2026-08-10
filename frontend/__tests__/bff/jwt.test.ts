/**
 * Unit tests for mintBffToken — RED phase (written before implementation exists).
 *
 * Why: Verify that the BFF JWT minter produces a FastAPI-acceptable HS256 token
 * with the correct claims, and fails closed when JWT_SECRET_KEY is unset.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { jwtVerify } from "jose";
import type { Session } from "@/lib/auth/types";

const MOCK_SESSION: Session = {
  user: {
    id: "u_test_01",
    name: "Test User",
    email: "test@acme.test",
    initials: "TU",
  },
  tenant: {
    id: "ws_test",
    name: "Test Corp",
    plan: "Enterprise",
  },
  role: "member",
  mode: "mock",
  permissions: ["artifact:view", "run:create"],
};

const TEST_SECRET = "test-secret-key-minimum-32-bytes-length!!";

describe("mintBffToken", () => {
  const originalSecret = process.env["JWT_SECRET_KEY"];

  beforeEach(() => {
    process.env["JWT_SECRET_KEY"] = TEST_SECRET;
  });

  afterEach(() => {
    if (originalSecret === undefined) {
      delete process.env["JWT_SECRET_KEY"];
    } else {
      process.env["JWT_SECRET_KEY"] = originalSecret;
    }
  });

  it("returns a string JWT", async () => {
    const { mintBffToken } = await import("@/lib/bff/jwt");
    const token = await mintBffToken(MOCK_SESSION);
    expect(typeof token).toBe("string");
    expect(token.split(".").length).toBe(3);
  });

  it("sets sub claim to session.user.id", async () => {
    const { mintBffToken } = await import("@/lib/bff/jwt");
    const token = await mintBffToken(MOCK_SESSION);
    const key = new TextEncoder().encode(TEST_SECRET);
    const { payload } = await jwtVerify(token, key);
    expect(payload.sub).toBe(MOCK_SESSION.user.id);
  });

  it("sets tenant_id claim to session.tenant.id", async () => {
    const { mintBffToken } = await import("@/lib/bff/jwt");
    const token = await mintBffToken(MOCK_SESSION);
    const key = new TextEncoder().encode(TEST_SECRET);
    const { payload } = await jwtVerify(token, key);
    expect((payload as Record<string, unknown>)["tenant_id"]).toBe(
      MOCK_SESSION.tenant.id,
    );
  });

  it("sets permissions claim to session.permissions (REQ-M7-12)", async () => {
    const { mintBffToken } = await import("@/lib/bff/jwt");
    const token = await mintBffToken(MOCK_SESSION);
    const key = new TextEncoder().encode(TEST_SECRET);
    const { payload } = await jwtVerify(token, key);
    expect((payload as Record<string, unknown>)["permissions"]).toEqual(
      MOCK_SESSION.permissions,
    );
  });

  it("sets exp claim approximately 60 min in the future", async () => {
    const before = Math.floor(Date.now() / 1000);
    const { mintBffToken } = await import("@/lib/bff/jwt");
    const token = await mintBffToken(MOCK_SESSION);
    const after = Math.floor(Date.now() / 1000);
    const key = new TextEncoder().encode(TEST_SECRET);
    const { payload } = await jwtVerify(token, key);
    expect(payload.exp).toBeDefined();
    const exp = payload.exp as number;
    // exp should be between now+55min and now+65min (generous window for test timing)
    expect(exp).toBeGreaterThanOrEqual(before + 55 * 60);
    expect(exp).toBeLessThanOrEqual(after + 65 * 60);
  });

  it("signs with HS256 algorithm", async () => {
    const { mintBffToken } = await import("@/lib/bff/jwt");
    const token = await mintBffToken(MOCK_SESSION);
    const header = JSON.parse(
      Buffer.from(token.split(".")[0]!, "base64url").toString("utf8"),
    );
    expect(header.alg).toBe("HS256");
  });

  it("throws a clear error when JWT_SECRET_KEY is unset", async () => {
    delete process.env["JWT_SECRET_KEY"];
    // Re-import to pick up env change (vitest does not cache across module reloads by default)
    const { mintBffToken } = await import("@/lib/bff/jwt");
    await expect(mintBffToken(MOCK_SESSION)).rejects.toThrow(
      /JWT_SECRET_KEY/,
    );
  });

  it("does not expose JWT_SECRET_KEY via NEXT_PUBLIC_ prefix", () => {
    // This is a static assertion — env vars prefixed NEXT_PUBLIC_ are embedded in the
    // browser bundle; the secret must never carry that prefix.
    const envKeys = Object.keys(process.env).filter((k) =>
      k.startsWith("NEXT_PUBLIC_"),
    );
    const leaked = envKeys.filter(
      (k) => k.includes("JWT") || k.includes("SECRET"),
    );
    expect(leaked).toHaveLength(0);
  });
});
