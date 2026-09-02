import uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant


@pytest.mark.asyncio
async def test_agent_default_evaluations_round_trips():
    tenant = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text(
                "INSERT INTO agent_default_evaluations "
                "(id, tenant_id, target_type, target_id, agent_id, scope, result, "
                " score, signals, evaluator_id) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), 'profile', "
                " CAST(:tid AS uuid), 'requirements', 'org', 'pass', 0.75, "
                " '{}'::jsonb, :ev)"
            ),
            {"id": row_id, "t": tenant, "tid": target_id, "ev": "user-1"},
        )
        row = (
            await s.execute(
                text("SELECT result, score, evaluator_id FROM agent_default_evaluations WHERE id = CAST(:id AS uuid)"),
                {"id": row_id},
            )
        ).first()
    assert row is not None
    assert row.result == "pass"
    assert row.score == 0.75
    assert row.evaluator_id == "user-1"


@pytest.mark.asyncio
async def test_agent_default_evaluations_is_tenant_isolated():
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    async with get_db_session_for_tenant(tenant_a) as s:
        await s.execute(
            text(
                "INSERT INTO agent_default_evaluations "
                "(id, tenant_id, target_type, target_id, agent_id, scope, result, score, signals, evaluator_id) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), 'profile', gen_random_uuid(), "
                " 'requirements', 'org', 'pass', 1.0, '{}'::jsonb, 'user-1')"
            ),
            {"id": row_id, "t": tenant_a},
        )
    async with get_db_session_for_tenant(tenant_b) as s:
        rows = (
            await s.execute(text("SELECT id FROM agent_default_evaluations WHERE id = CAST(:id AS uuid)"), {"id": row_id})
        ).fetchall()
    assert rows == []
