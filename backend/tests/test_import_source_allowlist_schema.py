import uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant


@pytest.mark.asyncio
async def test_import_source_allowlist_round_trips():
    tenant = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text(
                "INSERT INTO import_source_allowlist "
                "(id, tenant_id, source_pattern, label, created_by) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :p, :l, :cb)"
            ),
            {"id": row_id, "t": tenant, "p": "https://github.com/acme-org/", "l": "Acme skill library", "cb": "user-1"},
        )
        row = (
            await s.execute(
                text("SELECT source_pattern, label FROM import_source_allowlist WHERE id = CAST(:id AS uuid)"),
                {"id": row_id},
            )
        ).first()
    assert row is not None
    assert row.source_pattern == "https://github.com/acme-org/"
    assert row.label == "Acme skill library"
