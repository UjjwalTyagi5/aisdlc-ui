import { ROLE_META, type PlatformRole } from "@/lib/roles";

import type { Session } from "./types";

/**
 * Resolves which of the platform's twelve roles (PRD §33.1 plus Scrum Master) the current
 * viewer is acting as, so the UI can present the correctly scoped version of
 * the fixed page set (PRD §15.1: "What changes by role is only which pages a
 * person can open and what they can do inside them").
 *
 * TWO PATHS, IN PRIORITY ORDER
 * ----------------------------
 * 1. AUTHORITATIVE — `session.platformRole`, when the backend issues it.
 * 2. INFERRED — otherwise, derived from the permissions the session carries.
 *
 * The backend session today issues only a coarse `role` ("admin" | "member" |
 * "viewer") plus a resolved permission list, so path 2 is the one that runs.
 * Inferring is sound because in the PRD model a role IS a permission bundle
 * (§14.10, §14.11) — but it is still an inference, and the authoritative value
 * must win the moment it exists.
 *
 * That switchover needs no code change: the day the backend adds
 * `platformRole` to the session payload, path 1 starts matching and the
 * inference below is simply never reached. Deleting the inference then becomes
 * optional cleanup rather than a coordinated release. Callers depend on this
 * function, never on which path produced the answer.
 *
 * IMPORTANT — presentation only. This decides what to *show*. It is never an
 * authorisation decision: `hasPermission()` remains the UX gate and the
 * backend remains the enforcement boundary.
 */

const has = (perms: string[], p: string) => perms.includes(p);

/**
 * Is this server-issued string one of the twelve catalogued roles (§33.1)?
 *
 * `Session.platformRole` is typed `string`, not `PlatformRole`, so a backend
 * that ships a new or misspelled role can never break session decoding. This
 * guard is where that loose value is checked before it is trusted — anything
 * unrecognised falls through to the inference instead of escaping as a role
 * the UI has no `ROLE_META` entry for (which would throw at every label
 * lookup).
 */
function isPlatformRole(value: string): value is PlatformRole {
  return Object.prototype.hasOwnProperty.call(ROLE_META, value);
}

export function effectivePlatformRole(session: Session | null): PlatformRole | null {
  if (!session) return null;

  // Path 1 — the backend stated the role outright. Trust it over any
  // inference we could make from the permission bundle.
  const issued = session.platformRole;
  if (issued && isPlatformRole(issued)) return issued;

  // Path 2 — infer from permissions.
  const perms = session.permissions ?? [];

  // ── Governance tier (PRD §14.6) ───────────────────────────────────────────
  // settings:manage is Organization-Admin-only (§14.10), and admin:* is the
  // wildcard only that role holds.
  if (has(perms, "admin:*") || has(perms, "settings:manage")) return "org_admin";

  // Business Unit Admin: governance over its own unit — role/project
  // management, but never org-wide settings. NOT model:manage — Project
  // Admin holds that too now (onboards model credentials within its BU), so
  // it no longer uniquely identifies this tier.
  if (has(perms, "role:manage") || has(perms, "workspace:manage")) {
    return "bu_admin";
  }

  // ── Delivery tier ─────────────────────────────────────────────────────────
  // Project Admin is the only delivery role holding member:manage (§14.1).
  if (has(perms, "member:manage")) return "project_admin";

  // Contributor roles, identified by the gate each one owns (§14.7).
  // Ordered most-specific first: Security Engineer is the one contributor with
  // standing audit + trace access (§15.9), so it is checked before the others.
  if (has(perms, "audit:view") && has(perms, "trace:view")) return "security_engineer";
  if (has(perms, "artifact:approve_deployment")) return "devops_engineer";
  if (has(perms, "artifact:approve_testing")) return "qa";
  if (has(perms, "artifact:approve_design")) return "architect";
  if (has(perms, "artifact:approve_requirements")) return "ba";

  // Builds but never approves — the defining Developer constraint (§15.7).
  if (has(perms, "run:create")) return "developer";

  // Holds artifact:view and nothing that identifies a built-in role — this is
  // what a composed custom bundle looks like (§14.9, e.g. "Stakeholder").
  if (has(perms, "artifact:view")) return "custom";

  return null;
}

/** Display label for the viewer's role, e.g. for the profile chip. */
export function effectiveRoleLabel(session: Session | null): string | null {
  const role = effectivePlatformRole(session);
  return role ? ROLE_META[role].label : null;
}

/** True when the viewer is governance-tier — governs, never builds (§14.6). */
export function isGovernanceRole(session: Session | null): boolean {
  const role = effectivePlatformRole(session);
  return role ? ROLE_META[role].governanceOnly : false;
}

/**
 * The scope a Dashboard should summarise for this viewer (PRD §36):
 * organization-wide, one business unit, or the person's own projects.
 */
export type DashboardScope = "organization" | "business_unit" | "project";

export function dashboardScope(session: Session | null): DashboardScope {
  const role = effectivePlatformRole(session);
  if (role === "org_admin") return "organization";
  if (role === "bu_admin") return "business_unit";
  return "project";
}

/**
 * The dashboard VARIANT — a finer distinction than `dashboardScope`.
 *
 * Scope answers "how much data", which is only three answers because a Project
 * Admin and a Developer on the same project see the same rows. Variant answers
 * "what is this person's job on that data", which is a fourth: the Project Admin
 * owns budget, membership and every fallback approval on their projects, while a
 * contributor owns the stage in front of them and should land on work, not
 * governance. Same filtered data, different lead.
 *
 * `dashboardScope` is kept as-is rather than widened, because its three values
 * are exactly the right granularity for the QUERY scope its other callers use.
 */
export type DashboardVariant =
  | "organization"
  | "business_unit"
  | "project_admin"
  | "contributor";

export function dashboardVariant(session: Session | null): DashboardVariant {
  const role = effectivePlatformRole(session);
  if (role === "org_admin") return "organization";
  if (role === "bu_admin") return "business_unit";
  if (role === "project_admin") return "project_admin";
  return "contributor";
}
