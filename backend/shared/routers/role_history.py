"""Which role somebody held, when it changed, and who changed it.

THE TRAIL ALREADY EXISTED; NOTHING READ IT. `record_rbac_change` has been appending
`rbac.role.granted` / `rbac.role.revoked` to `audit_events` since the RBAC work
landed, keyed on the SUBJECT — the person the change is about — with the actor beside
it. There was no way to ask for one person's history, so the record that answers "who
made me a Project Admin, and when" sat unread.

FROM AND TO ARE DERIVED, NOT STORED, and that is the only real design decision here.
A role change is two events: the old role revoked, the new one granted. Recording a
`from`/`to` pair instead would mean a second, redundant statement of the same facts
that could disagree with them — and could not describe a first grant, which has no
`from`. So the pairing is done on read: a revoke and a grant in the same scope within
a short window are one change, and anything unpaired is a plain grant or a plain
removal. The window exists because the two writes are separate statements in one
request, not because the ordering is uncertain.

WHO MAY READ IT. `member:manage` plus the same scope rule as everything else: an
Organization Admin sees anyone, a Business Unit Admin sees the people in units they
administer. Somebody's role history names every unit they have ever been placed in,
which is more than a project-level admin is owed about a person who merely passes
through their project.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.audit import RBAC_ROLE_GRANTED, RBAC_ROLE_REVOKED
from shared.authz.dependency import require_permission
from shared.authz.read_scope import administered_workspace_ids, is_org_wide
from shared.db import get_db_session

logger = logging.getLogger(__name__)

role_history_router = APIRouter(
    dependencies=[Depends(require_permission("member:manage"))],
)

#: A revoke and a grant this close together, in the same scope, are one change rather
#: than two. They are written as separate statements inside a single request, so the
#: gap is milliseconds; seconds of slack costs nothing and survives a slow transaction.
_PAIR_WINDOW = timedelta(seconds=5)

#: The bell shows a window, not a history; this IS the history, but a person with
#: hundreds of changes is a person nobody scrolls to the end of.
_LIMIT = 200


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


async def _assert_may_read(db: AsyncSession, request: Request, subject_id: str) -> None:
    """Refuse a history for somebody outside the caller's units.

    404 rather than 403: whether a particular person exists in another unit is itself
    something a unit admin should not learn from an error code.
    """
    if is_org_wide(request):
        return

    administered = await administered_workspace_ids(db, request)
    if administered is None:
        return
    if not administered:
        raise HTTPException(status_code=404, detail="not found")

    shares_a_unit = (
        await db.execute(
            text(
                "SELECT 1 FROM role_bindings rb "
                "WHERE rb.user_id = :u "
                "  AND rb.scope_kind = 'business_unit' "
                "  AND rb.scope_id = ANY(CAST(:ids AS uuid[])) LIMIT 1"
            ),
            {"u": subject_id, "ids": administered},
        )
    ).first()
    if shares_a_unit is None:
        raise HTTPException(status_code=404, detail="not found")


@role_history_router.get("/users/{user_id}/role-history")
async def role_history(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """One person's role changes, newest first, with who made each one."""
    tenant_id = _tenant_id(request)
    await _assert_may_read(db, request, user_id)

    rows = (
        await db.execute(
            text(
                "SELECT a.event_type, a.actor_id, a.payload, a.created_at, "
                "       u.email AS actor_email "
                "  FROM audit_events a "
                "  LEFT JOIN users u ON u.id = a.actor_id "
                " WHERE a.tenant_id = CAST(:t AS uuid) "
                "   AND a.resource_type = 'role_binding' "
                "   AND a.resource_id = :u "
                "   AND a.event_type IN (:granted, :revoked) "
                " ORDER BY a.created_at DESC "
                f"LIMIT {_LIMIT}"
            ),
            {"t": tenant_id, "u": user_id,
             "granted": RBAC_ROLE_GRANTED, "revoked": RBAC_ROLE_REVOKED},
        )
    ).fetchall()

    # Unit names, so the history reads in the words the UI uses everywhere else.
    #
    # FILTERED BY TENANT. `workspaces` carries no row-level security, so an
    # unqualified SELECT here loaded every unit in the database — every other
    # organization's included — to build a lookup that only ever needed this
    # tenant's. Nothing leaked into the response because the history rows it is
    # keyed against are themselves scoped, but the query had no business reading
    # them and would have leaked the moment a caller could influence the keys.
    unit_names: dict[str, str] = {
        str(r.id): r.display_name
        for r in (
            await db.execute(
                text(
                    "SELECT id, display_name FROM workspaces "
                    "WHERE organization_id = CAST(:t AS uuid)"
                ),
                {"t": tenant_id},
            )
        ).fetchall()
    }

    events = []
    for r in rows:
        payload = r.payload if isinstance(r.payload, dict) else {}
        scope_id = str(payload.get("scope_id") or "")
        events.append(
            {
                "at": r.created_at.isoformat() if r.created_at else None,
                "granted": r.event_type == RBAC_ROLE_GRANTED,
                "role": payload.get("role"),
                "scopeKind": payload.get("scope_kind"),
                "scopeId": scope_id or None,
                "scopeName": unit_names.get(scope_id),
                "actorId": r.actor_id,
                # NULL actor is a SYSTEM action — seeding, a worker — which is a real
                # answer rather than a missing one, so it is named as such rather than
                # rendered as a blank.
                "actorEmail": r.actor_email,
                "raw": payload,
            }
        )

    return _pair_into_changes(events)


def _pair_into_changes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold revoke+grant pairs into one "X → Y" change.

    Events arrive newest first. A grant paired with a revoke in the same scope, close
    enough in time, is one change; anything left over is a first grant or a removal.
    Pairing on read rather than storing `from`/`to` keeps one set of facts — a stored
    pair could disagree with the events it summarises, and could not describe a first
    grant, which has no `from`.
    """
    from datetime import datetime

    def _ts(e: dict[str, Any]) -> Optional[datetime]:
        return datetime.fromisoformat(e["at"]) if e.get("at") else None

    out: list[dict[str, Any]] = []
    used: set[int] = set()

    for i, event in enumerate(events):
        if i in used or not event["granted"]:
            continue
        partner = None
        for j, other in enumerate(events):
            if j in used or j == i or other["granted"]:
                continue
            if other["scopeId"] != event["scopeId"]:
                continue
            a, b = _ts(event), _ts(other)
            if a and b and abs(a - b) <= _PAIR_WINDOW:
                partner = (j, other)
                break
        if partner:
            used.add(partner[0])
            used.add(i)
            out.append({**event, "from": partner[1]["role"], "to": event["role"],
                        "kind": "changed"})
        else:
            used.add(i)
            out.append({**event, "from": None, "to": event["role"], "kind": "granted"})

    for i, event in enumerate(events):
        if i in used:
            continue
        out.append({**event, "from": event["role"], "to": None, "kind": "removed"})

    out.sort(key=lambda e: e["at"] or "", reverse=True)
    return out
