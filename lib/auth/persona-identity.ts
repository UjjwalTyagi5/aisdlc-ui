/**
 * Which seeded person each role in the mock sign-in picker signs in AS.
 *
 * `buildMockSessionForPlatformRole()` mints a synthetic user (`u_bu_admin`,
 * `bu_admin@acme.test`) that appears in no membership store. Before this map
 * existed every mock persona therefore resolved to ZERO bindings — and zero
 * bindings is exactly the case where "show only your Business Unit" cannot be
 * told apart from "show everything", because there is no unit to compare
 * against. Each role is bound to a seeded identity whose real bindings
 * demonstrate that role's boundary:
 *
 *   bu_admin       → Noah Bennett, `bu_admin` of Platform Engineering and
 *                    nothing else. A single-unit admin is the cleanest proof
 *                    that the other two units are gone from every surface.
 *   project_admin  → Priya Menon, `project_admin` on one Payments project and
 *                    `ba` on a Lending one. Authorized on two projects across
 *                    two different units, admin on only one — so "authorized"
 *                    and "administered" can be seen to differ.
 *   contributors   → people already seeded with that role somewhere.
 *
 * Marcus Reyes and Farah Haddad are deliberately NOT used here: each holds
 * governance in one unit and delivery in another (the per-scope tier case), and
 * signing in as them would blur the very boundary this map exists to make
 * visible. They stay in the people directory, which is where that case belongs.
 *
 * MOCK-ONLY, and deliberately dependency-free (types only) so it can be
 * imported from the session builder without dragging the fixture graph along.
 * A real backend resolves the person from the SSO subject and this file goes
 * with the rest of the seam. Ids are the IDENTITIES in
 * `lib/mock/workspace-fixtures.ts` — keep them in sync if that roster moves.
 */
import type { PlatformRole } from "@/lib/roles";

const PERSONA_IDENTITY: Partial<Record<PlatformRole, string>> = {
  org_admin: "idn_sarthak",
  bu_admin: "idn_noah",
  project_admin: "idn_priya",
  ba: "idn_priya",
  architect: "idn_diego",
  developer: "idn_diego",
  qa: "idn_wei",
  devops_engineer: "idn_lena",
  data_engineer: "idn_lena",
  security_engineer: "idn_sarthak",
  scrum_master: "idn_priya",
  // `custom` is intentionally absent: a composed bundle has no canonical
  // persona, and resolveAccessScope() falls back to every binding the
  // identity holds rather than inventing one.
};

/** The seeded identity a mock platform-role sign-in acts as, if any. */
export function personaIdentityFor(role: PlatformRole | null | undefined): string | null {
  if (!role) return null;
  return PERSONA_IDENTITY[role] ?? null;
}
