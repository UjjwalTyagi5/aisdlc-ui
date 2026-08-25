"""One-off: backfill integration_grants (kind='model_provider') from existing
org_model_grants rows with visibility='specific'. Global-visibility grants need no
action — see the module docstring in the design spec, §7.

org_model_grants is NOT modified or deleted by this script — it keeps its own
write path (spec §3.3 amendment: Org Admin still curates per-model access via
org_model_grants). This script only ADDS rows to integration_grants so that a BU
which already had a per-model grant the old way does not lose provider-level
access under the new gate (Task 4's POST /model/providers now requires a
model_provider grant to exist before a BU-scoped key can be added).

Idempotent: re-running inserts nothing new for a pair already migrated (ON CONFLICT
DO NOTHING against integration_grants' composite primary key).

business_unit_ids is read as jsonb (via jsonb_array_elements_text + a uuid cast),
not unnest()'d as a native array — migration 0017_model_gateway_cascade converted
the column from uuid[] to jsonb to match what model_grants.py's get_org_grants/
set_org_grants actually read and write.

    python -m scripts.migrate_org_model_grants_to_provider_grants [--tenant TENANT_ID]

Omit --tenant to migrate every tenant.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser


async def migrate(tenant_id: str) -> int:
    """Returns the number of integration_grants rows written."""
    async with get_db_session_for_tenant(tenant_id) as s:
        # NOTE: business_unit_ids is jsonb (migration 0017_model_gateway_cascade
        # converted it from a native uuid[] to jsonb to match what model_grants.py's
        # get_org_grants/set_org_grants actually read and write). unnest() only works
        # on a real Postgres array, so this expands the jsonb array via
        # jsonb_array_elements_text + a uuid cast instead of the array-typed unnest()
        # the original draft of this script assumed.
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT g.provider, "
                    "  jsonb_array_elements_text(g.business_unit_ids)::uuid AS workspace_id "
                    "FROM org_model_grants g "
                    "WHERE g.tenant_id = CAST(:t AS uuid) AND g.visibility = 'specific'"
                ),
                {"t": tenant_id},
            )
        ).fetchall()
        written = 0
        for provider, workspace_id in rows:
            result = await s.execute(
                text(
                    "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id, granted_by) "
                    "VALUES (CAST(:t AS uuid), 'model_provider', :p, CAST(:w AS uuid), 'migration-0028') "
                    "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO NOTHING"
                ),
                {"t": tenant_id, "p": provider, "w": str(workspace_id)},
            )
            written += result.rowcount
        return written


async def _all_tenant_ids() -> list[str]:
    async with get_db_session_superuser() as s:
        rows = (await s.execute(text("SELECT id FROM organizations"))).fetchall()
    return [str(r.id) for r in rows]


async def main(tenant: str | None) -> None:
    tenant_ids = [tenant] if tenant else await _all_tenant_ids()
    total = 0
    for t in tenant_ids:
        n = await migrate(t)
        total += n
        print(f"  tenant {t}: {n} grant(s) written")
    print(f"\n{total} total integration_grants row(s) written across {len(tenant_ids)} tenant(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=None, help="Migrate only this tenant id")
    args = parser.parse_args()
    asyncio.run(main(args.tenant))
