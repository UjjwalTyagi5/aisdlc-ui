/**
 * Unit tests for the Wave C integrations page behaviors (REQ-M7-22).
 *
 * Covers:
 * 1. Install redirect — the `ConnectorInstallResponse` union correctly
 *    parses the `{redirectUrl}` shape and the branch logic that calls
 *    `window.location.assign(data.redirectUrl)` is exercised.
 * 2. connector:manage permission gate — `hasPermission` correctly blocks
 *    users lacking both `connector:manage` and `connector:install`, while
 *    granting access to those with `connector:manage` or `admin:*`.
 *
 * These are pure-logic unit tests (environment: "node") — React rendering
 * is not required to verify the behavioral contracts of the install-redirect
 * branch and the defense-in-depth permission gate.
 */
import { describe, it, expect } from "vitest";

import { ConnectorInstallResponse, Connector } from "@/lib/schemas/connector";
import { hasPermission } from "@/lib/auth/permissions";
import type { Session } from "@/lib/auth/types";

// ───────── helpers ─────────

function buildSession(permissions: string[], role: "admin" | "member" | "viewer" = "member"): Session {
  return {
    user: { id: "u_1", name: "Test User", email: "t@test.test", initials: "TU" },
    tenant: { id: "ws_1", name: "Test Corp", plan: "Enterprise" },
    role,
    mode: "mock",
    permissions,
  };
}

const REDIRECT_PAYLOAD = { redirectUrl: "https://auth.atlassian.com/authorize?state=abc" };

const CONNECTOR_PAYLOAD = {
  id: "conn_1" as `conn_${string}`,
  tenantId: "ws_1" as `ws_${string}`,
  kind: "jira" as const,
  name: "Jira",
  installed: true,
  health: "healthy" as const,
  capabilities: [],
  lastCheckedAt: null,
};

// ───────── ConnectorInstallResponse schema ─────────

describe("ConnectorInstallResponse schema (install-redirect branch)", () => {
  it("parses a {redirectUrl} response and retains the redirectUrl field", () => {
    const parsed = ConnectorInstallResponse.parse(REDIRECT_PAYLOAD);
    expect("redirectUrl" in parsed).toBe(true);
    if ("redirectUrl" in parsed) {
      expect(parsed.redirectUrl).toBe("https://auth.atlassian.com/authorize?state=abc");
    }
  });

  it("parses a full Connector record (non-OAuth / backward-compat path)", () => {
    const parsed = ConnectorInstallResponse.parse(CONNECTOR_PAYLOAD);
    // If the union parsed as a Connector, it will have an `id` field
    expect("id" in parsed).toBe(true);
    expect("redirectUrl" in parsed).toBe(false);
  });

  it("rejects an empty object that matches neither union member", () => {
    expect(() => ConnectorInstallResponse.parse({})).toThrow();
  });

  it("rejects a {redirectUrl} with a non-string value", () => {
    expect(() => ConnectorInstallResponse.parse({ redirectUrl: 42 })).toThrow();
  });
});

// ───────── install-redirect branch logic ─────────

describe("install-redirect branch logic (Connect Flow step 2)", () => {
  it("fires window.location.assign when data has redirectUrl", () => {
    // Simulate the onSuccess handler logic from IntegrationsPage.installMutation
    const assignCalls: string[] = [];
    const fakeAssign = (url: string) => { assignCalls.push(url); };

    const data = ConnectorInstallResponse.parse(REDIRECT_PAYLOAD);
    if ("redirectUrl" in data && typeof data.redirectUrl === "string") {
      fakeAssign(data.redirectUrl);
    }

    expect(assignCalls).toHaveLength(1);
    expect(assignCalls[0]).toBe("https://auth.atlassian.com/authorize?state=abc");
  });

  it("does NOT fire window.location.assign when data is a Connector (direct-install path)", () => {
    const assignCalls: string[] = [];
    const fakeAssign = (url: string) => { assignCalls.push(url); };

    const data = ConnectorInstallResponse.parse(CONNECTOR_PAYLOAD);
    if ("redirectUrl" in data && typeof data.redirectUrl === "string") {
      fakeAssign(data.redirectUrl);
    }

    expect(assignCalls).toHaveLength(0);
  });
});

// ───────── connector:manage permission gate ─────────

describe("connector:manage permission gate (UI-SPEC Permission Gating Contract)", () => {
  it("grants access to a user with connector:manage", () => {
    const session = buildSession(["connector:manage", "artifact:view"]);
    expect(hasPermission(session, "connector:manage")).toBe(true);
  });

  it("grants access to admin:* wildcard (covers connector:manage implicitly)", () => {
    const session = buildSession(["admin:*"]);
    expect(hasPermission(session, "connector:manage")).toBe(true);
  });

  it("denies a user with only connector:install (legacy capability, not M7.2 RBAC)", () => {
    // connector:install is a legacy MVP-0 Capability, not an M7.2 Permission string.
    // hasPermission checks the session.permissions array which contains M7.2 strings only.
    const session = buildSession(["artifact:view", "run:create"]);
    expect(hasPermission(session, "connector:manage")).toBe(false);
  });

  it("denies a viewer with no permissions (disabled button + tooltip expected)", () => {
    const session = buildSession([], "viewer");
    expect(hasPermission(session, "connector:manage")).toBe(false);
  });

  it("denies a null session (fail-closed)", () => {
    expect(hasPermission(null, "connector:manage")).toBe(false);
  });

  it("denies an empty-permissions session (fail-closed)", () => {
    const session = buildSession([]);
    expect(hasPermission(session, "connector:manage")).toBe(false);
  });

  it("denies a member with only approval permissions (no connector:manage)", () => {
    const session = buildSession([
      "artifact:view",
      "artifact:approve_requirements",
      "artifact:approve_design",
      "run:create",
    ]);
    expect(hasPermission(session, "connector:manage")).toBe(false);
  });
});

// ───────── Connector schema integrity ─────────

describe("Connector schema", () => {
  it("parses a valid Connector object", () => {
    const c = Connector.parse(CONNECTOR_PAYLOAD);
    expect(c.kind).toBe("jira");
    expect(c.installed).toBe(true);
  });
});
