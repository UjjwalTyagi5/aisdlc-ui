"""Per-project Langfuse roles: reach exactly one project, and never lose reach by gaining it.

WHY THIS EXISTS AT ALL. Langfuse documents project-level RBAC as Enterprise-only, and the
LICENCE GATE IS ON ASSIGNMENT, NOT ENFORCEMENT — `resolveProjectRole` in its MIT-licensed
core is a plain lookup with no entitlement check, and the invitation path builds
ProjectMemberships unconditionally. So rows written here are honoured. These tests pin the
behaviour that depends on that, because none of it fails loudly when it is wrong.

  1. THE DOWNGRADE. A project membership REPLACES the organization role for that project;
     it does not add to it. Granting MEMBER to a unit admin (organization ADMIN) therefore
     REMOVES capability from one project — a grant that takes access away, silently.
  2. THE ORGANIZATION FLOOR. `project_memberships.org_membership_id` is NOT NULL, so a
     project role is impossible without an organization membership. It has to be NONE, or
     the person reaches every other project in the unit.
  3. ONE INVITATION PER ORGANIZATION. Langfuse enforces UNIQUE (email, org_id), so a
     second pending project grant cannot be written as a second row — attempting it would
     fail the index and lose the first grant too.
"""
from __future__ import annotations

import pytest

from shared.observability.provisioning import (
    ORG_ROLE_ADMIN,
    ORG_ROLE_NONE,
    ORG_ROLE_OWNER,
    PROJECT_ROLE_MEMBER,
    PROJECT_ROLE_VIEWER,
    LangfuseProvisioner,
)
from tests.observability.test_langfuse_org_lifecycle import FakeLangfuseDB

ORG = "org-1"
PROJ_A = "proj-a"
PROJ_B = "proj-b"


class ProjectFakeDB(FakeLangfuseDB):
    """The org-lifecycle fake, plus the project_memberships surface."""

    def __init__(self, **kw):
        super().__init__(**kw)
        # (project_id, user_id) -> role
        self.project_memberships: dict[tuple, str] = {}
        # (org_id, email) -> {"org_role", "project_id", "project_role"}
        self.invites: dict[tuple, dict] = {}

    async def fetchval(self, sql, *args):
        s = " ".join(sql.lower().split())
        if "select role from project_memberships where project_id=" in s:
            return self.project_memberships.get((args[0], args[1]))
        if "select count(*) from project_memberships where org_membership_id=" in s:
            return len([1 for (_p, u) in self.project_memberships if f"om-{u}" == args[0]])
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        s = " ".join(sql.lower().split())
        if "select id, role from organization_memberships where org_id=" in s:
            role = self.memberships.get((args[0], args[1]))
            return None if role is None else {"id": f"om-{args[1]}", "role": role}
        if "select id, project_id, org_role, project_role from membership_invitations" in s:
            inv = self.invites.get((args[0], str(args[1]).lower()))
            if inv is None:
                return None
            return {"id": "inv-1", **inv}
        return await super().fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        s = " ".join(sql.lower().split())
        if "from project_memberships pm" in s:
            out = []
            for (pid, uid), role in self.project_memberships.items():
                if pid != args[0]:
                    continue
                email = next(e for e, u in self.users.items() if u == uid)
                out.append({"email": email, "role": role})
            return out
        if "select email, project_role from membership_invitations" in s:
            return [
                {"email": e, "project_role": v["project_role"]}
                for (oid, e), v in self.invites.items()
                if oid == args[0] and v.get("project_id") == args[1] and v.get("project_role")
            ]
        return await super().fetch(sql, *args)

    async def execute(self, sql, *args):
        s = " ".join(sql.lower().split())
        if s.startswith("insert into project_memberships"):
            self.project_memberships[(args[0], args[1])] = args[2]
            return "INSERT 1"
        if s.startswith("update project_memberships set role"):
            self.project_memberships[(args[2], args[3])] = args[0]
            return "UPDATE 1"
        if s.startswith("delete from project_memberships where project_id="):
            hit = self.project_memberships.pop((args[0], args[1]), None)
            return f"DELETE {1 if hit else 0}"
        if s.startswith("insert into membership_invitations"):
            # (id, email, org_id, org_role, project_id, project_role, invited_by, ...)
            if len(args) >= 6:
                self.invites[(args[2], str(args[1]).lower())] = {
                    "org_role": args[3], "project_id": args[4], "project_role": args[5],
                }
                return "INSERT 1"
            self.invites[(args[2], str(args[1]).lower())] = {
                "org_role": args[3], "project_id": None, "project_role": None,
            }
            return "INSERT 1"
        if s.startswith("update membership_invitations set project_id="):
            for k, v in self.invites.items():
                v["project_id"], v["project_role"] = args[0], args[1]
            return "UPDATE 1"
        if s.startswith("delete from membership_invitations where org_id=$1 and lower(email)=lower($2) and project_id"):
            hit = self.invites.pop((args[0], str(args[1]).lower()), None)
            return f"DELETE {1 if hit else 0}"
        if s.startswith("delete from membership_invitations where org_id=$1 and lower(email)"):
            hit = self.invites.pop((args[0], str(args[1]).lower()), None)
            return f"DELETE {1 if hit else 0}"
        if s.startswith("delete from organization_memberships where id="):
            for k in list(self.memberships):
                if f"om-{k[1]}" == args[0]:
                    del self.memberships[k]
                    return "DELETE 1"
            return "DELETE 0"
        return await super().execute(sql, *args)


