"""The BU -> Langfuse-organization lifecycle: provisioning, access, isolation, teardown.

WHAT THESE PIN. Creating a business unit now creates its Langfuse organization, makes the
platform's organization admins OWNER and the unit's own admin ADMIN, follows a rename,
and tears the organization down when the unit is archived. Four things about that are easy
to get wrong in ways no runtime error reveals:

  1. THE TWO GRANT PATHS. Somebody appointed unit admin five minutes ago has no Langfuse
     `users` row, so a membership cannot reference them. Getting this wrong does not raise
     — it silently grants nobody, which is exactly the bug this work fixed.
  2. REVOCATION HAS TO CLEAR BOTH. Deleting the membership and leaving the invitation
     means the person regains access the next time they sign in.
  3. NOT ADOPTING A FOREIGN ORGANIZATION. The Langfuse instance is shared with another
     product and already holds organizations called `Payments` and `Lending`. Matching by
     name would write this platform's prompts into theirs — a silent isolation breach.
  4. TEARDOWN IS NOT A DELETE. Traces live in ClickHouse; a Postgres delete orphans them.

The Langfuse database is stubbed, not mocked at the method boundary, so the SQL these
methods actually emit is what gets exercised.
"""
from __future__ import annotations

import pytest

from shared.observability.provisioning import (
    ORG_ROLE_ADMIN,
    ORG_ROLE_OWNER,
    LangfuseProvisioner,
    _affected,
)

SIBLING_ORG_ID = "431272bc-5216-497e-8191-aa1e1f26f636"


