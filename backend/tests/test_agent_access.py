import uuid as _uuid

import pytest
from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.agent_access import (
    assert_agent_access,
    check_agent_access,
    require_agent_access,
)
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_project():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Access Test')"
        ), {"i": org, "s": f"access-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Access Project')"
        ), {"i": project, "w": unit, "t": org})
    yield {"org": org, "project": project}


@pytest.mark.asyncio
async def test_security_engineer_reaches_security_by_default(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="security_engineer", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_developer_does_not_reach_deployment_by_default(org_project):
    # NOTE: AGENT_DEFAULT_REACH (config/agent_registry.py, Task 2 — transcribed
    # verbatim from PRD §14.7 / spec Appendix) gives "developer" a "use" reach to
    # "security" (every delivery role reaches Security at least at "use" by
    # design), so that pairing cannot demonstrate a default-deny. "deployment" is
    # the agent where the spec's own table marks Developer "-" (no default
    # involvement), so it's used here instead to test the same property: a real
    # delivery role, not just an admin role, can be denied by the default table.
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=str(_uuid.uuid4()), agent_id="deployment",
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_project_admin_reaches_every_portfolio_1_agent(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        for agent_id in (
            "requirements", "design", "development", "code_review",
            "security", "testing", "deployment", "documentation",
        ):
            allowed = await check_agent_access(
                db, tenant_id=t["org"], project_id=t["project"],
                role="project_admin", user_id=str(_uuid.uuid4()), agent_id=agent_id,
            )
            assert allowed is True, agent_id


@pytest.mark.asyncio
async def test_org_admin_permissions_do_not_grant_agent_access(org_project):
    """admin:* is never consulted here — org_admin holds zero agent access by design."""
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="org_admin", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_a_role_level_override_grants_access_the_default_table_denies(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, role, phase, involvement) "
            "VALUES (:i, :t, :p, 'developer', 'deployment', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"]})
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=str(_uuid.uuid4()), agent_id="deployment",
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_a_person_level_override_grants_access_without_touching_the_role(org_project):
    t = org_project
    other_developer = str(_uuid.uuid4())
    named_developer = str(_uuid.uuid4())
    # agent_access_overrides.user_id carries a real FK to users.id (Task 3,
    # fk_agent_access_override_user) — the row must exist before it can be
    # referenced by an override.
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email) VALUES (:i, :t, 'named-developer@example.com')"
        ), {"i": named_developer, "t": t["org"]})
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'deployment', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": named_developer})

        allowed_named = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=named_developer, agent_id="deployment",
        )
        allowed_other = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=other_developer, agent_id="deployment",
        )
    assert allowed_named is True
    assert allowed_other is False