def _provisioner(db):
    p = LangfuseProvisioner(
        db_url="postgresql://x/y", salt="salt", host="https://lf.test",
        bootstrap_email="boot@example.com",
    )

    async def _conn():
        return db

    p._connect = _conn  # noqa: SLF001
    return p


# ── the downgrade guard ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_unit_admin_is_not_downgraded_by_a_project_grant():
    """The trap. A project membership REPLACES the org role for that project.

    A business unit admin is organization ADMIN, which already reaches every project in
    the unit. Writing MEMBER for them on one project would leave them *less* able on that
    project than on its siblings — capability removed by an act named "grant".
    """
    db = ProjectFakeDB(users=["boot@example.com", "lead@corp.test"])
    db.memberships[(ORG, db.users["lead@corp.test"])] = ORG_ROLE_ADMIN
    p = _provisioner(db)

    applied = await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"lead@corp.test": PROJECT_ROLE_MEMBER}
    )

    assert applied["lead@corp.test"].startswith("covered-by-org")
    assert (PROJ_A, db.users["lead@corp.test"]) not in db.project_memberships
    # And their organization ADMIN is untouched, so they still reach the project fully.
    assert db.memberships[(ORG, db.users["lead@corp.test"])] == ORG_ROLE_ADMIN


@pytest.mark.asyncio
async def test_an_existing_project_row_is_removed_when_the_org_role_overtakes_it():
    """Promoted to unit admin: the old MEMBER row would now cap them. It must go."""
    db = ProjectFakeDB(users=["boot@example.com", "lead@corp.test"])
    uid = db.users["lead@corp.test"]
    db.memberships[(ORG, uid)] = ORG_ROLE_NONE
    db.project_memberships[(PROJ_A, uid)] = PROJECT_ROLE_MEMBER
    p = _provisioner(db)

    db.memberships[(ORG, uid)] = ORG_ROLE_OWNER  # they become an org admin
    await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"lead@corp.test": PROJECT_ROLE_MEMBER}
    )
    assert (PROJ_A, uid) not in db.project_memberships


# ── reaching exactly one project ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_project_admin_reaches_that_project_and_no_other():
    db = ProjectFakeDB(users=["boot@example.com", "pa@corp.test"])
    uid = db.users["pa@corp.test"]
    p = _provisioner(db)

    await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"pa@corp.test": PROJECT_ROLE_MEMBER}
    )

    assert db.project_memberships[(PROJ_A, uid)] == PROJECT_ROLE_MEMBER
    assert (PROJ_B, uid) not in db.project_memberships
    # The organization floor exists but grants nothing on its own.
    assert db.memberships[(ORG, uid)] == ORG_ROLE_NONE


@pytest.mark.asyncio
async def test_a_security_engineer_gets_viewer_not_member():
    db = ProjectFakeDB(users=["boot@example.com", "sec@corp.test"])
    p = _provisioner(db)
    await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"sec@corp.test": PROJECT_ROLE_VIEWER}
    )
    assert db.project_memberships[(PROJ_A, db.users["sec@corp.test"])] == PROJECT_ROLE_VIEWER