class FakeLangfuseDB:
    """A stand-in for the Langfuse Postgres, faithful to the columns this module writes."""

    def __init__(self, *, users=(), orgs=()):
        self.users = {e.lower(): f"user-{i}" for i, e in enumerate(users)}
        self.orgs = dict(orgs)  # id -> name
        self.memberships: dict[tuple, str] = {}  # (org_id, user_id) -> role
        self.invitations: dict[tuple, str] = {}  # (org_id, email) -> role
        self.projects: dict[str, dict] = {}
        self.api_keys: dict[str, str] = {}
        self.closed = False

    # ── the asyncpg surface the provisioner uses ──────────────────────────────
    async def fetchval(self, sql, *args):
        s = " ".join(sql.lower().split())
        if "from organizations where name=" in s:
            for oid, name in self.orgs.items():
                if name == args[0]:
                    return oid
            return None
        if "from users where lower(email)" in s:
            return self.users.get(str(args[0]).lower())
        return None

    async def fetchrow(self, sql, *args):
        s = " ".join(sql.lower().split())
        if "from organizations where id=" in s:
            name = self.orgs.get(args[0])
            return None if name is None else {"id": args[0], "name": name}
        if "from organization_memberships where org_id=" in s:
            role = self.memberships.get((args[0], args[1]))
            return None if role is None else {"id": "m1", "role": role}
        if "from membership_invitations where org_id=" in s:
            role = self.invitations.get((args[0], str(args[1]).lower()))
            return None if role is None else {"id": "i1", "org_role": role}
        return None

    async def fetch(self, sql, *args):
        s = " ".join(sql.lower().split())
        if "from information_schema.columns" in s:
            from shared.observability.provisioning import _REQUIRED_COLUMNS

            return [
                {"table_name": t, "column_name": c}
                for t, cols in _REQUIRED_COLUMNS.items()
                for c in cols
            ]
        if "select id from projects where org_id=" in s:
            return [
                {"id": pid}
                for pid, p in self.projects.items()
                if p["org_id"] == args[0] and p.get("deleted_at") is None
            ]
        if "from organization_memberships m" in s:
            out = []
            for (oid, uid), role in self.memberships.items():
                if oid != args[0]:
                    continue
                email = next(e for e, u in self.users.items() if u == uid)
                out.append({"email": email, "role": role})
            return out
        if "from membership_invitations where org_id=" in s:
            return [
                {"email": e, "org_role": r}
                for (oid, e), r in self.invitations.items()
                if oid == args[0]
            ]
        return []

    async def execute(self, sql, *args):
        s = " ".join(sql.lower().split())
        if s.startswith("insert into organizations"):
            self.orgs[args[0]] = args[1]
            return "INSERT 1"
        if s.startswith("update organizations set name"):
            self.orgs[args[2]] = args[0]
            return "UPDATE 1"
        if s.startswith("insert into organization_memberships"):
            self.memberships[(args[1], args[2])] = args[3]
            return "INSERT 1"
        if s.startswith("update organization_memberships set role"):
            for key in list(self.memberships):
                if key[0] and args[0]:
                    self.memberships[key] = args[0]
            return "UPDATE 1"
        if s.startswith("insert into membership_invitations"):
            self.invitations[(args[2], str(args[1]).lower())] = args[3]
            return "INSERT 1"
        if s.startswith("update membership_invitations set org_role"):
            for key in list(self.invitations):
                self.invitations[key] = args[0]
            return "UPDATE 1"
        if s.startswith("delete from organization_memberships where org_id=$1 and user_id"):
            return f"DELETE {1 if self.memberships.pop((args[0], args[1]), None) else 0}"
        if s.startswith("delete from organization_memberships where org_id="):
            n = len([k for k in self.memberships if k[0] == args[0]])
            self.memberships = {k: v for k, v in self.memberships.items() if k[0] != args[0]}
            return f"DELETE {n}"
        if s.startswith("delete from membership_invitations where org_id=$1 and lower(email)"):
            hit = self.invitations.pop((args[0], str(args[1]).lower()), None)
            return f"DELETE {1 if hit else 0}"
        if s.startswith("delete from membership_invitations where org_id="):
            n = len([k for k in self.invitations if k[0] == args[0]])
            self.invitations = {k: v for k, v in self.invitations.items() if k[0] != args[0]}
            return f"DELETE {n}"
        if s.startswith("update projects set deleted_at=$1"):
            for pid in args[1]:
                self.projects[pid]["deleted_at"] = args[0]
            return f"UPDATE {len(args[1])}"
        if s.startswith("update projects set deleted_at=null"):
            n = 0
            for p in self.projects.values():
                if p["org_id"] == args[1] and p.get("deleted_at") is not None:
                    p["deleted_at"] = None
                    n += 1
            return f"UPDATE {n}"
        if s.startswith("delete from api_keys where project_id"):
            n = len([k for k, pid in self.api_keys.items() if pid in args[0]])
            self.api_keys = {k: v for k, v in self.api_keys.items() if v not in args[0]}
            return f"DELETE {n}"
        return "OK 0"

    def transaction(self):
        db = self

        class _Tx:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *a):
                return False

        return _Tx()

    async def close(self):
        self.closed = True


def _provisioner(db, *, bootstrap="boot@example.com"):
    p = LangfuseProvisioner(
        db_url="postgresql://x/y", salt="salt", host="https://lf.test",
        bootstrap_email=bootstrap,
    )
    p._connect = lambda: _coro(db)  # noqa: SLF001
    return p


async def _coro(value):
    return value


# ── the two grant paths ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_existing_langfuse_user_gets_a_real_membership():
    db = FakeLangfuseDB(users=["boot@example.com", "admin@corp.test"])
    result = await _provisioner(db).provision_org(
        unit_name="Lending Ops", access={"admin@corp.test": ORG_ROLE_ADMIN}
    )
    assert result.access["admin@corp.test"] == "granted"
    org = result.langfuse_org_id
    assert db.memberships[(org, db.users["admin@corp.test"])] == ORG_ROLE_ADMIN
    assert not db.invitations, "a user who exists must not be invited"


@pytest.mark.asyncio
async def test_a_person_with_no_langfuse_account_is_invited_instead():
    """The case that made this work necessary: a just-appointed unit admin.

    They have no `users` row, so `organization_memberships.user_id` has nothing to point
    at. Before this, they were simply skipped with a log line — appointed in this product
    and granted nothing in Langfuse.
    """
    db = FakeLangfuseDB(users=["boot@example.com"])
    result = await _provisioner(db).provision_org(
        unit_name="Lending Ops", access={"newjoiner@corp.test": ORG_ROLE_ADMIN}
    )
    assert result.access["newjoiner@corp.test"] == "invited"
    org = result.langfuse_org_id
    assert db.invitations[(org, "newjoiner@corp.test")] == ORG_ROLE_ADMIN
    # And nothing was written to memberships for them, which would violate the FK.
    assert len(db.memberships) == 1  # the bootstrap owner only


