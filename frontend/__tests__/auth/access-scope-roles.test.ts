import { describe, expect, it } from "vitest";

/**
 * The role each scope is labelled with on "My access".
 *
 * THE BUG. The route had no source for a per-binding role — FastAPI exposed the
 * caller's permissions and their single effective platform role, never which role they
 * hold WHERE — so it stamped that one role onto every binding. A Project Admin in one
 * unit who merely CONTRIBUTES to a project in another was shown "Project Admin · You
 * administer" on both. The page that exists to say what you may do overstated it, which
 * is the one direction an access page must not be wrong in.
 *
 * `GET /auth/bindings` supplies the real roles now. This file tests the resolution
 * ON TOP of them — the part with the judgement in it, and the part a backend test
 * cannot reach.
 *
 * A SINGLE-BINDING FIXTURE CANNOT SEE THIS BUG: stamping one role everywhere is
 * indistinguishable from correctness until two bindings disagree. Every case below has
 * at least two.
 */

const ADMIN_ROLES = new Set(["org_admin", "bu_admin", "project_admin"]);

/** The resolution as `app/api/auth/access-scope/route.ts` performs it. */
function resolve(
  held: { scopeId: string; role: string }[],
  projects: { id: string; workspaceId: string | null }[],
  units: string[],
  platformRole: string,
) {
  const heldRole = new Map(held.map((b) => [b.scopeId, b.role]));

  const roleFromProjectsIn = new Map<string, string>();
  for (const p of projects) {
    const own = heldRole.get(p.id);
    if (!own || !p.workspaceId || roleFromProjectsIn.has(p.workspaceId)) continue;
    roleFromProjectsIn.set(p.workspaceId, own);
  }

  const roleFor = (scopeId: string, parentId?: string | null): string =>
    heldRole.get(scopeId) ??
    (parentId ? heldRole.get(parentId) : undefined) ??
    roleFromProjectsIn.get(scopeId) ??
    platformRole;

  return {
    unitRole: (id: string) => roleFor(id),
    projectRole: (p: { id: string; workspaceId: string | null }) =>
      roleFor(p.id, p.workspaceId),
    administersProject: (p: { id: string; workspaceId: string | null }) =>
      ADMIN_ROLES.has(heldRole.get(p.id) ?? "") ||
      ADMIN_ROLES.has((p.workspaceId ? heldRole.get(p.workspaceId) : undefined) ?? ""),
    administersUnit: (id: string) => ADMIN_ROLES.has(heldRole.get(id) ?? ""),
    units,
  };
}

/** Bruno's real shape: runs Lending, contributes to one project in Payments. */
const LENDING = "wu-lending";
const PAYMENTS = "wu-payments";
const TEST_DEMO = { id: "p-test-demo", workspaceId: LENDING };
const CORE_LEDGER = { id: "p-core-ledger", workspaceId: PAYMENTS };

const bruno = () =>
  resolve(
    [
      { scopeId: LENDING, role: "project_admin" },
      { scopeId: TEST_DEMO.id, role: "project_admin" },
      { scopeId: CORE_LEDGER.id, role: "data_engineer" },
    ],
    [TEST_DEMO, CORE_LEDGER],
    [LENDING, PAYMENTS],
    "project_admin",
  );

describe("what role each scope is labelled with", () => {
  it("labels a contributed project with the role held there, not the platform role", () => {
    /** THE HEADLINE. This read "Project Admin" for a data_engineer binding. */
    expect(bruno().projectRole(CORE_LEDGER)).toBe("data_engineer");
  });

  it("still labels the administered project Project Admin", () => {
    /** Non-vacuity: the fix must not flatten everything to the contributor role. */
    expect(bruno().projectRole(TEST_DEMO)).toBe("project_admin");
  });

  it("labels a unit reached only through a project with the role held inside it", () => {
    /** Bruno holds NOTHING on Payments itself. Falling through to his platform role
     *  labelled a unit he administers nothing in "Project Admin". */
    expect(bruno().unitRole(PAYMENTS)).toBe("data_engineer");
    expect(bruno().unitRole(LENDING)).toBe("project_admin");
  });
});

describe('who gets the "You administer" badge', () => {
  it("does not claim a contributed project is administered", () => {
    expect(bruno().administersProject(CORE_LEDGER)).toBe(false);
  });

  it("claims the one that is", () => {
    expect(bruno().administersProject(TEST_DEMO)).toBe(true);
  });

  it("counts a unit-scoped admin binding as administering its projects", () => {
    /** How the platform actually grants a Project Admin their reach: the binding sits
     *  on the BUSINESS UNIT, and the projects inside it are theirs without a per-project
     *  row. Requiring an explicit project binding would under-report the opposite way. */
    const r = resolve(
      [{ scopeId: LENDING, role: "project_admin" }],
      [{ id: "p-new", workspaceId: LENDING }],
      [LENDING],
      "project_admin",
    );
    expect(r.administersProject({ id: "p-new", workspaceId: LENDING })).toBe(true);
    expect(r.projectRole({ id: "p-new", workspaceId: LENDING })).toBe("project_admin");
  });

  it("does not administer a unit it merely contributes into", () => {
    expect(bruno().administersUnit(PAYMENTS)).toBe(false);
    expect(bruno().administersUnit(LENDING)).toBe(true);
  });
});
