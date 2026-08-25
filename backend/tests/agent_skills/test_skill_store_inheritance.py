"""Live-DB tests for list_skills_merged's ancestor-chain merge (Agent Studio
sub-project 1). Mirrors the live-DB convention in tests/test_model_grants.py —
random per-test tenant, raw INSERTs for setup, real service calls under test."""
import uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant
from shared.services import skill_store as store


async def _insert_skill(tenant_id: str, agent_id: str, scope: str, scope_id: str | None,
                         skill_key: str, display_name: str) -> None:
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO agent_skills "
                "(id, tenant_id, agent_id, scope, scope_id, skill_key, version, is_active, "
                " display_name, description, when_to_use, body, runtime, origin, created_by) "
                "VALUES (gen_random_uuid(), CAST(:t AS uuid), :a, :sc, "
                " CAST(:sid AS uuid), :k, 1, true, :dn, 'd', 'w', 'body text', 'llm', 'custom', 'tester')"
            ),
            {"t": tenant_id, "a": agent_id, "sc": scope, "sid": scope_id, "k": skill_key, "dn": display_name},
        )


@pytest.mark.asyncio
async def test_own_scope_wins_over_ancestors():
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "shared-key", "Org Version")
    await _insert_skill(tenant, "requirements", "workspace", ws_id, "shared-key", "BU Version")

    items = await store.list_skills_merged(
        tenant, "requirements", "workspace", ws_id, ancestor=[("org", None)],
    )
    hit = next(i for i in items if i["skill_key"] == "shared-key")
    assert hit["display_name"] == "BU Version"
    assert hit["origin_scope"] == "workspace"


@pytest.mark.asyncio
async def test_falls_through_to_ancestor_when_own_scope_empty():
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "org-only", "Org Only Skill")

    items = await store.list_skills_merged(
        tenant, "requirements", "workspace", ws_id, ancestor=[("org", None)],
    )
    hit = next(i for i in items if i["skill_key"] == "org-only")
    assert hit["origin_scope"] == "org"


@pytest.mark.asyncio
async def test_no_ancestor_arg_matches_todays_behavior():
    tenant = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "org-only", "Org Only Skill")
    ws_id = str(uuid.uuid4())

    items = await store.list_skills_merged(tenant, "requirements", "workspace", ws_id)
    assert not any(i["skill_key"] == "org-only" for i in items)


@pytest.mark.asyncio
async def test_get_skill_detail_includes_origin_scope():
    """Regression for the final-review finding: _list_item gained an origin_scope
    parameter, but get_skill_detail's own two call sites weren't updated to pass
    it — every skill create/update/view response was missing the key entirely,
    which the (required, if nullable) frontend Zod field turns into a hard parse
    failure. This pins the field's actual presence on get_skill_detail's output,
    not just list_skills_merged's."""
    tenant = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "my-skill", "My Skill")

    detail = await store.get_skill_detail(tenant, "requirements", "org", None, "custom", "my-skill")
    assert detail is not None
    assert "origin_scope" in detail
    assert detail["origin_scope"] == "org"
    # Resolved at its own exact scope -> genuinely editable/deletable here.
    assert detail["editable"] is True
    assert detail["deletable"] is True


@pytest.mark.asyncio
async def test_inherited_item_in_list_is_not_editable_or_deletable():
    """An item list_skills_merged surfaces from an ancestor tier is NOT editable
    or deletable at the tier you asked about — update/delete are exact-scope
    operations that would 404 against the ancestor's row; only Override (create a
    new row at your own tier) is the correct action for such an item."""
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "org-only", "Org Only Skill")

    items = await store.list_skills_merged(
        tenant, "requirements", "workspace", ws_id, ancestor=[("org", None)],
    )
    hit = next(i for i in items if i["skill_key"] == "org-only")
    assert hit["origin_scope"] == "org"
    assert hit["editable"] is False
    assert hit["deletable"] is False


@pytest.mark.asyncio
async def test_own_scope_item_in_list_is_editable_and_deletable():
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "workspace", ws_id, "own-skill", "Own Skill")

    items = await store.list_skills_merged(tenant, "requirements", "workspace", ws_id)
    hit = next(i for i in items if i["skill_key"] == "own-skill")
    assert hit["origin_scope"] == "workspace"
    assert hit["editable"] is True
    assert hit["deletable"] is True


@pytest.mark.asyncio
async def test_toggle_precedence_nearest_wins_across_scopes():
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "shared-key", "Org Version")
    # Org-level toggle turns it OFF; this workspace's own toggle turns it back ON —
    # nearest (own scope) should win.
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO agent_skill_toggles "
            "(id, tenant_id, agent_id, scope, scope_id, origin, skill_key, enabled, updated_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), 'requirements', 'org', NULL, "
            " 'custom', 'shared-key', false, 'tester')"
        ), {"t": tenant})
        await s.execute(text(
            "INSERT INTO agent_skill_toggles "
            "(id, tenant_id, agent_id, scope, scope_id, origin, skill_key, enabled, updated_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), 'requirements', 'workspace', "
            " CAST(:sid AS uuid), 'custom', 'shared-key', true, 'tester')"
        ), {"t": tenant, "sid": ws_id})

    items = await store.list_skills_merged(
        tenant, "requirements", "workspace", ws_id, ancestor=[("org", None)],
    )
    hit = next(i for i in items if i["skill_key"] == "shared-key")
    assert hit["enabled"] is True
