"""Phase 4 — org provisioning + member onboarding."""
import uuid as _uuid

import email_validator as _ev
import pytest

# Tests use reserved `.test` addresses (RFC 6761). email-validator (and thus
# pydantic EmailStr) rejects special-use domains by default; allow `.test` for
# the test process only — production validation is untouched.
_ev.SPECIAL_USE_DOMAIN_NAMES = [d for d in _ev.SPECIAL_USE_DOMAIN_NAMES if d != "test"]

# These tests provision real organizations and left every one of them behind. The
# conftest fixture diffs the organizations table around each test and removes what
# appeared.
pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    """Dispose the shared async engine after each test so asyncpg's pool isn't
    reused across pytest-asyncio's per-function event loops (avoids the
    'Event loop is closed' teardown artifact). Mirrors test_custom_roles.py."""
    yield
    from shared.db import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_provision_organization_creates_full_chain():
    from sqlalchemy import text
    from shared.db import get_db_session_superuser
    from shared.services.provisioning import provision_organization
    from shared.authz.resolver import resolve_permissions_for_user

    slug = f"acme-{_uuid.uuid4().hex[:8]}"
    result = await provision_organization(
        display_name="Acme Corp",
        slug=slug,
        admin_email=f"admin-{slug}@acme.test",
        admin_password="hunter2-strong",
    )
    async with get_db_session_superuser() as s:
        org = (await s.execute(text("SELECT id FROM organizations WHERE slug=:s"), {"s": slug})).first()
        assert org is not None
        ws = (await s.execute(text("SELECT id FROM workspaces WHERE organization_id=:o"), {"o": str(org.id)})).first()
        assert ws is not None
        usr = (await s.execute(text("SELECT id, password_hash FROM users WHERE id=:i"), {"i": result["admin_user_id"]})).first()
        assert usr is not None and usr.password_hash
    perms = await resolve_permissions_for_user(result["admin_user_id"], result["org_id"])
    assert "admin:*" in perms


@pytest.mark.asyncio
async def test_provision_duplicate_slug_raises():
    from shared.services.provisioning import provision_organization, SlugTakenError
    slug = f"dup-{_uuid.uuid4().hex[:8]}"
    await provision_organization("Dup", slug, f"a-{slug}@x.test", "pw-strong-1")
    with pytest.raises(SlugTakenError):
        await provision_organization("Dup2", slug, f"b-{slug}@x.test", "pw-strong-2")


@pytest.mark.asyncio
async def test_create_member_in_org():
    from shared.services.provisioning import provision_organization
    from shared.routers.admin import create_member, MemberCreateIn
    from shared.authz.resolver import resolve_permissions_for_user
    from fastapi import Request

    slug = f"mem-{_uuid.uuid4().hex[:8]}"
    org = await provision_organization("MemCo", slug, f"a-{slug}@x.test", "pw-strong-1")

    req = Request({"type": "http", "headers": []})
    req.state.tenant_id = org["org_id"]
    req.state.permissions = ["admin:*"]
    req.state.user_id = org["admin_user_id"]

    out = await create_member(
        req,
        MemberCreateIn(
            email=f"dev-{slug}@x.test",
            password="dev-strong-1",
            role_name="developer",
            workspace_id=org["workspace_id"],
        ),
    )
    perms = await resolve_permissions_for_user(out.user_id, org["org_id"])
    assert "run:create" in perms

    # duplicate email rejected (409)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await create_member(
            req,
            MemberCreateIn(
                email=f"dev-{slug}@x.test",
                password="another-pw-1",
                role_name="developer",
                workspace_id=org["workspace_id"],
            ),
        )
    assert ei.value.status_code == 409

    # unknown role rejected (422)
    with pytest.raises(HTTPException) as ei2:
        await create_member(
            req,
            MemberCreateIn(
                email=f"x-{slug}@x.test",
                password="another-pw-2",
                role_name="not_a_role",
                workspace_id=org["workspace_id"],
            ),
        )
    assert ei2.value.status_code == 422
