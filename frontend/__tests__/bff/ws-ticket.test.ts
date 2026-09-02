/**
 * Regression test for mintWsTicket — found live during Development agent
 * verification (2026-08-31): the chat WS bridge (app/api/chat/route.ts),
 * the Orchestrator Copilot WS bridge, and the runs SSE stream bridge all
 * share this one function, and it called mintBffToken directly instead of
 * the bearerForRequest() branch every other BFF->FastAPI call already uses
 * (lib/bff/client.ts, __tests__/bff/client.test.ts). mintBffToken throws by
 * design in local mode (lib/bff/jwt.ts), so every real-time chat/stream
 * connection was broken in local auth mode -- silently, since
 * app/api/chat/route.ts's POST still returns 200 even when the WS bridge
 * fails to open. Never caught before because every prior chat-access test
 * exercised the backend WS handler directly, never through this Next.js
 * bridge.
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

const mockFetch = vi.fn();

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.stubGlobal("fetch", mockFetch);
  mockFetch.mockResolvedValue({
    ok: true,
    json: async () => ({ ticket: "the-ticket", ttl_seconds: 20 }),
  });
});

describe("mintWsTicket", () => {
  it("in local mode, forwards the stored backend token and never calls mintBffToken", async () => {
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
    vi.mock("@/lib/bff/jwt", () => ({
      mintBffToken: vi.fn().mockRejectedValue(
        new Error("mintBffToken is not permitted in local mode."),
      ),
    }));

    const { mintBffToken } = await import("@/lib/bff/jwt");
    const { mintWsTicket } = await import("@/lib/bff/ws-ticket");

    const ticket = await mintWsTicket(MOCK_SESSION);

    expect(ticket).toBe("the-ticket");
    expect(mintBffToken).not.toHaveBeenCalled();

    // The Authorization header FastAPI's /auth/ws-ticket receives must carry
    // the real forwarded token, not anything mintBffToken would have produced.
    const [, requestInit] = mockFetch.mock.calls[0] as [string, RequestInit];
    const headers = requestInit.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer backend-issued-token");
  });

  it("in enterprise OIDC mode, still resolves a ticket via the Auth0 token path", async () => {
    vi.doMock("@/lib/auth/mode", () => ({
      isOidcEnabled: true,
      isMockAuth: false,
      AUTH_MODE: "auth0",
      isAuth0: true,
      isLocalAuth: false,
    }));
    vi.doMock("@/lib/auth/auth0", () => ({
      getAuth0: vi.fn(() => ({
        getAccessToken: vi.fn().mockResolvedValue({ token: "auth0-access-token" }),
      })),
    }));

    const { mintWsTicket } = await import("@/lib/bff/ws-ticket");
    const ticket = await mintWsTicket(MOCK_SESSION);

    expect(ticket).toBe("the-ticket");
    const [, requestInit] = mockFetch.mock.calls[0] as [string, RequestInit];
    const headers = requestInit.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer auth0-access-token");
  });
});
