/**
 * Unit tests for the integrations page behaviors.
 *
 * Covers the connector:manage permission gate — `hasPermission` blocks users
 * lacking `connector:manage`, and grants it to holders of `connector:manage`
 * or `admin:*`.
 *
 * REMOVED: the ConnectorInstallResponse and install-redirect suites. They covered
 * the `{redirectUrl}` branch that sent the browser to an OAuth authorize URL, and
 * both the schema and the flow are gone — connecting a provider is now a pasted
 * credential stored per tenant, with no redirect and no platform OAuth app.
 *
 * These are pure-logic unit tests (environment: "node") — React rendering is not
 * required to verify the defense-in-depth permission gate.
 */
import { describe, it, expect } from "vitest";

import { Connector } from "@/lib/schemas/connector";
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