@pytest.mark.asyncio
async def test_assert_agent_access_raises_403_on_denial(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        with pytest.raises(HTTPException) as exc:
            await assert_agent_access(
                db, tenant_id=t["org"], project_id=t["project"],
                role="developer", user_id=str(_uuid.uuid4()), agent_id="deployment",
            )
    assert exc.value.status_code == 403


# ── Fix Round 1: UUID-shape guard + slug resolution (reviewer finding) ─────────
#
# `check_agent_access` used to hand `project_id`/`tenant_id` straight to
# `CAST(:p AS uuid)` with no shape check. A non-UUID `project_id` — a real slug like
# "payments-portal", or any garbage string — sailed into the DB and raised an
# unhandled "invalid input syntax for type uuid" (a 500), instead of a controlled
# deny. The two tests below cover the guard directly; the router-level test after
# them covers the companion fix in `require_agent_access`, which must still resolve
# a real slug to a real project (fail-closed alone would make every slug-addressed
# route silently deny everyone — safe, but wrong).

@pytest.mark.asyncio
async def test_check_agent_access_returns_false_for_non_uuid_project_id(org_project):
    """A slug (or any non-UUID garbage) in `project_id` must deny, not crash the DB call."""
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id="not-a-uuid",
            role="project_admin", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_check_agent_access_returns_false_for_empty_tenant_id(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id="", project_id=t["project"],
            role="project_admin", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is False


@pytest.fixture
def _agent_access_probe_route():
    """Mounts one throwaway route on the real `process_api.app`, gated by
    `require_agent_access("security")` on `{project_id}`, so the dependency's `_dep`
    runs through the app's real JWT middleware (which populates
    `request.state.{user_id,tenant_id,permissions}`) exactly like a production route
    would. Task 5-7 haven't mounted a real slug-addressable route on this branch yet,
    so this is the least-invention way to exercise the actual `_dep` code path —
    added and removed per-test so it never leaks into other test modules that share
    the same `process_api.app` instance.
    """
    router = APIRouter()

    @router.get("/_test_only/agent-access/{project_id}")
    async def _probe(
        project_id: str,
        _access: None = Depends(require_agent_access("security")),
    ):
        return {"ok": True}

    before = list(process_api.app.router.routes)
    process_api.app.include_router(router)
    added = [r for r in process_api.app.router.routes if r not in before]
    yield
    for r in added:
        process_api.app.router.routes.remove(r)


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


@pytest.mark.asyncio
async def test_require_agent_access_resolves_a_slug_and_grants_access(
    org_project, _agent_access_probe_route
):
    """The reviewer's core scenario: a real project addressed by its SLUG, not its
    UUID, must resolve through `resolve_project` and then pass the (granting)
    access check — not 500, and not a false deny.
    """
    t = org_project
    user = f"seceng-{_uuid.uuid4()}"
    await grant_role(user, t["project"], "security_engineer",
                     tenant_id=t["org"], scope_kind="project")
    hdr = _hdr(user, t["org"], ["artifact:view"])

    # org_project's fixture project is named "Access Project" -> slug "access-project".
    r = _client().get("/_test_only/agent-access/access-project", headers=hdr)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_require_agent_access_denies_by_slug_when_default_reach_says_no(
    org_project, _agent_access_probe_route
):
    """Same slug resolution, but a role/agent pairing the default table denies.
    Uses `scrum_master` (a real, visible project member role) which is NOT in
    `AGENT_DEFAULT_REACH["security"]` — so it has "none" reach by design. This
    proves that `require_agent_access` correctly propagates `assert_agent_access`'s
    403 when a genuine project member is denied purely by role-reach, after passing
    the new project membership check."""
    t = org_project
    user = f"scrum-{_uuid.uuid4()}"
    await grant_role(user, t["project"], "scrum_master",
                     tenant_id=t["org"], scope_kind="project")
    hdr = _hdr(user, t["org"], ["artifact:view"])

    r = _client().get("/_test_only/agent-access/access-project", headers=hdr)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_require_agent_access_404s_on_an_unknown_slug_not_500(
    org_project, _agent_access_probe_route
):
    """A slug that resolves to no project must 404 through `resolve_project` — the
    exact case that used to reach `CAST(:p AS uuid)` unguarded and 500 when the
    path segment was a slug at all (known or not).
    """
    t = org_project
    user = f"seceng-{_uuid.uuid4()}"
    await grant_role(user, t["project"], "security_engineer",
                     tenant_id=t["org"], scope_kind="project")
    hdr = _hdr(user, t["org"], ["artifact:view"])

    r = _client().get("/_test_only/agent-access/no-such-project", headers=hdr)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_require_agent_access_denies_a_role_held_only_on_a_different_project():
    """require_agent_access(agent_id) resolves role via effective_platform_role ->
    platform_role_for, which is NOT project-scoped (resolves a role the caller holds
    ANYWHERE in the tenant). Before this fix, a Developer on Project A reaches Project
    B's require_agent_access-gated routes purely because
    AGENT_DEFAULT_REACH["security"]["developer"] == "use" -- with no check that they
    are actually a member of Project B. This is the same leak
    assert_agent_access_for_chat's visible_project_ids check already closes for the
    chat routes; this test proves the router-dependency form is now closed too, via
    the one already-gated route that exists today (security_workspace_router)."""
    import uuid as _uuid
    from config.auth.jwt import create_access_token
    from shared.authz.grant import grant_role
    from shared.db import get_db_session_for_tenant, get_db_session_superuser
    from sqlalchemy import text
    from fastapi.testclient import TestClient
    import process_api

    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project_a = str(_uuid.uuid4())
    project_b = str(_uuid.uuid4())
    dev = f"dev-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'RAA Test')"
        ), {"i": org, "s": f"raa-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Project A')"
        ), {"i": project_a, "w": unit, "t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Project B')"
        ), {"i": project_b, "w": unit, "t": org})
    # dev is a Security Engineer on Project A only -- never added to Project B.
    # security is chosen (not development) because security_workspace_router is the
    # one require_agent_access-gated route that exists today; Task 3 adds the
    # equivalent for development.
    await grant_role(dev, project_a, "security_engineer", tenant_id=org, scope_kind="project", granted_by="test")

    resp = TestClient(process_api.app).get(
        f"/security/{project_b}/scans",
        headers={
            "Authorization": "Bearer "
            + create_access_token(user_id=dev, tenant_id=org, permissions=["artifact:view"])
        },
    )
    assert resp.status_code == 404
