"""Organization provisioning (Phase 4). Creates Org + default Workspace + first
org-admin user (creator-set password) + org_admin assignment.

organizations/workspaces/users are GLOBAL (non-RLS) -> superuser session.
user_workspace_roles is FORCE-RLS -> grant_role sets the tenant GUC.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import text

from shared.auth.passwords import hash_password
from shared.authz.grant import grant_role
from shared.db import get_db_session_superuser

logger = logging.getLogger(__name__)


class SlugTakenError(Exception):
    """Raised when the organization slug already exists."""


class ProvisioningError(Exception):
    """Raised on an invalid provisioning request (bad password, etc.)."""


async def provision_organization(
    display_name: str,
    slug: str,
    admin_email: str,
    admin_password: str,
) -> dict:
    display_name = (display_name or "").strip()
    slug = (slug or "").strip().lower()
    admin_email = (admin_email or "").strip().lower()
    if not display_name or not slug or not admin_email:
        raise ProvisioningError("display_name, slug, and admin_email are required")
    if len(admin_password or "") < 8:
        raise ProvisioningError("admin password must be at least 8 characters")

    org_id = _uuid.uuid4()
    ws_id = _uuid.uuid4()
    admin_user_id = str(_uuid.uuid4())
    pw_hash = hash_password(admin_password)

    async with get_db_session_superuser() as s:
        exists = (await s.execute(
            text("SELECT 1 FROM organizations WHERE slug = :s"), {"s": slug}
        )).first()
        if exists:
            raise SlugTakenError(f"slug '{slug}' is taken")
        await s.execute(
            text("INSERT INTO organizations (id, slug, display_name) VALUES (:id, :slug, :name)"),
            {"id": str(org_id), "slug": slug, "name": display_name},
        )
        await s.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:id, :org, 'default', 'Default Workspace')"
            ),
            {"id": str(ws_id), "org": str(org_id)},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                "VALUES (:id, :email, :ph, :tid, true)"
            ),
            {"id": admin_user_id, "email": admin_email, "ph": pw_hash, "tid": str(org_id)},
        )

    await grant_role(admin_user_id, ws_id, "org_admin", tenant_id=str(org_id))

    logger.info("provisioned org slug=%s id=%s admin=%s", slug, org_id, admin_email)
    return {
        "org_id": str(org_id),
        "slug": slug,
        "display_name": display_name,
        "workspace_id": str(ws_id),
        "admin_email": admin_email,
        "admin_user_id": admin_user_id,
    }
