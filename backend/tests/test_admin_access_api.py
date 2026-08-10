"""Unit tests for the /admin RBAC management surface (self-serve role assignment).

Tests the _require_admin gate and the static /roles catalog directly — no HTTP stack
or live DB required (mirrors tests/test_m7_rbac.py's request-state-stub style). The
grant/revoke DB write paths run through grant_role/revoke_role, which are exercised by
the live smoke/exit-gate; here we cover the authz gate and response shaping.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from shared.authz.permissions import ALL_ROLES
from shared.routers.admin import _require_admin, list_roles


def _request(permissions):
    return SimpleNamespace(
        state=SimpleNamespace(
            permissions=permissions,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="u-test",
        )
    )


@pytest.mark.asyncio
async def test_require_admin_allows_admin_wildcard():
    """admin:* passes the gate (no exception)."""
    await _require_admin(_request(["admin:*"]))


@pytest.mark.asyncio
async def test_require_admin_denies_non_admin():
    """A non-admin permission set is rejected with a generic 403."""
    with pytest.raises(HTTPException) as exc:
        await _require_admin(_request(["run:create", "artifact:view"]))
    assert exc.value.status_code == 403
    assert exc.value.detail == "Forbidden"  # no permission-name leak


@pytest.mark.asyncio
async def test_require_admin_denies_empty_and_absent():
    """Deny-by-default: empty list AND absent attribute both 403."""
    with pytest.raises(HTTPException) as exc:
        await _require_admin(_request([]))
    assert exc.value.status_code == 403

    bare = SimpleNamespace(state=SimpleNamespace(tenant_id="t", user_id="u"))
    with pytest.raises(HTTPException) as exc2:
        await _require_admin(bare)
    assert exc2.value.status_code == 403


def test_require_admin_carries_bootscan_sentinel():
    """D-05 boot scan recognises the route as consciously protected."""
    assert getattr(_require_admin, "__rbac_require_permission__", False) is True


@pytest.mark.asyncio
async def test_list_roles_matches_backend_matrix():
    """GET /admin/roles returns the canonical role catalog with permissions."""
    roles = await list_roles()
    assert {r.name for r in roles} == set(ALL_ROLES)
    by_name = {r.name: r for r in roles}
    assert by_name["admin"].permissions == ["admin:*"]
    assert "artifact:approve_design" in by_name["tech_lead"].permissions
    # Every role carries a human label.
    assert all(r.label for r in roles)
