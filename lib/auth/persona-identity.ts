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
 *   project_admin  → Priya Menon, `project_admin` on TWO Payments projects and
 *                    a member of Payments and nothing else. Administering more
 *                    than one project without administering the unit above them
 *                    is the boundary the scoped pages have to hold.
 *   contributors   → people already seeded with that role somewhere.
 *
 * Marcus Reyes, Farah Haddad and Noah Bennett are deliberately NOT used for the
 * delivery roles: they are Business Unit Admins, and a governance role is never
 * on a project at all. They stay in the people directory, which is where that
 * case belongs.
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
  // Tomas is in Platform Engineering with `contributor` and nothing else — the
  // state right after onboarding, before Noah (the bu_admin persona above, who
  // runs that same unit) has said what he does. Signing in as both shows the
  // two ends of one handover.
  contributor: "idn_tomas",
  // Each persona signs in as someone who ACTUALLY HOLDS that role. Several
  // used to point at whoever was nearest — `security_engineer` at the
  // Organization Admin, `data_engineer` and `scrum_master` at people who held
  // neither — which made `resolveAccessScope` fall through to "every binding
  // they have" and quietly handed the persona the wrong reach.
  project_admin: "idn_priya",
  ba: "idn_sofia",
  architect: "idn_rafael",
  developer: "idn_diego",
  qa: "idn_wei",
  devops_engineer: "idn_lena",
  data_engineer: "idn_bruno",
  security_engineer: "idn_hana",
  scrum_master: "idn_luca",
  // `custom` is intentionally absent: a composed bundle has no canonical
  // persona, and resolveAccessScope() falls back to every binding the
  // identity holds rather than inventing one.
};

/** The seeded identity a mock platform-role sign-in acts as, if any. */
export function personaIdentityFor(role: PlatformRole | null | undefined): string | null {
  if (!role) return null;
  return PERSONA_IDENTITY[role] ?? null;
}
