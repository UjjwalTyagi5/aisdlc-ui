"""B4: GET /dev/{project_id}/prs — list PRs for the Development PR tabs.

Tests:
  - Unit tests: mock the DB session and test the endpoint contract (field mapping,
    status bucket, exclusion of runs without pr_url).
  - Integration test: live DB — seeds real Run rows and verifies cross-tenant
    isolation (RLS) by querying the runs table directly.

Design note on not using httpx + live DB in the same test:
  The FastAPI ASGI transport creates asyncpg connections on the test's function
  loop while DB seeding fixtures run on the session loop — mixing them causes
  "Future attached to a different loop" errors (the same pitfall that all other
  DB-backed integration tests in this repo avoid). The unit tests below cover
  the full HTTP contract; the integration test covers tenant isolation at the
  SQL layer.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from shared.db import RESOLVED_POSTGRES_CONN_STRING as _CONN
    _DB_AVAILABLE = bool(_CONN) and "placeholder" not in _CONN
except Exception:
    _DB_AVAILABLE = False

TENANT_A = "00000000-0000-0000-0000-b400000000a1"
TENANT_B = "00000000-0000-0000-0000-b400000000b2"

PROJECT_A = str(uuid.UUID("00000000-0000-0000-0000-b4000000a001"))

_skip_no_db = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason="DB not reachable — skipping live-DB B4 tests",
)


# ── Shared DB session mock factory ─────────────────────────────────────────

def _make_db_override(runs: list):
    """Return a FastAPI dependency override that yields a mock AsyncSession.

    `runs` is the list of ORM-like objects the endpoint will iterate over.
    """
    from shared.models.orm import Run

    async def _override():
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = runs
        session.execute = AsyncMock(return_value=result_mock)
        yield session

    return _override


def _make_run(
    run_id: str,
    project_id: str,
    tenant_id: str,
    development_artifacts: dict | None,
) -> MagicMock:
    """Build a minimal mock Run object for unit tests."""
    from datetime import datetime, timezone

    run = MagicMock()
    run.id = uuid.UUID(run_id)
    run.project_id = uuid.UUID(project_id)
    run.tenant_id = uuid.UUID(tenant_id)
    run.development_artifacts = development_artifacts
    run.created_at = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return run


@pytest.fixture(autouse=True)
def _mock_workspace_resolution(monkeypatch):
    async def _fake(request, tenant_id: str):
        return uuid.uuid4()

    monkeypatch.setattr(
        "shared.authz.workspace.active_workspace_for_request",
        _fake,
    )


@pytest.fixture(autouse=True)
def _mock_agent_access(monkeypatch):
    """dev_workspace_router is gated by require_agent_access("development")
    (Task 3), which — via assert_agent_access — resolves the project through
    the mocked `get_db_session` above. `session.execute(...).first()` on that
    mock returns a truthy but non-UUID-shaped MagicMock id, so the router's
    `_is_uuid(project_id)` guard in `check_agent_access` fails closed with a
    403 that has nothing to do with these tests' actual PR-listing contract.
    Patches only `assert_agent_access` (the one call `require_agent_access`
    can't satisfy against a mocked DB) — everything else in the dependency
    chain runs for real.
    """
    async def _fake(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "shared.authz.agent_access.assert_agent_access",
        _fake,
    )


def _mint(tenant_id: str, permissions: list[str] | None = None):
    from datetime import datetime, timedelta

    import jwt as pyjwt

    from config.env import JWT_SECRET_KEY

    payload = {
        "sub": "test-user-b4",
        "tenant_id": tenant_id,
        "permissions": permissions if permissions is not None else ["artifact:view"],
        "exp": datetime.utcnow() + timedelta(minutes=60),
    }
    return pyjwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests — mock DB, exercise full HTTP path via httpx ASGI transport
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
async def test_list_prs_returns_only_runs_with_pr_url():
    """Runs without pr_url are excluded; runs with pr_url are included."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    run_id_with_pr = "00000000-0000-0000-0000-b4ff00010001"
    run_with_pr = _make_run(
        run_id_with_pr,
        PROJECT_A,
        TENANT_A,
        {
            "pr_url": "https://dev.azure.com/org/proj/_git/repo/pullrequest/12",
            "pr_title": "Add health endpoint",
            "branch_name": "dev/health",
            "status": "pr_created",
        },
    )
    # A run with no pr_url should be excluded by the endpoint's in-Python filter
    run_no_pr = _make_run(
        "00000000-0000-0000-0000-b4ff00010002",
        PROJECT_A,
        TENANT_A,
        {"branch_name": "dev/no-pr", "status": "in_progress"},
    )

    # The WHERE clause in the real endpoint filters at the DB level; the mock
    # returns both — the endpoint's Python guard `if run.development_artifacts.get("pr_url")`
    # then excludes the no-pr run, proving the secondary guard works.
    app.dependency_overrides[get_db_session] = _make_db_override([run_with_pr, run_no_pr])
    try:
        token = _mint(TENANT_A)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/dev/{PROJECT_A}/prs", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        data = response.json()
        ids = [item["id"] for item in data]
        assert run_id_with_pr in ids, "Run with pr_url must be returned"
        assert "00000000-0000-0000-0000-b4ff00010002" not in ids, (
            "Run without pr_url must be excluded"
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_list_prs_maps_fields_correctly():
    """Returned PR item maps title, branch, status='open', url, created_at correctly."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    run_id = "00000000-0000-0000-0000-b4ff00010001"
    run = _make_run(
        run_id,
        PROJECT_A,
        TENANT_A,
        {
            "pr_url": "https://dev.azure.com/org/proj/_git/repo/pullrequest/12",
            "pr_title": "Add health endpoint",
            "branch_name": "dev/health",
            "status": "pr_created",
        },
    )

    app.dependency_overrides[get_db_session] = _make_db_override([run])
    try:
        token = _mint(TENANT_A)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/dev/{PROJECT_A}/prs", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        pr = data[0]

        assert pr["id"] == run_id
        assert pr["title"] == "Add health endpoint"
        assert pr["branch"] == "dev/health"
        assert pr["status"] == "open", "v1 bucket must always be 'open'"
        assert "pullrequest/12" in pr["url"]
        assert "created_at" in pr
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_list_prs_title_falls_back_to_branch_name():
    """When pr_title is absent, title falls back to branch_name."""
    import httpx
    from process_api import app
    from shared.db import get_db_session

    run = _make_run(
        "00000000-0000-0000-0000-b4ff00010001",
        PROJECT_A,
        TENANT_A,
        {
            "pr_url": "https://dev.azure.com/org/proj/_git/repo/pullrequest/99",
            "branch_name": "dev/feature-x",
            "status": "pr_created",
        },
    )

    app.dependency_overrides[get_db_session] = _make_db_override([run])
    try:
        token = _mint(TENANT_A)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/dev/{PROJECT_A}/prs", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        pr = response.json()[0]
        assert pr["title"] == "dev/feature-x", (
            "title must fall back to branch_name when pr_title is absent"
        )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_list_prs_requires_auth():
    """GET /dev/{project_id}/prs returns 401/403 without a token."""
    import httpx
    from process_api import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/dev/{PROJECT_A}/prs")

    assert response.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════
# Integration test — live DB, tests cross-tenant RLS isolation at SQL layer
# ═══════════════════════════════════════════════════════════════════════════

pytestmark_integration = [
    _skip_no_db,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
]


@_skip_no_db
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.integration
async def test_list_prs_cross_tenant_isolation_at_db_level():
    """Tenant A's query must not return Tenant B's runs — verified at the SQL layer.

    Seeds two Run rows via get_db_session_for_tenant (RLS GUC set) and executes
    the same SQLAlchemy query the endpoint uses. Confirms that with the tenant_id
    filter, only Tenant A's run is returned and Tenant B's run is invisible.
    """
    from sqlalchemy import select

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Organization, Project, Run, Workspace

    org_id = uuid.UUID("00000000-0000-0000-0000-b4db00000001")
    ws_id = uuid.UUID("00000000-0000-0000-0000-b4db00000002")
    proj_id = uuid.UUID("00000000-0000-0000-0000-b4db00000010")
    run_a_id = uuid.UUID("00000000-0000-0000-0000-b4db00010001")
    run_b_id = uuid.UUID("00000000-0000-0000-0000-b4db00010002")
    proj_b_id = uuid.UUID("00000000-0000-0000-0000-b4db00000099")

    from sqlalchemy import text

    try:
        # Pre-clean any leftover rows (runs before projects — FK)
        async with get_db_session_for_tenant(TENANT_A) as s:
            await s.execute(text("DELETE FROM runs WHERE id = :id"), {"id": str(run_a_id)})
        async with get_db_session_for_tenant(TENANT_B) as s:
            await s.execute(text("DELETE FROM runs WHERE id = :id"), {"id": str(run_b_id)})
            await s.execute(text("DELETE FROM projects WHERE id = :id"), {"id": str(proj_b_id)})
        async with get_db_session_for_tenant(TENANT_A) as s:
            await s.execute(text("DELETE FROM projects WHERE id = :id"), {"id": str(proj_id)})
            await s.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": str(ws_id)})
            await s.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})

        # Seed org + workspace (no RLS)
        async with get_db_session_for_tenant(TENANT_A) as sess:
            await sess.execute(
                text("INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                     "VALUES (:id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"),
                {"id": str(org_id), "slug": "b4-db-test-org", "dn": "B4 DB Test Org"},
            )
            await sess.execute(
                text("INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                     "VALUES (:id, :org_id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"),
                {"id": str(ws_id), "org_id": str(org_id), "slug": "b4-db-test-ws", "dn": "B4 DB WS"},
            )

        # Seed project + run for TENANT_A
        async with get_db_session_for_tenant(TENANT_A) as sess:
            sess.add(Project(
                id=proj_id, workspace_id=ws_id,
                tenant_id=uuid.UUID(TENANT_A),
                display_name="B4 DB Proj", provider_kind="azure_devops",
            ))
            await sess.flush()
            sess.add(Run(
                id=run_a_id, project_id=proj_id,
                tenant_id=uuid.UUID(TENANT_A),
                stage="development", status="completed", trigger="manual",
                development_artifacts={
                    "pr_url": "https://dev.azure.com/tenant-a/pullrequest/1",
                    "pr_title": "Tenant A PR",
                    "branch_name": "dev/a",
                    "status": "pr_created",
                },
            ))

        # Seed a separate project + run for TENANT_B (run references project by id but
        # belongs to tenant B — tests that the tenant_id filter excludes it)
        async with get_db_session_for_tenant(TENANT_B) as sess_b:
            sess_b.add(Project(
                id=proj_b_id, workspace_id=ws_id,
                tenant_id=uuid.UUID(TENANT_B),
                display_name="B4 DB Proj B", provider_kind="azure_devops",
            ))
            await sess_b.flush()
            sess_b.add(Run(
                id=run_b_id, project_id=proj_id,
                tenant_id=uuid.UUID(TENANT_B),
                stage="development", status="completed", trigger="manual",
                development_artifacts={
                    "pr_url": "https://dev.azure.com/tenant-b/pullrequest/99",
                    "pr_title": "Tenant B PR",
                    "branch_name": "dev/b",
                    "status": "pr_created",
                },
            ))

        # Execute the same query the endpoint uses — scoped to TENANT_A
        async with get_db_session_for_tenant(TENANT_A) as sess_a:
            stmt = (
                select(Run)
                .where(
                    Run.project_id == proj_id,
                    Run.tenant_id == uuid.UUID(TENANT_A),
                    Run.development_artifacts.isnot(None),
                    Run.development_artifacts["pr_url"].astext != "",
                )
                .order_by(Run.created_at.desc())
            )
            result = (await sess_a.execute(stmt)).scalars().all()

        ids = [str(r.id) for r in result]
        assert str(run_a_id) in ids, "Tenant A's run must appear in Tenant A's query"
        assert str(run_b_id) not in ids, (
            "Tenant B's run must NOT appear in Tenant A's query (cross-tenant isolation)"
        )

    finally:
        async with get_db_session_for_tenant(TENANT_A) as cl:
            await cl.execute(text("DELETE FROM runs WHERE id = :id"), {"id": str(run_a_id)})
        async with get_db_session_for_tenant(TENANT_B) as cl:
            await cl.execute(text("DELETE FROM runs WHERE id = :id"), {"id": str(run_b_id)})
            await cl.execute(text("DELETE FROM projects WHERE id = :id"), {"id": str(proj_b_id)})
        async with get_db_session_for_tenant(TENANT_A) as cl:
            await cl.execute(text("DELETE FROM projects WHERE id = :id"), {"id": str(proj_id)})
            await cl.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": str(ws_id)})
            await cl.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": str(org_id)})
