"""Platform-tier identity helpers + env-seed bootstrap (Phase 3, D-4 env seed)."""
from __future__ import annotations

import logging

from sqlalchemy import text

import config.env as env
from shared.auth.passwords import hash_password
from shared.db import get_db_session_superuser

logger = logging.getLogger(__name__)

PLATFORM_WILDCARD = "platform:*"


def is_platform_admin_email(email: str) -> bool:
    return bool(email) and email.strip().lower() in set(env.PLATFORM_ADMIN_EMAILS)


async def seed_platform_admins() -> None:
    """Idempotently seed env-listed platform admins (users row + platform_users row).

    Runs at startup. No-op unless BOTH PLATFORM_ADMIN_EMAILS and PLATFORM_ADMIN_PASSWORD
    are set. users/platform_users are GLOBAL (non-RLS) -> get_db_session_superuser is correct.
    Existing rows keep their password (ON CONFLICT DO NOTHING) -> re-seeding never clobbers.
    """
    if not env.PLATFORM_ADMIN_EMAILS or not env.PLATFORM_ADMIN_PASSWORD:
        return
    pw_hash = hash_password(env.PLATFORM_ADMIN_PASSWORD)
    async with get_db_session_superuser() as s:
        for email in env.PLATFORM_ADMIN_EMAILS:
            user_id = f"platform|{email}"
            await s.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, active) "
                    "VALUES (:id, :email, :ph, true) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": user_id, "email": email, "ph": pw_hash},
            )
            await s.execute(
                text(
                    "INSERT INTO platform_users (user_id, email, platform_role, active) "
                    "VALUES (:id, :email, 'platform_admin', true) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"id": user_id, "email": email},
            )
    logger.info("seed_platform_admins: ensured %d platform admin(s)", len(env.PLATFORM_ADMIN_EMAILS))


async def is_platform_user(user_id: str) -> bool:
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text("SELECT 1 FROM platform_users WHERE user_id = :id AND active = true"),
            {"id": user_id},
        )).first()
    return row is not None
