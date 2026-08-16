"""Single-organization bootstrap — seeds the one org and its admin(s) at startup.

The platform hosts exactly ONE organization. No route, no console and no signup form
creates another: this module is the only thing that creates an organization at all,
and only when the database has none under DEFAULT_ORG_SLUG. Self-serve signup joins
this same org (see shared/routers/auth_local.py) carrying no role bindings.

Replaces the retired platform tier. The env-listed admin used to be a tenant-LESS
platform_users row holding `platform:*`; it is now an ordinary org user holding
org_admin at organization scope, so it resolves permissions through the same RBAC
path as everyone else and no code has a second, privileged identity model to honour.

Everything here is idempotent — it runs on every boot and converges on the same state.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import text

import config.env as env
from shared.auth.passwords import hash_password
from shared.authz.grant import grant_role
from shared.db import get_db_session_superuser

logger = logging.getLogger(__name__)

ORG_ADMIN_ROLE = "org_admin"


def is_org_admin_email(email: str) -> bool:
    """True when this email is one the env designates as a bootstrap org admin."""
    return bool(email) and email.strip().lower() in set(env.ORG_ADMIN_EMAILS)


async def get_default_org_id() -> str | None:
    """Id of the single organization, or None before it has been seeded."""
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text("SELECT id FROM organizations WHERE slug = :s"),
            {"s": env.DEFAULT_ORG_SLUG},
        )).first()
    return str(row.id) if row else None


async def _ensure_default_business_unit(org_id: str) -> None:
    """Give the org one business unit so project/BU-scoped screens have somewhere to land."""
    async with get_db_session_superuser() as s:
        existing = (await s.execute(
            text("SELECT 1 FROM workspaces WHERE organization_id = :org LIMIT 1"),
            {"org": org_id},
        )).first()
        if existing:
            return
        await s.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:id, :org, 'default', 'Default Business Unit')"
            ),
            {"id": str(_uuid.uuid4()), "org": org_id},
        )
    logger.info("bootstrap: created default business unit for org %s", org_id)


async def ensure_default_organization() -> str:
    """Create the one organization + its default business unit if absent; return its id."""
    org_id = await get_default_org_id()
    if org_id is None:
        async with get_db_session_superuser() as s:
            await s.execute(
                text(
                    "INSERT INTO organizations (id, slug, display_name) "
                    "VALUES (:id, :slug, :name) ON CONFLICT (slug) DO NOTHING"
                ),
                {"id": str(_uuid.uuid4()), "slug": env.DEFAULT_ORG_SLUG,
                 "name": env.DEFAULT_ORG_NAME},
            )
        # Re-read rather than trusting the id we generated: a concurrent boot may have
        # won the insert, in which case ours was silently discarded by ON CONFLICT.
        org_id = await get_default_org_id()
        if org_id is None:  # pragma: no cover - only reachable if the insert was rolled back
            raise RuntimeError(
                f"bootstrap: could not create or find organization '{env.DEFAULT_ORG_SLUG}'"
            )
        logger.info("bootstrap: created organization %s (%s)", env.DEFAULT_ORG_SLUG, org_id)

    await _ensure_default_business_unit(org_id)
    return org_id


async def _ensure_admin_user(email: str, password: str, org_id: str) -> str:
    """Return the user id for email, creating it or attaching it to the org as needed."""
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text("SELECT id FROM users WHERE lower(email) = :e ORDER BY created_at LIMIT 1"),
            {"e": email},
        )).first()
        if row is not None:
            # An account seeded before this org existed — or by the retired platform tier,
            # which was deliberately tenant-LESS — carries tenant_id NULL and would resolve
            # to zero permissions. Re-point it at the org; leave the password alone so a
            # password the admin has since changed is never silently reverted to the env one.
            await s.execute(
                text(
                    "UPDATE users SET tenant_id = :t WHERE id = :i "
                    "AND tenant_id IS DISTINCT FROM :t"
                ),
                {"t": org_id, "i": row.id},
            )
            return row.id

        user_id = str(_uuid.uuid4())
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                "VALUES (:i, :e, :p, :t, true)"
            ),
            {"i": user_id, "e": email, "p": hash_password(password), "t": org_id},
        )
        return user_id


async def seed_org_admins() -> None:
    """Seed the organization and bind every env-listed email to it as org_admin.

    No-op unless BOTH ORG_ADMIN_EMAILS and ORG_ADMIN_PASSWORD are set — an empty
    config must not silently create a nameless org.

    An existing account keeps its current password: re-seeding tops up the org
    membership and the role binding, never the credential.
    """
    if not env.ORG_ADMIN_EMAILS or not env.ORG_ADMIN_PASSWORD:
        logger.info(
            "seed_org_admins: ORG_ADMIN_EMAILS/ORG_ADMIN_PASSWORD not set — nothing seeded"
        )
        return

    org_id = await ensure_default_organization()
    for email in env.ORG_ADMIN_EMAILS:
        user_id = await _ensure_admin_user(email, env.ORG_ADMIN_PASSWORD, org_id)
        # org_admin binds at ORGANIZATION scope, never at a business unit — binding it to
        # one unit would hide every other unit from the org's own administrator.
        await grant_role(
            user_id, org_id, ORG_ADMIN_ROLE,
            tenant_id=org_id, scope_kind="organization",
        )
    logger.info(
        "seed_org_admins: ensured %d org admin(s) on '%s'",
        len(env.ORG_ADMIN_EMAILS), env.DEFAULT_ORG_SLUG,
    )