@pytest.mark.asyncio
async def test_bootstrap_addresses_are_always_owners():
    """An organization nobody can open is indistinguishable from one that failed to
    provision, so there is always a floor of owners."""
    db = FakeLangfuseDB(users=["boot@example.com"])
    result = await _provisioner(db).provision_org(unit_name="Lending Ops")
    assert result.access["boot@example.com"] == "granted"
    assert db.memberships[(result.langfuse_org_id, db.users["boot@example.com"])] == ORG_ROLE_OWNER


@pytest.mark.asyncio
async def test_the_bootstrap_owner_is_not_demoted_by_also_being_a_unit_admin():
    """Caught end to end, not in review.

    The bootstrap address is an OWNER so that a provisioned organization is always
    openable. Appointing that same person admin of one business unit used to overwrite
    that with ADMIN — an ordinary appointment quietly removing the safety floor. Merging
    takes the stronger role now, not the later one.
    """
    db = FakeLangfuseDB(users=["boot@example.com"])
    result = await _provisioner(db).provision_org(
        unit_name="Lending Ops", access={"boot@example.com": ORG_ROLE_ADMIN}
    )
    assert db.memberships[(result.langfuse_org_id, db.users["boot@example.com"])] == ORG_ROLE_OWNER


def test_strongest_role_ranks_owner_above_admin():
    from shared.observability.provisioning import strongest_role

    assert strongest_role(ORG_ROLE_ADMIN, ORG_ROLE_OWNER) == ORG_ROLE_OWNER
    assert strongest_role("", ORG_ROLE_ADMIN) == ORG_ROLE_ADMIN
    assert strongest_role("VIEWER", ORG_ROLE_ADMIN) == ORG_ROLE_ADMIN


# ── revocation ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoking_clears_the_pending_invitation_too():
    """Otherwise a removed unit admin regains access simply by signing in later."""
    p = _provisioner(db := FakeLangfuseDB(users=["boot@example.com"]))
    result = await p.provision_org(
        unit_name="Lending Ops", access={"leaver@corp.test": ORG_ROLE_ADMIN}
    )
    org = result.langfuse_org_id
    assert (org, "leaver@corp.test") in db.invitations

    await p.sync_org_access(org_id=org, revoke=["leaver@corp.test"])
    assert (org, "leaver@corp.test") not in db.invitations


@pytest.mark.asyncio
async def test_granting_someone_who_has_since_signed_in_drops_their_invitation():
    """Left in place it is a second, independent grant Langfuse could apply after a revoke."""
    db = FakeLangfuseDB(users=["boot@example.com"])
    p = _provisioner(db)
    result = await p.provision_org(
        unit_name="Lending Ops", access={"later@corp.test": ORG_ROLE_ADMIN}
    )
    org = result.langfuse_org_id
    assert (org, "later@corp.test") in db.invitations

    db.users["later@corp.test"] = "user-later"  # they sign in to Langfuse
    await p.sync_org_access(org_id=org, grants={"later@corp.test": ORG_ROLE_ADMIN})

    assert (org, "later@corp.test") not in db.invitations
    assert db.memberships[(org, "user-later")] == ORG_ROLE_ADMIN


# ── isolation on a shared instance ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_name_collision_does_not_adopt_another_products_organization():
    """The silent isolation breach this guards against.

    The shared instance holds an organization called `Payments` belonging to a sibling
    product. Reusing it would send this platform's prompts and completions somewhere their
    users can read them, with no error anywhere.
    """
    db = FakeLangfuseDB(users=["boot@example.com"], orgs={SIBLING_ORG_ID: "Payments"})
    result = await _provisioner(db).provision_org(unit_name="Payments")

    assert result.langfuse_org_id != SIBLING_ORG_ID
    assert result.langfuse_org_name != "Payments"
    assert result.created
    assert db.orgs[SIBLING_ORG_ID] == "Payments", "the sibling's organization was modified"


