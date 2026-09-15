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
# PRD §34.9: "Export is itself an audited event." Taking the trail is an act on the
# trail, and the only one that used to leave no mark on it.
AUDIT_EXPORTED = "audit.exported"

RBAC_ROLE_GRANTED = "rbac.role.granted"
RBAC_ROLE_REVOKED = "rbac.role.revoked"
RBAC_CUSTOM_ROLE_GRANTED = "rbac.custom_role.granted"
RBAC_CUSTOM_ROLE_CREATED = "rbac.custom_role.created"
RBAC_CUSTOM_ROLE_DELETED = "rbac.custom_role.deleted"
RBAC_MEMBER_CREATED = "rbac.member.created"
ACCESS_DENIED = "access.denied"


# The tables that can name each thing an audit row points at. Deliberately the same
# mapping `shared/routers/audit.py` reads with, because a name captured at write time
# and a name resolved at read time must agree about where to look.
_NAME_SOURCES: dict[str, tuple[str, str]] = {
    "organization": ("organizations", "display_name"),
    "business_unit": ("workspaces", "display_name"),
    "workspace": ("workspaces", "display_name"),
    "project": ("projects", "display_name"),
}


async def _name_of(session: AsyncSession, kind: str, ident: Optional[str]) -> Optional[str]:
    """The display name of one scope, looked up NOW so the record keeps it forever.

    WHY THE RECORD CANNOT RELY ON A LOOKUP LATER. Read-time resolution joins the
    trail to the tables it references, which works right up until one of those rows is
    deleted — and then eight role grants render as `Project fa5e4ce1…`, an id nothing
    can turn back into a name because no name was ever written down. That is exactly
    what happened here: `scripts/seed_dev_personas.py` created "Core ledger — Java 8
    to 21", granted seven roles in it, and the project was later dropped. The grants
    are still facts and they are no longer legible.

    Deleting a project must not retroactively blind the trail that governs it, so the
    name is captured at the moment of the write. PRD §34.9 asks for a record that is
    legible "without guessing"; a foreign key into mutable data is a guess with good
    odds, and this is the table where good odds are not the standard.

    Best-effort and never raises: a name we could not read is a name the record does
    without, and `AuditEventOut` still falls back to resolving it at read time.
    """
    source = _NAME_SOURCES.get(kind or "")
    if not source or not ident:
        return None
    table, column = source
    try:
        row = (await session.execute(
            text(f"SELECT {column} AS name FROM {table} WHERE id::text = :i"),
            {"i": str(ident)},
        )).first()
        return str(row.name) if row is not None and row.name else None
    except Exception:
        logger.debug("audit: could not name %s %s", kind, ident, exc_info=True)
        return None


async def _actor_email(session: AsyncSession, actor_id: Optional[str]) -> Optional[str]:
    """The actor's email, captured for the same reason — people leave."""
    if not actor_id:
        return None
    try:
        row = (await session.execute(
            text("SELECT email FROM users WHERE id::text = :i"), {"i": str(actor_id)},
        )).first()
        return str(row.email) if row is not None and row.email else None
    except Exception:
        logger.debug("audit: could not name actor %s", actor_id, exc_info=True)
        return None


async def capture_names(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    actor_id: Optional[str] = None,
    resource_name: Optional[str] = None,
) -> dict[str, Any]:
    """`payload` with its `*_name` keys filled in, looked up NOW.

    THE ONE ENTRY POINT for every audit writer that is not `record_rbac_change`.
    Artifacts, runs, deployments and artifact versions all build their own
    `AuditEvent` rows, and all of them stored ids alone — so all of them had the
    failure this module documents: delete the project and the record of what was
    approved inside it stops naming it, permanently, because `audit_events` cannot be
    updated afterwards.

    It reads the payload the caller already built rather than taking six arguments:
    every one of these writers puts `project_id` in it, RBAC writers put
    `scope_kind` + `scope_id`, and that is enough to know what to name. A caller that
    names none of those gets its actor named and nothing else, which is correct.

    Never raises and never overwrites: a key the caller set deliberately wins, and a
    lookup that fails leaves the record exactly as it would have been.
    """
    out = dict(payload)

    kind, ident = None, None
    if out.get("scope_kind") and out.get("scope_id"):
        kind, ident = str(out["scope_kind"]), str(out["scope_id"])
    elif out.get("project_id"):
        kind, ident = "project", str(out["project_id"])
    elif out.get("workspace_id"):
        kind, ident = "business_unit", str(out["workspace_id"])

    if kind and not out.get("scope_name"):
        name = await _name_of(session, kind, ident)
        if name:
            out["scope_name"] = name

    if not out.get("actor_name"):
        email = await _actor_email(session, actor_id)
        if email:
            out["actor_name"] = email

    if resource_name and not out.get("resource_name"):
        out["resource_name"] = resource_name

    return out


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
    before: Optional[str] = None,
    after: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Append one RBAC change to the audit trail, in the caller's transaction.

    `subject_id` is who the change is ABOUT; `actor_id` is who made it. They are
    frequently the same person and the difference is the entire point of the record.

    `before` / `after` are PRD §34.9's prior and new state — "the prior state and the
    new one, so a change is legible without guessing". A grant recorded only the role
    it conferred, which answers "what do they hold now" and not "what changed", and
    those are different questions: `developer -> project_admin` is a promotion,
    `none -> project_admin` is an appointment, and a row reading `project_admin` is
    both. They are plain strings rather than a diff because that is what a person
    reads in a table cell; the structured detail is already in the payload.

    Only the CALLER can supply them. By the time this runs the change is applied in
    the same transaction, so anything read here would return the new state twice.
    """
    payload: dict[str, Any] = {
        "subject_id": subject_id,
        "scope_kind": scope_kind,
        "scope_id": str(scope_id),
    }
    if role:
        payload["role"] = role
    # Recorded even when empty-ish on one side: "none -> developer" is the shape of an
    # appointment, and dropping the empty half would make it indistinguishable from a
    # change with no prior state recorded at all.
    if before is not None or after is not None:
        payload["before"] = before
        payload["after"] = after

    # THE NAMES, CAPTURED NOW. See `_name_of`: a record that stores only ids stops
    # being legible the day one of those rows is deleted, and an audit row outlives
    # everything it points at by design.
    scope_name = await _name_of(session, scope_kind, scope_id)
    if scope_name:
        payload["scope_name"] = scope_name
    actor_name = await _actor_email(session, actor_id)
    if actor_name:
        payload["actor_name"] = actor_name
    subject_name = await _actor_email(session, subject_id)
    if subject_name:
        payload["resource_name"] = subject_name

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
            # Same write-time capture as the change writer. A denial names the unit
            # someone was refused on, and that unit can be deleted too.
            scope_name = await _name_of(session, scope_kind or "", scope_id)
            if scope_name:
                payload["scope_name"] = scope_name
                payload["resource_name"] = scope_name
            actor_name = await _actor_email(session, actor_id)
            if actor_name:
                payload["actor_name"] = actor_name

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
