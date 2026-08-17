"""The static RBAC catalogue: its shape, its data-entry order, and its boot guard.

The boot guard is the point of this file. roles / permissions / role_permissions are
GLOBAL tables with no tenant_id and no RLS, so a single direct INSERT escalates every
holder of that role in every tenant with no application code involved. Nothing else
in the system would notice.
"""
import pytest
from sqlalchemy import text

from shared.authz.catalog import (
    CATALOG_PERMISSIONS,
    ROLE_CATALOG,
    RbacCatalogDriftError,
    assert_rbac_catalog,
    seed_rbac_catalog,
    verify_rbac_catalog,
)
from shared.authz.permissions import ALL_ROLES
from shared.db import get_db_session_superuser


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


# ── shape (no database) ──────────────────────────────────────────────────────

def test_every_role_has_a_complete_record():
    """A role missing a label, tier or scope must not be representable."""
    for spec in ROLE_CATALOG:
        assert spec.name and spec.label and spec.description
        assert spec.tier in {"governance", "delivery"}
        assert spec.scope in {"organization", "business_unit", "project", "configurable"}
        assert isinstance(spec.permissions, tuple), "permissions must be immutable"


def test_catalog_covers_exactly_the_roles_the_app_enforces_with():
    """Drift here would seed one set of roles and authorize against another."""
    assert {s.name for s in ROLE_CATALOG} == set(ALL_ROLES)


def test_catalog_permissions_are_a_superset_of_every_granted_permission():
    """role_permissions.permission_name is an FK — every granted string must exist."""
    granted = {p for spec in ROLE_CATALOG for p in spec.permissions}
    assert granted <= set(CATALOG_PERMISSIONS)


def test_wildcards_are_present_for_the_foreign_key():
    """admin:* is granted to org_admin, so it must satisfy the FK even though it is
    deliberately absent from the grantable leaf catalogue offered to custom roles."""
    assert "admin:*" in CATALOG_PERMISSIONS


# ── data entry + verification (database) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_is_idempotent_and_leaves_no_drift():
    async with get_db_session_superuser() as s:
        await seed_rbac_catalog(s)
        first = await seed_rbac_catalog(s)
        # A second run must change nothing — every step is an upsert or a reconcile.
        assert sum(first.values()) == 0, f"second seed still changed rows: {first}"
        assert await verify_rbac_catalog(s) == []


@pytest.mark.asyncio
async def test_verify_detects_an_added_permission():
    """The dangerous direction: a role silently granted something extra."""
    async with get_db_session_superuser() as s:
        await seed_rbac_catalog(s)
        await s.execute(text(
            "INSERT INTO role_permissions (role_name, permission_name) "
            "VALUES ('developer', 'member:manage') ON CONFLICT DO NOTHING"
        ))
        drift = await verify_rbac_catalog(s)
        assert any("EXTRA permission 'member:manage'" in d for d in drift), drift

        with pytest.raises(RbacCatalogDriftError):
            await assert_rbac_catalog(s)

        # autorepair reconciles rather than raising, and clears the drift.
        await assert_rbac_catalog(s, autorepair=True)
        assert await verify_rbac_catalog(s) == []


@pytest.mark.asyncio
async def test_verify_detects_a_removed_permission():
    async with get_db_session_superuser() as s:
        await seed_rbac_catalog(s)
        await s.execute(text(
            "DELETE FROM role_permissions WHERE role_name='developer' AND permission_name='run:create'"
        ))
        drift = await verify_rbac_catalog(s)
        assert any("missing permission 'run:create'" in d for d in drift), drift
        await seed_rbac_catalog(s)
        assert await verify_rbac_catalog(s) == []


@pytest.mark.asyncio
async def test_seed_order_satisfies_the_foreign_keys():
    """role_permissions references roles AND permissions, so it must be written last.

    Asserted by deleting an edge and its endpoint, then reseeding: if the order were
    wrong this raises ForeignKeyViolation rather than repairing.
    """
    async with get_db_session_superuser() as s:
        await seed_rbac_catalog(s)
        await s.execute(text("DELETE FROM role_permissions WHERE role_name='qa'"))
        await s.execute(text("DELETE FROM roles WHERE name='qa'"))
        await seed_rbac_catalog(s)
        assert await verify_rbac_catalog(s) == []