@pytest.mark.asyncio
async def test_an_organization_we_created_is_reused_not_duplicated():
    db = FakeLangfuseDB(users=["boot@example.com"], orgs={"ours-1": "Payments"})
    result = await _provisioner(db).provision_org(
        unit_name="Payments", owned_org_ids=frozenset({"ours-1"})
    )
    assert result.langfuse_org_id == "ours-1"
    assert not result.created


@pytest.mark.asyncio
async def test_a_recorded_organization_is_addressed_by_id_not_by_name():
    """A unit renamed in Langfuse by hand must still resolve to its own organization."""
    db = FakeLangfuseDB(users=["boot@example.com"], orgs={"ours-1": "renamed-in-langfuse"})
    result = await _provisioner(db).provision_org(unit_name="Lending Ops", org_id="ours-1")
    assert result.langfuse_org_id == "ours-1"
    assert not result.created
    assert len(db.orgs) == 1, "a second organization was created for the same unit"


@pytest.mark.asyncio
async def test_renaming_into_a_foreign_name_is_refused():
    db = FakeLangfuseDB(users=["boot@example.com"], orgs={
        SIBLING_ORG_ID: "Lending", "ours-1": "Old Name",
    })
    used = await _provisioner(db).rename_org(org_id="ours-1", new_name="Lending")
    assert used != "Lending"
    assert db.orgs[SIBLING_ORG_ID] == "Lending"


# ── teardown ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_teardown_removes_access_and_keys_without_deleting_the_organization():
    """Traces live in ClickHouse. Deleting the Postgres rows orphans them instead of
    removing them, which is worse than either leaving them or deleting them properly."""
    db = FakeLangfuseDB(users=["boot@example.com", "admin@corp.test"])
    p = _provisioner(db)
    result = await p.provision_org(
        unit_name="Lending Ops", access={"admin@corp.test": ORG_ROLE_ADMIN}
    )
    org = result.langfuse_org_id
    db.projects["proj-1"] = {"org_id": org, "deleted_at": None}
    db.api_keys["key-1"] = "proj-1"

    counts = await p.teardown_org(org_id=org)

    assert counts["projects"] == 1
    assert counts["api_keys"] == 1
    assert db.projects["proj-1"]["deleted_at"] is not None, "project not soft-deleted"
    assert not db.api_keys, "keys survived — ingestion would continue"
    assert not db.memberships and not db.invitations, "access survived teardown"
    assert org in db.orgs, "the organization row was deleted, orphaning ClickHouse traces"


@pytest.mark.asyncio
async def test_teardown_is_reversible_because_archiving_is():
    db = FakeLangfuseDB(users=["boot@example.com"])
    p = _provisioner(db)
    result = await p.provision_org(unit_name="Lending Ops")
    org = result.langfuse_org_id
    db.projects["proj-1"] = {"org_id": org, "deleted_at": None}

    await p.teardown_org(org_id=org)
    assert await p.restore_org(org_id=org) == 1
    assert db.projects["proj-1"]["deleted_at"] is None


# ── small things that would be silent if wrong ────────────────────────────────

@pytest.mark.parametrize(
    "tag,expected",
    [("DELETE 3", 3), ("UPDATE 0", 0), ("INSERT 0 1", 1), ("nonsense", 0), (None, 0)],
)
def test_affected_parses_asyncpg_command_tags(tag, expected):
    assert _affected(tag) == expected


@pytest.mark.asyncio
async def test_an_empty_address_is_skipped_not_written():
    """A user row with no email cannot be granted; it must not write a null-email row."""
    db = FakeLangfuseDB(users=["boot@example.com"])
    result = await _provisioner(db).provision_org(unit_name="Lending Ops", access={"  ": "ADMIN"})
    assert "" not in result.access
    assert not db.invitations


@pytest.mark.asyncio
async def test_the_connection_is_always_closed():
    db = FakeLangfuseDB(users=["boot@example.com"])
    await _provisioner(db).provision_org(unit_name="Lending Ops")
    assert db.closed
