"""`/auth/me` reports what is true NOW, not what was true at login.

THE COMMENT WAS RIGHT AND THE CODE WAS WRONG. The endpoint carried a comment saying
"Re-resolved rather than read off the token: this endpoint exists to tell a client what
is true NOW, and a role assigned since the token was minted is exactly the case it is
asked about" — directly above `perms = getattr(request.state, "permissions", [])`, which
is the token's own claim as unpacked by the JWT middleware. It echoed the login-time
snapshot back while documenting the opposite. Only `platform_role` was really resolved.

Surfaced when `artifact:delete` arrived in migration 0039: the permission was in the
database, `resolve_permissions_for_user` returned it, and this endpoint still reported
the stale set from before the migration.

WHAT THIS DOES NOT CHANGE. `require_permission` reads the token claim, so a permission
granted after login is still not USABLE until the token is re-minted. This endpoint
reports; it does not authorise. Reporting the truth is what lets a client see that a
refresh is needed — which it previously could not.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.routers import auth_local as mod  # noqa: E402

UID = "user-1"
TID = "tenant-1"

# What the token was minted with, before the permission existed.
STALE = ["artifact:view", "run:create"]
# What the database says now.
FRESH = ["artifact:view", "run:create", "artifact:delete"]


def _request(token_perms):
    """A request whose state carries the TOKEN's claim — what the middleware sets."""
    return SimpleNamespace(
        state=SimpleNamespace(user_id=UID, tenant_id=TID, permissions=token_perms)
    )


@pytest.mark.unit
async def test_a_permission_granted_since_login_is_reported():
    """THE BUG. The token predates the grant; the database has it; /auth/me must say so."""
    with patch.object(mod, "resolve_permissions_for_user", AsyncMock(return_value=FRESH)), \
            patch.object(mod, "resolve_platform_role_for_user", AsyncMock(return_value="ba")):
        out = await mod.me(_request(STALE))

    assert "artifact:delete" in out.permissions
    assert out.permissions == FRESH


@pytest.mark.unit
async def test_the_token_claim_is_not_what_is_returned():
    """A permission REVOKED since login must also disappear, not linger because the
    token still carries it — the same bug in the direction that actually matters for
    security reporting."""
    with patch.object(mod, "resolve_permissions_for_user", AsyncMock(return_value=["artifact:view"])), \
            patch.object(mod, "resolve_platform_role_for_user", AsyncMock(return_value="contributor")):
        out = await mod.me(_request(FRESH))

    assert out.permissions == ["artifact:view"]
    assert "artifact:delete" not in out.permissions


@pytest.mark.unit
async def test_the_resolver_is_called_with_the_identity_from_request_state():
    """Identity comes from the verified JWT via request.state — never from a query or
    body param, which would let a caller enumerate someone else's permissions."""
    resolver = AsyncMock(return_value=FRESH)
    with patch.object(mod, "resolve_permissions_for_user", resolver), \
            patch.object(mod, "resolve_platform_role_for_user", AsyncMock(return_value="ba")):
        await mod.me(_request(STALE))

    resolver.assert_awaited_once_with(UID, TID)


@pytest.mark.unit
async def test_the_platform_role_is_derived_from_the_fresh_set_not_the_stale_one():
    """resolve_platform_role_for_user takes the permissions as an argument. Passing the
    token's set would make the role disagree with the permissions beside it."""
    role = AsyncMock(return_value="ba")
    with patch.object(mod, "resolve_permissions_for_user", AsyncMock(return_value=FRESH)), \
            patch.object(mod, "resolve_platform_role_for_user", role):
        await mod.me(_request(STALE))

    assert role.await_args.args[2] == FRESH


# -- failure modes -------------------------------------------------------------


@pytest.mark.unit
async def test_a_resolver_outage_is_a_503_not_an_empty_permission_set():
    """A client cannot tell "the database is down" from "you hold nothing", and would
    render an admin as powerless. Mirrors GET /auth/permissions."""
    from shared.authz.resolver import PermissionResolutionError

    with patch.object(mod, "resolve_permissions_for_user",
                      AsyncMock(side_effect=PermissionResolutionError("down"))):
        with pytest.raises(HTTPException) as e:
            await mod.me(_request(STALE))

    assert e.value.status_code == 503


@pytest.mark.unit
@pytest.mark.parametrize(
    ("uid", "tid"), [("", TID), (UID, ""), ("", "")]
)
async def test_missing_identity_fails_closed_without_calling_the_resolver(uid, tid):
    """public() opts out of the PERMISSION check, not authentication — absent identity
    here means no verified JWT, so there is nobody to resolve."""
    resolver = AsyncMock(return_value=FRESH)
    request = SimpleNamespace(
        state=SimpleNamespace(user_id=uid, tenant_id=tid, permissions=STALE)
    )
    with patch.object(mod, "resolve_permissions_for_user", resolver), \
            patch.object(mod, "resolve_platform_role_for_user", AsyncMock(return_value=None)):
        out = await mod.me(request)

    assert out.permissions == []
    resolver.assert_not_awaited()


@pytest.mark.unit
def test_the_endpoint_does_not_read_permissions_off_request_state():
    """A source guard: request.state.permissions is right there and reads as the
    obvious source, which is how the original bug happened."""
    import inspect

    src = inspect.getsource(mod.me)
    assert 'getattr(request.state, "permissions"' not in src
    assert "resolve_permissions_for_user" in src
