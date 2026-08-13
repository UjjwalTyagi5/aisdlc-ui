"""Deliver a notification, and read the ones addressed to you.

ADDRESSED, NEVER BROADCAST. `emit` requires a recipient — a person, a role, or both —
and refuses a row with neither. The refusal is here as well as in the CHECK constraint
because a caller that forgot the audience should be told at the call site, not by a
constraint violation three frames down.

WHAT A VIEWER SEES is the union of two things: notifications addressed to them
personally, and notifications addressed to the role they are acting as. Both, not
either — an approver who also raises requests needs their own outcomes and their queue
in one list, which is what a bell is for.

BEST-EFFORT BY DESIGN. `emit` is called from inside operations that matter more than it
does: approving a request, onboarding a person. A notification that fails to write must
never roll back the thing it was announcing, so every caller wraps it and this module
logs rather than raises. The inverse — an approval that succeeds silently — is the
lesser failure, because the state it announces is still queryable.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# The bell shows a window, not a history. Older notifications are not deleted — they
# simply stop being offered, because a bell listing five hundred items is a list
# nobody reads.
LIST_LIMIT = 50


async def emit(
    db: AsyncSession,
    *,
    tenant_id: str,
    kind: str,
    title: str,
    body: Optional[str] = None,
    href: Optional[str] = None,
    recipient_user_id: Optional[str] = None,
    recipient_role: Optional[str] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[str]:
    """Deliver one notification. Returns its id, or None when it could not be written.

    Never raises: see the module docstring. A caller inside a transaction that
    matters gets None and carries on.
    """
    if not recipient_user_id and not recipient_role:
        logger.error(
            "notification not emitted — no recipient (kind=%s title=%r)", kind, title
        )
        return None

    notification_id = str(_uuid.uuid4())
    try:
        await db.execute(
            text(
                "INSERT INTO notifications "
                "  (id, tenant_id, kind, title, body, href, project_id, run_id, "
                "   recipient_user_id, recipient_role) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :kind, :title, :body, :href, "
                "        CAST(:pid AS uuid), CAST(:rid AS uuid), :user, :role)"
            ),
            {
                "id": notification_id,
                "t": tenant_id,
                "kind": kind,
                "title": title,
                "body": body,
                "href": href,
                "pid": project_id,
                "rid": run_id,
                "user": recipient_user_id,
                "role": recipient_role,
            },
        )
        return notification_id
    except Exception:  # noqa: BLE001 — announcing must not break the thing announced
        logger.exception(
            "notification emit failed (kind=%s to=%s/%s)", kind, recipient_user_id, recipient_role
        )
        return None


async def list_for(
    db: AsyncSession, *, user_id: str, role: Optional[str], unread_only: bool = False
) -> list[dict[str, Any]]:
    """Everything addressed to this person or to the role they act as, newest first."""
    where = ["(recipient_user_id = :user"]
    params: dict[str, Any] = {"user": user_id}
    if role:
        where[0] += " OR recipient_role = :role"
        params["role"] = role
    where[0] += ")"
    if unread_only:
        where.append("read_at IS NULL")

    rows = (
        await db.execute(
            text(
                "SELECT id, kind, title, body, href, project_id, run_id, read_at, created_at "
                f"FROM notifications WHERE {' AND '.join(where)} "
                f"ORDER BY created_at DESC LIMIT {LIST_LIMIT}"
            ),
            params,
        )
    ).fetchall()

    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "title": r.title,
            # Omitted rather than null: the frontend schema has these optional, and a
            # present-but-null body renders as an empty second line.
            **({"body": r.body} if r.body else {}),
            **({"href": r.href} if r.href else {}),
            **({"projectId": str(r.project_id)} if r.project_id else {}),
            **({"runId": str(r.run_id)} if r.run_id else {}),
            "readAt": r.read_at.isoformat() if r.read_at else None,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def mark_read(db: AsyncSession, *, user_id: str, role: Optional[str]) -> int:
    """Mark everything currently addressed to this viewer as read. Returns the count.

    Scoped to the same set `list_for` returns — you can only mark read what you could
    see, so this cannot be used to clear somebody else's queue.
    """
    now = datetime.now(tz=timezone.utc)
    clause = "recipient_user_id = :user"
    params: dict[str, Any] = {"user": user_id, "now": now}
    if role:
        clause += " OR recipient_role = :role"
        params["role"] = role

    result = await db.execute(
        text(
            f"UPDATE notifications SET read_at = :now WHERE ({clause}) AND read_at IS NULL"
        ),
        params,
    )
    return result.rowcount or 0
