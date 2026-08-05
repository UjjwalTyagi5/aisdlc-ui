/**
 * hasPermission — frontend mirror of the backend's `has_permission` (D-01,
 * single source of truth: agentic_app/shared/authz/permissions.py).
 *
 * This is a PARALLEL SIBLING to `capabilities.ts::can()`, not a replacement —
 * the legacy MVP-0 Role/Capability model stays untouched (additive closure,
 * REQ-M7-12 frontend half / milestone-7.2 plan 05).
 *
 * IMPORTANT — defense-in-depth only: this is UX gating, NOT a security
 * boundary. The authoritative check is the backend `require_permission`
 * dependency (and the Temporal `_check_permission_for_phase` signal guard).
 * A user who tampers with client state to reveal a hidden action still gets
 * a 403 from FastAPI on the actual call (T-7.2-25, accepted).
 */
import type { Phase } from "@/lib/schemas/enums";
import type { PlatformRole } from "@/lib/roles";

import type { Permission, Session } from "./types";

/**
 * Returns true when `session` carries `perm` directly OR the `admin:*`
 * wildcard — mirroring backend `has_permission(perms, required)` EXACTLY:
 * `required in perms or "admin:*" in perms`.
 *
 * Fail-closed: a null session or a session with no permissions returns false.
 */
export function hasPermission(session: Session | null, perm: string): boolean {
  if (!session) return false;
  const perms = session.permissions;
  if (!perms || perms.length === 0) return false;
  return perms.includes(perm) || perms.includes("admin:*");
}

/**
 * Maps a frontend `Phase` to its backend approval permission string —
 * mirrors the backend `_PHASE_PERMISSION` map (agentic_app/shared/authz/permissions.py)
 * plus the `deployment` entry that exists in `ALL_PERMISSIONS` (owned by `sre_lead`,
 * forward-compat for the future deployment phase per D-07).
 *
 * `review` has NO backend approval permission — `_PHASE_PERMISSION` omits it
 * and no role is ever granted an `artifact:approve_review`. Returning a
 * never-granted sentinel string keeps `hasPermission` fail-closed for this
 * phase (false for everyone except `admin:*`) without throwing or returning
 * a permission a non-admin could plausibly hold.
 */
export function approvePermissionForPhase(phase: Phase | string): string {
  switch (phase) {
    case "requirements":
      return "artifact:approve_requirements" satisfies Permission;
    case "design":
      return "artifact:approve_design" satisfies Permission;
    case "development":
      return "artifact:approve_development" satisfies Permission;
    case "testing":
      return "artifact:approve_testing" satisfies Permission;
    case "deployment":
      return "artifact:approve_deployment" satisfies Permission;
    case "review":
    default:
      // SAFE-DENY sentinel: not in backend ALL_PERMISSIONS / _PHASE_PERMISSION,
      // so no non-admin role can ever hold it — hasPermission stays fail-closed.
      return "artifact:approve_review";
  }
}

/**
 * May this role create a project?
 *
 * A ROLE CHECK, NOT A PERMISSION CHECK, and that is the whole point. The
 * permission `project:create` is held by `bu_admin` and `project_admin`
 * alone — the Organization Admin was never granted it. They passed anyway,
 * because they hold `admin:*` and `hasPermission` treats that as a wildcard
 * over everything.
 *
 * The wildcard is right for most of what it covers: an Org Admin genuinely
 * can do the things below them. This is one of the few cases where it is
 * wrong, because project creation is not an administrative capability they
 * happen to outrank — it is a step in a flow that belongs to someone else.
 * A project is created inside a Business Unit, against that unit's budget,
 * with that unit's members and grants (PRD §15.2: the Org Admin creates
 * business units and appoints their admins; the unit runs itself from
 * there). An Org Admin creating one has to guess at all four.
 *
 * Enforced in the API route and the MSW handler as well as the button —
 * project creation had NO server-side check at all, so hiding the button was
 * the entire control.
 */
export function canCreateProject(role: PlatformRole | null): boolean {
  return role === "bu_admin" || role === "project_admin";
}
