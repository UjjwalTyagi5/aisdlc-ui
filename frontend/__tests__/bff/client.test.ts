/**
 * Unit tests for bearerForRequest — the enterprise/local bearer-source branch
 * in bff/client.ts (milestone-7.3 plan 03, D-01 BFF token-forwarding seam).
 *
 * Why: Verify that in enterprise OIDC mode the BFF forwards the Auth0 RS256
 * access token, and in local/mock mode it mints HS256 via mintBffToken —
 * critically, that getAuth0() is NEVER called in mock/local dev (Pitfall 6 /
 * USER STANDING DIRECTIVE: mock login → /projects must stay green).
 *
 * Confirmed A5: Auth0Client.getAccessToken() (App Router path) returns
 * Promise<{ token: string; ... }> in @auth0/nextjs-auth0 v4.22.0.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Session } from "@/lib/auth/types";

const MOCK_SESSION: Session = {
  user: {
    id: "u_test_01",
    name: "Test User",
    email: "test@acme.test",
    initials: "TU",
  },
  tenant: {
    id: "ws_test_tenant",
    name: "Test Corp",
    plan: "Enterprise",
  },
  role: "member",
  mode: "mock",
  permissions: ["artifact:view"],
};

const MOCK_HS256_TOKEN = "mock.hs256.token";
const MOCK_AUTH0_TOKEN = "mock.rs256.access.token.from.auth0";

// Module mocks — declared before any imports of the tested module so Vitest
// hoists them above the module under test.
vi.mock("@/lib/bff/jwt", () => ({
  mintBffToken: vi.fn().mockResolvedValue(MOCK_HS256_TOKEN),
}));

const mockGetAccessToken = vi.fn().mockResolvedValue({ token: MOCK_AUTH0_TOKEN });
vi.mock("@/lib/auth/auth0", () => ({
  getAuth0: vi.fn(() => ({ getAccessToken: mockGetAccessToken })),
}));

describe("bearerForRequest", () => {
  // Reset modules AND clear call counts between tests so mode flags
  // re-evaluate cleanly and spy call counts don't bleed across tests.
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockGetAccessToken.mockResolvedValue({ token: MOCK_AUTH0_TOKEN });
  });

  describe("local / mock mode (isOidcEnabled=false)", () => {
    it("calls mintBffToken and returns HS256 token", async () => {
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: false,
        isMockAuth: true,
        AUTH_MODE: "mock",
        isAuth0: false,
        isLocalAuth: false,
      }));

      const { mintBffToken } = await import("@/lib/bff/jwt");
      const { bearerForRequest } = await import("@/lib/bff/client");

      const token = await bearerForRequest(MOCK_SESSION);

      expect(token).toBe(MOCK_HS256_TOKEN);
      expect(mintBffToken).toHaveBeenCalledWith(MOCK_SESSION);
    });

    it("never calls getAuth0() in mock mode (Pitfall 6 lock)", async () => {
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: false,
        isMockAuth: true,
        AUTH_MODE: "mock",
        isAuth0: false,
        isLocalAuth: false,
      }));

      const { getAuth0 } = await import("@/lib/auth/auth0");
      const { bearerForRequest } = await import("@/lib/bff/client");

      await bearerForRequest(MOCK_SESSION);

      expect(getAuth0).not.toHaveBeenCalled();
    });

    it("falls back to HS256 when AUTH_MODE=auth0 but ENABLE_OIDC=false (SC#4 rollback)", async () => {
      // ENABLE_OIDC=false must force the HS256 path even when AUTH_MODE=auth0.
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: false,
        isMockAuth: false,
        AUTH_MODE: "auth0",
        isAuth0: true,
        isLocalAuth: false,
      }));

      const { mintBffToken } = await import("@/lib/bff/jwt");
      const { getAuth0 } = await import("@/lib/auth/auth0");
      const { bearerForRequest } = await import("@/lib/bff/client");

      const token = await bearerForRequest(MOCK_SESSION);

      expect(token).toBe(MOCK_HS256_TOKEN);
      expect(mintBffToken).toHaveBeenCalledWith(MOCK_SESSION);
      expect(getAuth0).not.toHaveBeenCalled();
    });
  });

  describe("local mode (the path that used to mint from a forgeable cookie)", () => {
    it("forwards the stored backend token and never mints", async () => {
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: false,
        isMockAuth: false,
        AUTH_MODE: "local",
        isAuth0: false,
        isLocalAuth: true,
      }));
      vi.doMock("next/headers", () => ({
        cookies: async () => ({ get: () => ({ value: "backend-issued-token" }) }),
      }));

      const { mintBffToken } = await import("@/lib/bff/jwt");
      const { bearerForRequest } = await import("@/lib/bff/client");

      const token = await bearerForRequest(MOCK_SESSION);

      expect(token).toBe("backend-issued-token");
      // The whole point: the session's permissions never become a signed claim.
      expect(mintBffToken).not.toHaveBeenCalled();
    });

    it("refuses the request when there is no stored token rather than minting one", async () => {
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: false,
        isMockAuth: false,
        AUTH_MODE: "local",
        isAuth0: false,
        isLocalAuth: true,
      }));
      vi.doMock("next/headers", () => ({
        cookies: async () => ({ get: () => undefined }),
      }));

      const { mintBffToken } = await import("@/lib/bff/jwt");
      const { bearerForRequest } = await import("@/lib/bff/client");

      await expect(bearerForRequest(MOCK_SESSION)).rejects.toMatchObject({ status: 401 });
      expect(mintBffToken).not.toHaveBeenCalled();
    });
  });

  describe("enterprise OIDC mode (isOidcEnabled=true, !isMockAuth)", () => {
    it("calls getAuth0().getAccessToken() and returns the Auth0 RS256 token", async () => {
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: true,
        isMockAuth: false,
        AUTH_MODE: "auth0",
        isAuth0: true,
        isLocalAuth: false,
      }));

      const { getAuth0 } = await import("@/lib/auth/auth0");
      const { bearerForRequest } = await import("@/lib/bff/client");

      const token = await bearerForRequest(MOCK_SESSION);

      expect(token).toBe(MOCK_AUTH0_TOKEN);
      expect(getAuth0).toHaveBeenCalled();
      expect(mockGetAccessToken).toHaveBeenCalled();
    });

    it("never calls mintBffToken in enterprise OIDC mode", async () => {
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: true,
        isMockAuth: false,
        AUTH_MODE: "auth0",
        isAuth0: true,
        isLocalAuth: false,
      }));

      const { mintBffToken } = await import("@/lib/bff/jwt");
      const { bearerForRequest } = await import("@/lib/bff/client");

      await bearerForRequest(MOCK_SESSION);

      expect(mintBffToken).not.toHaveBeenCalled();
    });

    it("does NOT call getAuth0() when isMockAuth=true even if isOidcEnabled=true", async () => {
      // Guards the edge case where AUTH_MODE=mock takes priority over ENABLE_OIDC=true.
      vi.doMock("@/lib/auth/mode", () => ({
        isOidcEnabled: true,
        isMockAuth: true,
        AUTH_MODE: "mock",
        isAuth0: false,
        isLocalAuth: false,
      }));

      const { getAuth0 } = await import("@/lib/auth/auth0");
      const { bearerForRequest } = await import("@/lib/bff/client");

      await bearerForRequest(MOCK_SESSION);

      expect(getAuth0).not.toHaveBeenCalled();
    });
  });
});
