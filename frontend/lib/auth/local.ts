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
  /** Organization display name. Optional — older backends do not send it. */
  tenant_name?: string | null;
}

/**
 * Derive the coarse MVP-0 role from the resolved permission set so the existing
 * `role`/capability UI keeps working alongside the M7.2 `permissions` model.
 *
 * A freshly self-registered user has NO bindings and therefore no permissions at
 * all, which lands here as "viewer" — the least-privileged rung. That is correct:
 * the coarse role is presentation only, and every real gate reads `permissions`,
 * which is empty until an admin grants a role.
 */
function deriveRole(permissions: string[]): Role {
  if (permissions.includes("admin:*")) {
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
  return {
    user: {
      id: resp.user_id,
      name: email.split("@", 1)[0] ?? email,
      email,
      initials: initialsFrom(email),
    },
    tenant: {
      // Every account belongs to the one organization, so tenant_id is always set.
      // "" is kept as the fallback rather than throwing: an empty tenant claim
      // resolves to zero permissions server-side, which fails closed.
      id: resp.tenant_id ?? "",
      name: resp.tenant_name ?? "Organization",
      plan: "Enterprise",
    },
    role: deriveRole(resp.permissions),
    mode: "local",
    tier: resp.tier,
    permissions: resp.permissions,
  };
}
