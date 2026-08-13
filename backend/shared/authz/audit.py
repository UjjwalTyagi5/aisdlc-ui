"""Audit records for RBAC decisions — who granted what to whom, and who was refused.

Role grants and revocations are the highest-leverage writes on the platform: they are
how someone comes to hold authority at all. Until now they wrote nothing to
audit_events, so "who gave this person org_admin, and when" had no answer.

TWO KINDS OF RECORD
-------------------
`record_rbac_change`  a write happened — a binding created or removed, a role defined.
`record_access_denied` a request was refused — the 403 trail.

Denials are worth keeping for the same reason a door log keeps failed badge swipes:
one is noise, forty in a minute from one account is the only warning you will get.

WRITTEN IN THE CALLER'S TRANSACTION
-----------------------------------
`record_rbac_change` takes the session the change is being made in, so the grant and
its audit row commit or roll back together. An audit write in its own transaction can
succeed while the change it describes is rolled back — recording something that never
happened — or the reverse, which is worse.

Denials cannot do that: the refusal happens in a dependency, before any route session
exists, and the request is about to fail anyway. Those get their own short-lived
session and are best-effort — see `record_access_denied`.

NEVER RAISES
------------
An audit failure must not convert a successful authorization into a 500. Both helpers
swallow and log their own errors: losing one audit row is bad, refusing a legitimate
grant because the trail was briefly unavailable is worse, and the log line preserves
the evidence either way.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Event type vocabulary. Kept as constants because these strings are queried by the
# audit UI and by compliance exports; a typo in a literal would silently produce a
# category nothing reads.
RBAC_ROLE_GRANTED = "rbac.role.granted"
RBAC_ROLE_REVOKED = "rbac.role.revoked"
RBAC_CUSTOM_ROLE_GRANTED = "rbac.custom_role.granted"
RBAC_CUSTOM_ROLE_CREATED = "rbac.custom_role.created"
RBAC_CUSTOM_ROLE_DELETED = "rbac.custom_role.deleted"
RBAC_MEMBER_CREATED = "rbac.member.created"
ACCESS_DENIED = "access.denied"


async def record_rbac_change(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_id: Optional[str],
    event_type: str,
    subject_id: str,
    scope_kind: str,
    scope_id: str,
    role: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Append one RBAC change to the audit trail, in the caller's transaction.

    `subject_id` is who the change is ABOUT; `actor_id` is who made it. They are
    frequently the same person and the difference is the entire point of the record.
    """
    payload: dict[str, Any] = {
        "subject_id": subject_id,
        "scope_kind": scope_kind,
        "scope_id": str(scope_id),
    }
    if role:
        payload["role"] = role
    if extra:
        payload.update(extra)

    try:
        await session.execute(
            text(
                "INSERT INTO audit_events "
                "  (id, tenant_id, actor_id, event_type, resource_type, resource_id, payload) "
                "VALUES (:id, :t, :actor, :et, :rt, :rid, CAST(:p AS jsonb))"
            ),
            {
                "id": str(_uuid.uuid4()),
                "t": str(tenant_id),
                # NULL actor = a system action (startup seeding, a worker), which is a
                # meaningful value rather than a missing one. Recorded as NULL rather
                # than a placeholder string so it cannot collide with a real user id.
                "actor": actor_id or None,
                "et": event_type,
                "rt": "role_binding",
                "rid": subject_id,
                "p": _json(payload),
            },
        )
    except Exception:
        logger.exception(
            "audit write FAILED (change not blocked): %s subject=%s scope=%s:%s actor=%s",
            event_type, subject_id, scope_kind, scope_id, actor_id,
        )


async def record_access_denied(
    *,
    tenant_id: str,
    actor_id: Optional[str],
    permission: str,
    scope_kind: Optional[str] = None,
    scope_id: Optional[str] = None,
    route: Optional[str] = None,
) -> None:
    """Append a 403 to the audit trail. Best-effort, in its own session.

    Deliberately not in the caller's transaction: a denial happens in a dependency
    before any route session exists, and the request is about to fail regardless — so
    there is no transaction to join and nothing to keep consistent with.

    No-ops without a tenant. A denial before tenant resolution has no trail to land in,
    and audit_events is tenant-anchored under RLS; the server log still carries it.
    """
    if not tenant_id:
        logger.info(
            "access denied (no tenant, not audited): actor=%s permission=%s route=%s",
            actor_id, permission, route,
        )
        return

    payload: dict[str, Any] = {"permission": permission}
    if scope_kind:
        payload["scope_kind"] = scope_kind
    if scope_id:
        payload["scope_id"] = str(scope_id)
    if route:
        payload["route"] = route

    try:
        from shared.db import get_db_session_for_tenant  # noqa: PLC0415 - avoids import cycle

        async with get_db_session_for_tenant(str(tenant_id)) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_events "
                    "  (id, tenant_id, actor_id, event_type, resource_type, resource_id, payload) "
                    "VALUES (:id, :t, :actor, :et, :rt, :rid, CAST(:p AS jsonb))"
                ),
                {
                    "id": str(_uuid.uuid4()),
                    "t": str(tenant_id),
                    "actor": actor_id or None,
                    "et": ACCESS_DENIED,
                    "rt": scope_kind or "route",
                    "rid": str(scope_id) if scope_id else (route or permission),
                    "p": _json(payload),
                },
            )
    except Exception:
        # A denial that cannot be recorded must still be a denial. The request is
        # already being refused; this only loses the row.
        logger.exception(
            "audit write FAILED for denial: actor=%s permission=%s route=%s",
            actor_id, permission, route,
        )


def _json(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str, sort_keys=True)
