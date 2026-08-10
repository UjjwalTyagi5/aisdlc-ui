import type { Role, Session } from "./types";

/**
 * Shape of the FastAPI `POST /auth/login` response (Phase 3 local auth).
 * Mirrors `LoginOut` in `shared/routers/auth_local.py`.
 */
export interface LoginResponse {
  token: string;
  tier: "platform" | "org";
  user_id: string;
  tenant_id: string | null;
  permissions: string[];
}

/**
 * Derive the coarse MVP-0 role from the resolved permission set so the existing
 * `role`/capability UI keeps working alongside the M7.2 `permissions` model.
 * `admin:*` (org) and `platform:*` (vendor) both map to admin.
 */
function deriveRole(permissions: string[]): Role {
  if (permissions.includes("admin:*") || permissions.includes("platform:*")) {
    return "admin";
  }
  const isMember = permissions.some(
    (p) => p === "run:create" || p.startsWith("artifact:approve_"),
  );
  return isMember ? "member" : "viewer";
}

function initialsFrom(email: string): string {
  const local = email.split("@", 1)[0] ?? "";
  const parts = local.split(/[._\-+]+/).filter(Boolean);
  if (parts.length >= 2) {
    return ((parts[0]![0] ?? "") + (parts[1]![0] ?? "")).toUpperCase() || "?";
  }
  return (local.slice(0, 2) || "?").toUpperCase();
}

/**
 * Build the canonical `Session` from a successful login response + the email the
 * user typed. Reuses the existing `sdlc_session` cookie machinery
 * (encode/decode in `mock.ts`) so the app shell and the BFF minter consume it
 * unchanged — `mintBffToken` reads `user.id` / `tenant.id` / `permissions`.
 */
export function buildLocalSession(resp: LoginResponse, email: string): Session {
  const isPlatform = resp.tier === "platform";
  return {
    user: {
      id: resp.user_id,
      name: email.split("@", 1)[0] ?? email,
      email,
      initials: initialsFrom(email),
    },
    tenant: {
      // Platform admins are tenant-LESS; "" yields an empty tenant_id claim,
      // which the backend's tenant-less platform path expects.
      id: resp.tenant_id ?? "",
      name: isPlatform ? "Platform" : "Workspace",
      plan: isPlatform ? "Platform" : "Enterprise",
    },
    role: deriveRole(resp.permissions),
    mode: "local",
    tier: resp.tier,
    permissions: resp.permissions,
  };
}
