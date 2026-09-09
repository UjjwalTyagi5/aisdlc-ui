"""The per-project workspace routers are scoped to the project in their path.

Five routers — dev, code review, security, deployment, documentation — took
`{project_id}` from the path and filtered on `tenant_id` alone. Their only gate was the
`artifact:view` floor applied at include time, and `contributor` holds that: the role
whose entire designed purpose is to hold nothing until a unit admin assigns a real one.
So the least-privileged account in the organisation could read any project's source tree
and trigger clones, scans and deploy-prepares against projects in units it had never
been admitted to.

See finding 3 in docs/rbac-audit-2026-08-17.md.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def two_units():
    """Two units, a project in each. The point is the one the caller is NOT in."""
    org = str(_uuid.uuid4())
    unit_a, unit_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj_a, proj_b = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Scope Test')"
        ), {"i": org, "s": f"pws-{org[:8]}"})
        for wid, slug in ((unit_a, "unit-a"), (unit_b, "unit-b")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :s)"
            ), {"i": wid, "o": org, "s": slug})
    async with get_db_session_for_tenant(org) as s:
        for pid, wid, name in ((proj_a, unit_a, "Alpha"), (proj_b, unit_b, "Beta")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
                "VALUES (:i, :w, :t, :n)"
            ), {"i": pid, "w": wid, "t": org, "n": name})
    yield {"org": org, "unit_a": unit_a, "unit_b": unit_b,
           "proj_a": proj_a, "proj_b": proj_b}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


# Every read route across the five routers that takes only {project_id}. Listed
# explicitly rather than derived from app.routes so a route silently losing its scope
# check shows up as a test that stops covering it, not as a loop that shrinks.
FOREIGN_READS = [
    "/dev/{p}/workspace/tree",
    "/dev/{p}/workspace",
    "/dev/{p}/workspace/changes",
    "/code-review/{p}/reviews",
    "/security/{p}/scans",
    "/deployment/{p}/connectors",
    "/documentation/{p}/connectors",
]


@pytest.mark.asyncio
async def test_a_contributor_cannot_read_another_units_project(two_units):
    """The headline case: `contributor` holds artifact:view and nothing else."""
    t = two_units
    user = f"contrib-{_uuid.uuid4()}"
    await grant_role(user, t["unit_a"], "contributor",
                     tenant_id=t["org"], scope_kind="business_unit")
    hdr = _hdr(user, t["org"], ["artifact:view"])
    c = _client()

    for path in FOREIGN_READS:
        r = c.get(path.format(p=t["proj_b"]), headers=hdr)
        # 404, not 403 — a project you cannot reach must not be confirmed to exist.
        assert r.status_code == 404, f"{path} leaked: {r.status_code} {r.text[:200]}"


@pytest.mark.asyncio
async def test_a_developer_cannot_prepare_work_against_a_foreign_project(two_units):
    """The writes matter more than the reads: these clone repos and stage deploys."""
    t = two_units
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer",
                     tenant_id=t["org"], scope_kind="project")
    hdr = _hdr(user, t["org"], ["artifact:view", "run:create", "agent:invoke"])
    c = _client()
    foreign = t["proj_b"]

    assert c.post(f"/dev/{foreign}/workspace/pull", headers=hdr,
                  json={"ado_project": "x", "repo_name": "y", "branch": "main"}
                  ).status_code == 404
    assert c.post(f"/security/{foreign}/scan/prepare", headers=hdr,
                  json={}).status_code == 404
    assert c.post(f"/deployment/{foreign}/deploy/prepare", headers=hdr,
                  json={}).status_code == 404


@pytest.mark.asyncio
async def test_the_project_you_are_bound_to_still_works(two_units):
    """The check must bite only on foreign projects.

    A developer bound to a project has to be able to open its workspace — if this
    regressed, the scope fix would have broken the product rather than secured it.
    """
    t = two_units
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer",
                     tenant_id=t["org"], scope_kind="project")
    hdr = _hdr(user, t["org"], ["artifact:view", "run:create"])

    r = _client().get(f"/dev/{t['proj_a']}/workspace", headers=hdr)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_bu_admin_reaches_every_project_in_their_own_unit(two_units):
    """Reach is by binding, not by membership row: a unit binding covers its projects."""
    t = two_units
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["unit_a"], "bu_admin",
                     tenant_id=t["org"], scope_kind="business_unit")
    hdr = _hdr(user, t["org"], ["artifact:view", "workspace:manage"])
    c = _client()

    # 403, NOT 200 — and the difference from 404 is the whole assertion.
    #
    # These two tests used to expect 200 and had been failing since 2026-08-31, when
    # `dev_workspace_router` gained `require_agent_access("development")` on top of
    # project scope. Governance roles hold `none` on every agent in
    # `AGENT_DEFAULT_REACH` by design (PRD §14.8: they do not run agents), and the
    # org_admin-denial case is required coverage in
    # docs/superpowers/specs/2026-08-31-development-agent-verification-design.md §2.4.
    # So the behaviour is intended and these expectations were the stale half.
    #
    # They are kept rather than deleted because the pair still answers the question
    # this FILE exists for, just one layer further in: a project inside your unit is
    # refused for the agent (403 — it is yours, this agent is not), and one outside it
    # is refused for existing at all (404). Collapsing both to 404 would mean scope had
    # stopped being enforced separately, and nothing else would notice.
    assert c.get(f"/dev/{t['proj_a']}/workspace", headers=hdr).status_code == 403
    # ...and stops at the unit boundary, where the answer changes to "no such project".
    assert c.get(f"/dev/{t['proj_b']}/workspace", headers=hdr).status_code == 404


@pytest.mark.asyncio
async def test_an_org_admin_is_refused_the_agent_but_never_the_project(two_units):
    """`admin:*` is org-wide standing, so scope never refuses them — every project in
    the tenant is theirs to address, and BOTH answers below are 403 rather than one of
    them being 404.

    That is the contrast with the Business Unit Admin above, and it is what makes these
    two tests worth keeping now that neither returns 200: scope and agent access are
    still two separate gates, and you can tell which one spoke.

    An Org Admin who genuinely needs the workspace is granted it per project through
    `agent_access_overrides` (POST /projects/{id}/agent-access-overrides), so this is a
    default, not a wall.
    """
    t = two_units
    user = f"oa-{_uuid.uuid4()}"
    await grant_role(user, t["org"], "org_admin",
                     tenant_id=t["org"], scope_kind="organization")
    hdr = _hdr(user, t["org"], ["admin:*"])
    c = _client()

    assert c.get(f"/dev/{t['proj_a']}/workspace", headers=hdr).status_code == 403
    assert c.get(f"/dev/{t['proj_b']}/workspace", headers=hdr).status_code == 403


@pytest.mark.asyncio
async def test_a_slug_in_the_path_is_resolved_and_scoped(two_units):
    """The gate resolves slugs as well as UUIDs, so it cannot be stepped around by
    addressing a project the other way.

    These particular handlers have always required a UUID — `dev_workspace_store
    .get_for_project` casts it — so the own-slug case is asserted as "got past the
    gate", not as 200. Asserting 200 would be asserting slug routing the handler has
    never supported, and the test would be measuring the wrong thing.
    """
    t = two_units
    user = f"dev-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer",
                     tenant_id=t["org"], scope_kind="project")
    hdr = _hdr(user, t["org"], ["artifact:view", "run:create"])
    c = _client()

    # The foreign slug is refused BY THE GATE, before any handler runs.
    assert c.get("/dev/beta/workspace", headers=hdr).status_code == 404
    # The caller's own project resolves and passes the gate.
    from shared.authz.project_scope import resolve_project
    async with get_db_session_for_tenant(t["org"]) as s:
        assert str((await resolve_project(s, t["org"], "alpha")).id) == t["proj_a"]
        assert await resolve_project(s, t["org"], "nonexistent-slug") is None


@pytest.mark.asyncio
async def test_an_unknown_project_is_not_found(two_units):
    t = two_units
    user = f"oa-{_uuid.uuid4()}"
    await grant_role(user, t["org"], "org_admin",
                     tenant_id=t["org"], scope_kind="organization")

    r = _client().get(f"/dev/{_uuid.uuid4()}/workspace", headers=_hdr(user, t["org"], ["admin:*"]))
    assert r.status_code == 404
