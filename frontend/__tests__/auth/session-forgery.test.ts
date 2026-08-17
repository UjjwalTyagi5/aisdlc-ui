/**
 * The session cookie must not be able to assert who you are or what you may do.
 *
 * Until 2026-08-17 it could. `sdlc_session` was unsigned base64 with
 * `httpOnly: false`, and `mintBffToken` re-signed its `permissions` array into the
 * JWT FastAPI trusts verbatim — so setting the cookie to `["admin:*"]` produced a
 * correctly-signed organization-admin token for any tenant whose id you knew, and
 * the entire backend RBAC layer became decorative.
 *
 * These tests pin the two properties that close it: a forged cookie establishes no
 * session, and the minter is unreachable outside mock mode.
 *
 * See finding 1 in docs/rbac-audit-2026-08-17.md.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { encodeSession } from "@/lib/auth/mock";
import type { Session } from "@/lib/auth/types";
import { verifyBackendToken } from "@/lib/auth/token";

// The tracked .env ships JWT_SECRET_KEY empty (it is a per-deployment secret), and
// verifyBackendToken throws rather than verifying against an empty key. Supply one
// for the suite so these tests exercise the verifier rather than that guard.
const TEST_SECRET = "test-signing-secret-for-vitest";
beforeEach(() => vi.stubEnv("JWT_SECRET_KEY", TEST_SECRET));
afterEach(() => vi.unstubAllEnvs());

/** Exactly what an attacker would put in the cookie. */
function forgedAdminSession(): Session {
  return {
    user: { id: "attacker", name: "A", email: "a@evil.example.org", initials: "A" },
    tenant: { id: "00000000-e2e0-4000-a000-000000000001", name: "Acme", plan: "Enterprise" },
    role: "admin",
    mode: "local",
    permissions: ["admin:*"],
  };
}

describe("the session cookie is not a credential", () => {
  it("a forged session blob is not a valid backend token", async () => {
    // The forged cookie is well-formed and decodes cleanly — that was never the
    // problem. What matters is that it carries no signature, so the token
    // verifier rejects it outright rather than reading its permissions.
    const forged = encodeSession(forgedAdminSession());
    expect(await verifyBackendToken(forged)).toBeNull();
  });

  it("rejects a token signed with the wrong key", async () => {
    const { SignJWT } = await import("jose");
    const wrongKey = new TextEncoder().encode("not-the-server-secret");
    const token = await new SignJWT({
      tenant_id: "00000000-e2e0-4000-a000-000000000001",
      permissions: ["admin:*"],
    })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("attacker")
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(wrongKey);

    expect(await verifyBackendToken(token)).toBeNull();
  });

  it("rejects an expired token even though its signature is good", async () => {
    const { SignJWT } = await import("jose");
    const key = new TextEncoder().encode(process.env["JWT_SECRET_KEY"]!);
    const token = await new SignJWT({ tenant_id: "t", permissions: ["admin:*"] })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("someone")
      .setExpirationTime(Math.floor(Date.now() / 1000) - 60)
      .sign(key);

    expect(await verifyBackendToken(token)).toBeNull();
  });

  it("accepts a genuine token and reports its claims", async () => {
    // The negative cases above would all pass against a verifier that always
    // returned null, so pin the positive one too.
    const { SignJWT } = await import("jose");
    const key = new TextEncoder().encode(process.env["JWT_SECRET_KEY"]!);
    const token = await new SignJWT({ tenant_id: "tenant-1", permissions: ["run:create"] })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("real-user")
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(key);

    const claims = await verifyBackendToken(token);
    expect(claims).not.toBeNull();
    expect(claims!.sub).toBe("real-user");
    expect(claims!.tenant_id).toBe("tenant-1");
    expect(claims!.permissions).toEqual(["run:create"]);
  });

  it("treats absent and malformed tokens the same as invalid ones", async () => {
    // One failure value, deliberately — a caller must not be able to distinguish
    // "no token" from "bad token" and branch differently on them.
    expect(await verifyBackendToken(undefined)).toBeNull();
    expect(await verifyBackendToken("")).toBeNull();
    expect(await verifyBackendToken("not.a.jwt")).toBeNull();
  });
});

describe("mintBffToken is forbidden in local mode", () => {
  beforeEach(() => vi.resetModules());

  it("throws in local mode rather than signing a client-supplied permission set", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "local");
    const { mintBffToken } = await import("@/lib/bff/jwt");

    await expect(mintBffToken(forgedAdminSession())).rejects.toThrow(
      /not permitted in local mode/,
    );
  });

  it("still mints in mock mode, which has no backend to issue a token", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "mock");
    const { mintBffToken } = await import("@/lib/bff/jwt");

    const token = await mintBffToken(forgedAdminSession());
    expect(token.split(".")).toHaveLength(3);
  });

  it("still mints in auth0 mode, where the session is built from a verified IdP session", async () => {
    // The SC#4 rollback: AUTH_MODE=auth0 with ENABLE_OIDC=false. Permissions there
    // come from the backend's own resolver via GET /auth/permissions, not from a
    // cookie, so this path was never the vulnerable one.
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "auth0");
    const { mintBffToken } = await import("@/lib/bff/jwt");

    const token = await mintBffToken(forgedAdminSession());
    expect(token.split(".")).toHaveLength(3);
  });
});