# ── the invitation path ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_somebody_with_no_account_is_invited_to_the_org_and_the_project_together():
    db = ProjectFakeDB(users=["boot@example.com"])
    p = _provisioner(db)

    applied = await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"newjoiner@corp.test": PROJECT_ROLE_MEMBER}
    )

    assert applied["newjoiner@corp.test"] == "invited"
    inv = db.invites[(ORG, "newjoiner@corp.test")]
    assert inv["project_id"] == PROJ_A
    assert inv["project_role"] == PROJECT_ROLE_MEMBER
    # NONE at the organization level, or the invitation would hand them the whole unit
    # the moment they sign in.
    assert inv["org_role"] == ORG_ROLE_NONE


@pytest.mark.asyncio
async def test_a_second_project_cannot_be_invited_before_they_sign_in():
    """Langfuse enforces UNIQUE (email, org_id) on invitations.

    Writing a second row would fail the index and roll back the first grant with it, so
    the second is reported rather than attempted. Reported, not silently dropped: "I added
    them and they still cannot get in" is the thing an operator has to be able to see.
    """
    db = ProjectFakeDB(users=["boot@example.com"])
    p = _provisioner(db)
    await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"newjoiner@corp.test": PROJECT_ROLE_MEMBER}
    )
    applied = await p.sync_project_access(
        org_id=ORG, project_id=PROJ_B, grants={"newjoiner@corp.test": PROJECT_ROLE_VIEWER}
    )
    assert applied["newjoiner@corp.test"] == "invite-slot-taken"
    assert db.invites[(ORG, "newjoiner@corp.test")]["project_id"] == PROJ_A


@pytest.mark.asyncio
async def test_signing_in_between_grants_drops_the_stale_invitation():
    db = ProjectFakeDB(users=["boot@example.com"])
    p = _provisioner(db)
    await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"later@corp.test": PROJECT_ROLE_MEMBER}
    )
    assert (ORG, "later@corp.test") in db.invites

    db.users["later@corp.test"] = "user-later"  # they sign in
    await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"later@corp.test": PROJECT_ROLE_MEMBER}
    )
    assert (ORG, "later@corp.test") not in db.invites
    assert db.project_memberships[(PROJ_A, "user-later")] == PROJECT_ROLE_MEMBER


# ── revocation ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoking_removes_the_project_row_and_the_bare_org_floor():
    """A NONE member holding no project rows grants nothing but still lists as a member of
    the unit, which reads as access they do not have."""
    db = ProjectFakeDB(users=["boot@example.com", "leaver@corp.test"])
    uid = db.users["leaver@corp.test"]
    p = _provisioner(db)
    await p.sync_project_access(
        org_id=ORG, project_id=PROJ_A, grants={"leaver@corp.test": PROJECT_ROLE_MEMBER}
    )
    assert (PROJ_A, uid) in db.project_memberships

    await p.sync_project_access(org_id=ORG, project_id=PROJ_A, revoke=["leaver@corp.test"])
    assert (PROJ_A, uid) not in db.project_memberships
    assert (ORG, uid) not in db.memberships, "the bare NONE floor was left behind"


@pytest.mark.asyncio
async def test_revoking_a_project_leaves_a_real_org_role_alone():
    """Losing a project must not cost somebody the unit they administer."""
    db = ProjectFakeDB(users=["boot@example.com", "lead@corp.test"])
    uid = db.users["lead@corp.test"]
    db.memberships[(ORG, uid)] = ORG_ROLE_ADMIN
    p = _provisioner(db)

    await p.sync_project_access(org_id=ORG, project_id=PROJ_A, revoke=["lead@corp.test"])
    assert db.memberships[(ORG, uid)] == ORG_ROLE_ADMIN


# ── the role map is the permission model, not a preference ───────────────────

def test_only_roles_that_hold_trace_view_are_mapped():
    """Langfuse must not show somebody data this product refuses them.

    If a role is added here without `trace:view`, that person could read every prompt and
    completion for their project by opening Langfuse directly, while /traces refuses them.
    """
    from shared.authz.permissions import _ROLE_PERMISSIONS, has_permission
    from shared.observability.org_sync import _PROJECT_ROLE_MAP

    for role in _PROJECT_ROLE_MAP:
        assert has_permission(_ROLE_PERMISSIONS[role], "trace:view"), (
            f"{role} is granted Langfuse access but holds no trace:view in this product"
        )
