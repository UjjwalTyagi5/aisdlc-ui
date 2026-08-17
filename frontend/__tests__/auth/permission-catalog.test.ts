/**
 * The permission catalogue's own invariants.
 *
 * The one that matters is the first: **the role that owns a gate can pass it.** Three
 * of the eight pipeline gates failed it — `artifact:approve_code_review`,
 * `_security` and `_documentation` were granted to no role, so Code Review, Security
 * and Documentation could be approved only by an Organization Admin's `admin:*`
 * wildcard. The Security Engineer held the generic `approve` but not the specific
 * permission, and `hasPermission` is an exact membership test, so the role that owns
 * the Security gate could not sign it off.
 *
 * Nothing caught it because every check was written in permission strings, where
 * "security_engineer lacks artifact:approve_security" reads as a fact rather than a
 * contradiction. Stated as "the owner of a gate can approve it", the bug is obvious.
 */
import { describe, it, expect } from "vitest";

import { PERMISSION_CATALOG, ALL_GRANTABLE_PERMISSIONS } from "@/lib/auth/permission-catalog";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import { approvePermissionForPhase } from "@/lib/auth/permissions";
import { AGENT_OWNER_ROLE } from "@/lib/roles";

/** The phases with a real backend gate — `_PHASE_PERMISSION` has eight entries. */
const GATED_PHASES = [
  "requirements",
  "design",
  "development",
  "review",
  "security",
  "testing",
  "deployment",
  "documentation",
] as const;

describe("gate ownership", () => {
  it("lets the role that owns each gate approve it", () => {
    const broken: string[] = [];
    for (const phase of GATED_PHASES) {
      const owner = AGENT_OWNER_ROLE[phase];
      const required = approvePermissionForPhase(phase);
      const held = ROLE_PERMISSIONS[owner] ?? [];
      if (!held.includes(required)) {
        broken.push(`${phase}: ${owner} does not hold ${required}`);
      }
    }
    expect(broken, "gates whose owning role cannot pass them").toEqual([]);
  });

  it("resolves every gated phase to a catalogued permission", () => {
    // The safe-deny sentinel is `artifact:approve_review`, which is deliberately in no
    // catalogue. A gated phase resolving to it means that phase silently denies
    // everyone but an admin — which is exactly how the three gates broke.
    for (const phase of GATED_PHASES) {
      expect(
        ALL_GRANTABLE_PERMISSIONS,
        `phase '${phase}' resolves to an uncatalogued permission`,
      ).toContain(approvePermissionForPhase(phase));
    }
  });
});

describe("catalogue integrity", () => {
  it("has no duplicate permission ids across groups", () => {
    // A duplicate renders two checkboxes writing the same id — one visibly unticking
    // the other.
    const seen = new Map<string, string>();
    const dupes: string[] = [];
    for (const group of PERMISSION_CATALOG) {
      for (const perm of group.perms) {
        const first = seen.get(perm.id);
        if (first) dupes.push(`${perm.id} in both '${first}' and '${group.group}'`);
        else seen.set(perm.id, group.group);
      }
    }
    expect(dupes).toEqual([]);
  });

  it("offers no wildcard", () => {
    // Mirrors the server-side exclusion in `list_permissions`: a wildcard satisfies
    // every check, so offering one in a picker is offering "make this an
    // administrator" disguised as a checkbox.
    expect(ALL_GRANTABLE_PERMISSIONS.filter((p) => p.includes("*"))).toEqual([]);
  });

  it("grants every role only permissions that exist in the catalogue", () => {
    const catalogued = new Set(ALL_GRANTABLE_PERMISSIONS);
    const unknown: string[] = [];
    for (const [role, perms] of Object.entries(ROLE_PERMISSIONS)) {
      for (const p of perms) {
        if (p !== "admin:*" && !catalogued.has(p)) unknown.push(`${role}: ${p}`);
      }
    }
    expect(unknown, "granted but not in the catalogue — ungrantable via the UI").toEqual([]);
  });
});
