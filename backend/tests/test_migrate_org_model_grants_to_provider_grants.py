"""backend/tests/test_migrate_org_model_grants_to_provider_grants.py — Task 7.

Covers the one-off backfill from existing `org_model_grants` (visibility='specific')
rows into `integration_grants` (kind='model_provider'), per spec §7. `visibility=
'global'` rows need no action — they already reach every BU regardless of the new
provider-level grant gate (Task 4).
"""
import uuid

import pytest
from sqlalchemy import text

from tests.test_model_grants import _seed_org_workspace_project

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def seeded_org_with_specific_grant():
    """One org, one workspace, one org_model_grants row with visibility='specific'
    naming that workspace."""
    from shared.services import model_grants as mg

    tenant_id = str(uuid.uuid4())
    ws_id, _ = await _seed_org_workspace_project(tenant_id, "Payments")
    await mg.set_org_grants(
        tenant_id,
        [{
            "provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": None,
            "visibility": "specific", "business_unit_ids": [ws_id],
        }],
        created_by="admin1",
    )
    return tenant_id, ws_id


@pytest.fixture
async def seeded_org_with_global_grant():
    """One org, one workspace, one org_model_grants row with visibility='global' —
    no business_unit_ids needed, since a global grant already reaches every BU."""
    from shared.services import model_grants as mg

    tenant_id = str(uuid.uuid4())
    await _seed_org_workspace_project(tenant_id, "Lending")
    await mg.set_org_grants(
        tenant_id,
        [{
            "provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": None,
            "visibility": "global", "business_unit_ids": [],
        }],
        created_by="admin1",
    )
    return tenant_id


@pytest.mark.asyncio
async def test_migrates_specific_visibility_grants_to_provider_grants(seeded_org_with_specific_grant):
    from scripts.migrate_org_model_grants_to_provider_grants import migrate

    tenant_id, workspace_id = seeded_org_with_specific_grant
    await migrate(tenant_id)

    from shared.db import get_db_session_for_tenant
    async with get_db_session_for_tenant(tenant_id) as s:
        row = (await s.execute(
            text(
                "SELECT 1 FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = 'model_provider' AND target_ref = 'anthropic' "
                "  AND workspace_id = CAST(:w AS uuid)"
            ),
            {"t": tenant_id, "w": workspace_id},
        )).first()
    assert row is not None


@pytest.mark.asyncio
async def test_global_visibility_grants_are_not_migrated(seeded_org_with_global_grant):
    """Global grants already reach every BU via org-wide model_providers rows —
    nothing to backfill, per spec §7 step 1."""
    from scripts.migrate_org_model_grants_to_provider_grants import migrate

    tenant_id = seeded_org_with_global_grant
    await migrate(tenant_id)

    from shared.db import get_db_session_for_tenant
    async with get_db_session_for_tenant(tenant_id) as s:
        count = (await s.execute(
            text(
                "SELECT count(*) FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = 'model_provider'"
            ),
            {"t": tenant_id},
        )).scalar()
    assert count == 0


@pytest.mark.asyncio
async def test_migration_is_idempotent(seeded_org_with_specific_grant):
    """Re-running the migration for the same tenant writes nothing new and does not
    error — the whole point of ON CONFLICT DO NOTHING against the composite PK."""
    from scripts.migrate_org_model_grants_to_provider_grants import migrate

    tenant_id, workspace_id = seeded_org_with_specific_grant
    first = await migrate(tenant_id)
    second = await migrate(tenant_id)

    assert first == 1
    assert second == 0

    from shared.db import get_db_session_for_tenant
    async with get_db_session_for_tenant(tenant_id) as s:
        count = (await s.execute(
            text(
                "SELECT count(*) FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = 'model_provider' AND target_ref = 'anthropic' "
                "  AND workspace_id = CAST(:w AS uuid)"
            ),
            {"t": tenant_id, "w": workspace_id},
        )).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_tenant_with_no_org_model_grants_is_a_noop():
    """A tenant with zero org_model_grants rows must not crash and must write nothing."""
    from scripts.migrate_org_model_grants_to_provider_grants import migrate

    tenant_id = str(uuid.uuid4())
    await _seed_org_workspace_project(tenant_id, "Empty Org")

    written = await migrate(tenant_id)

    assert written == 0
