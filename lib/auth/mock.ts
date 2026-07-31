import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { personaIdentityFor } from "./persona-identity";
import { ROLE_PERMISSIONS } from "./role-permissions";
import type { Role, Session } from "./types";

export const MOCK_COOKIE_NAME = "sdlc_session";
export const MOCK_COOKIE_MAX_AGE = 60 * 60 * 8; // 8 hours

const SAMPLE_USERS: Record<Role, Session["user"]> = {
  admin: {
    id: "u_admin",
    name: "Ada Lovelace",
    email: "ada@acme.test",
    initials: "AL",
  },
  member: {
    id: "u_member",
    name: "Grace Hopper",
    email: "grace@acme.test",
    initials: "GH",
  },
  viewer: {
    id: "u_viewer",
    name: "Alan Turing",
    email: "alan@acme.test",
    initials: "AT",
  },
};

const SAMPLE_TENANT: Session["tenant"] = {
  // Must be a real UUID: in real-API mode the BFF mints this as the JWT `tenant_id`
  // claim, and FastAPI scopes UUID-typed `tenant_id` columns by it. A non-UUID like
  // "ws_acme" makes Postgres throw `invalid input syntax for type uuid` (500). This
  // matches the E2E bootstrap tenant seeded by agentic_app/scripts/seed_e2e_fixtures.py.
  id: "00000000-e2e0-4000-a000-000000000001",
  name: "Acme Inc.",
  plan: "Enterprise",
};

/**
 * Role -> M7.2 permission strings for mock-mode sessions (REQ-M7-12 frontend
 * half / milestone-7.2 plan 05). Mock mode has no backend resolver to call —
 * these are role-seeded analogs of the real DB-resolved sets so `hasPermission`
 * gating is exercisable in local/mock dev without a live RBAC stack.
 *
 * admin -> the `admin:*` wildcard (passes every check, mirrors backend).
 * member -> a representative working set incl. run:create/artifact:view/approve_*.
 * viewer -> the read-only floor only (artifact:view), nothing gated.
 */
const MOCK_PERMISSIONS: Record<Role, readonly string[]> = {
  admin: ["admin:*"],
  member: [
    "run:create",
    "artifact:view",
    "artifact:approve_requirements",
    "artifact:approve_design",
    "artifact:approve_development",
    "artifact:approve_testing",
  ],
  viewer: ["artifact:view"],
};

export function buildMockSession(role: Role): Session {
  return {
    user: SAMPLE_USERS[role],
    tenant: SAMPLE_TENANT,
    role,
    mode: "mock",
    permissions: [...MOCK_PERMISSIONS[role]],
  };
}

function initialsFromLabel(label: string): string {
  const words = label.replace(/[()/]/g, " ").trim().split(/\s+/);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase() || "?";
}

/**
 * Build a session for one of the platform's twelve roles (`lib/roles.ts`)
 * directly — as opposed to `buildMockSession()`'s coarse admin/member/viewer,
 * which only lets `effectivePlatformRole()` *infer* a platform role from a
 * representative permission set. Setting `platformRole` here is the
 * authoritative path (`effective-role.ts` path 1), so every platform role is
 * actually reachable at sign-in for manual testing — not just the handful
 * the coarse roles happen to infer to.
 */
export function buildMockSessionForPlatformRole(role: PlatformRole): Session {
  const meta = ROLE_META[role];
  return {
    user: {
      id: `u_${role}`,
      name: meta.label,
      email: `${role}@acme.test`,
      initials: initialsFromLabel(meta.label),
    },
    tenant: SAMPLE_TENANT,
    role: meta.governanceOnly ? "admin" : "member",
    mode: "mock",
    permissions: [...ROLE_PERMISSIONS[role]],
    platformRole: role,
    // Bind the persona to a seeded person so it has real Business Unit and
    // project memberships to be scoped BY — otherwise a "Business Unit Admin"
    // sign-in belongs to no unit, and "show only your unit" degenerates into
    // "show everything". See lib/auth/persona-identity.ts.
    identityId: personaIdentityFor(role) ?? undefined,
  };
}

/**
 * Base64-url encode so the cookie value is URL-safe and parseable
 * from both Node (server components) and Edge (middleware) runtimes.
 */
export function encodeSession(s: Session): string {
  const json = JSON.stringify(s);
  // `btoa` is available in Edge + Node 18+
  return btoa(unescape(encodeURIComponent(json)));
}

export function decodeSession(raw: string | undefined | null): Session | null {
  if (!raw) return null;
  try {
    const json = decodeURIComponent(escape(atob(raw)));
    const parsed = JSON.parse(json) as Session;
    if (!parsed?.user?.id || !parsed.role) return null;
    return parsed;
  } catch {
    return null;
  }
}
