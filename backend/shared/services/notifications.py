"""Deliver a notification, and read the ones addressed to you.

ADDRESSED, NEVER BROADCAST. `emit` requires a recipient — a person, a role, or both —
and refuses a row with neither. The refusal is here as well as in the CHECK constraint
because a caller that forgot the audience should be told at the call site, not by a
constraint violation three frames down.

WHAT A VIEWER SEES is the union of two things: notifications addressed to them
personally, and notifications addressed to a role-and-scope they actually hold. Both,
not either — an approver who also raises requests needs their own outcomes and their
queue in one list, which is what a bell is for.

A ROLE ADDRESS IS NOT COMPLETE WITHOUT A SCOPE. `recipient_role = 'bu_admin'` names a
queue but not which one, and every Business Unit Admin in the tenant matched it, so the
Lending admin read Payments' business. The role says WHO, the scope says WHICH of them,
and only the pair is deliverable — see 0022_notification_scope. The single exception is
a role whose scope is the organization: there is one Organization Admin queue, so it is
addressed without one.

HELD, NOT ACTED AS. A scoped address is matched against the caller's live BINDINGS
rather than against the single role they are acting as, because "the Payments admin"
is a fact about bindings and `effective_platform_role` collapses to the highest one.
That widens delivery slightly as a side effect: somebody who is a Business Unit Admin
in one place and a Developer in another now hears from both queues rather than only
their most senior. That is the correct reading of an addressed bell — the notification
is for the hat, and they are wearing both.

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

from shared.authz.permissions import ROLE_SCOPE
from shared.authz.read_scope import live_binding

logger = logging.getLogger(__name__)

# The bell shows a window, not a history. Older notifications are not deleted — they
# simply stop being offered, because a bell listing five hundred items is a list
# nobody reads.
LIST_LIMIT = 50


def _needs_scope(role: str) -> bool:
    """Is this role held somewhere narrower than the whole organization?

    Read from ROLE_SCOPE rather than listed here, so a role added to the matrix is
    covered without anyone remembering this file. Anything unrecognised — including
    `custom`, whose scope is 'configurable' — is treated as needing one, because the
    failure mode of guessing wrong in that direction is a notification nobody
    receives, and the other direction is a unit's business in somebody else's bell.
    """
    return ROLE_SCOPE.get(role) != "organization"


# ── who a row is addressed to ────────────────────────────────────────────────
#
# ONE PREDICATE, TWO READERS. `list_for` and `mark_read` must agree exactly: marking
# read is documented as bounded to what the listing returns, and the moment the two
# are written separately that stops being true — you get a sweep that clears rows the
# caller cannot see, or leaves ones they can. Written once here, for the same reason
# `live_binding` exists.
#
# The scope match is deliberately not equality alone. A binding overlaps an address
# when it is AT that scope, or at a project INSIDE it (a project admin belongs to the
# queue for their unit), or at the unit CONTAINING it (a unit admin belongs to the
# queue for a project of theirs). What no clause reaches is a sibling unit, which is
# the whole point.
_SCOPE_OVERLAP = (
    "rb.scope_id = notifications.recipient_scope_id "
    "OR EXISTS (SELECT 1 FROM projects p WHERE p.id = rb.scope_id "
    "           AND p.workspace_id = notifications.recipient_scope_id) "
    "OR EXISTS (SELECT 1 FROM projects p2 WHERE p2.id = notifications.recipient_scope_id "
    "           AND p2.workspace_id = rb.scope_id)"
)


def _addressed_to_caller(*, with_role: bool) -> str:
    """SQL for "this row is addressed to the caller". Binds :user, :now, and :role.

    `with_role` drops the unscoped branch when the caller acts as no role at all,
    which is a real state — a brand-new account with no bindings — and binding a
    NULL :role would make `recipient_role = :role` quietly never match anyway.
    """
    unscoped = (
        "OR (notifications.recipient_scope_id IS NULL "
        "    AND notifications.recipient_role = :role) "
        if with_role
        else ""
    )
    return (
        "(notifications.recipient_user_id = :user "
        # The organization's own queue, and the pre-0022 rows that never named a
        # scope. Matched against the role the caller is ACTING as, which is what
        # org-wide standing is: reachable through settings:manage with no binding
        # at all, so a binding join would miss it.
        f"{unscoped}"
        "OR (notifications.recipient_scope_id IS NOT NULL AND EXISTS ("
        "     SELECT 1 FROM role_bindings rb "
        f"    WHERE {live_binding(user_param='user')} "
        "      AND rb.role_name = notifications.recipient_role "
        f"      AND ({_SCOPE_OVERLAP})"
        "))"
        ")"
    )


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
    recipient_scope_kind: Optional[str] = None,
    recipient_scope_id: Optional[str] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[str]:
    """Deliver one notification. Returns its id, or None when it could not be written.

    `recipient_scope_id` is required alongside `recipient_role` for every role held
    below the organization — see the module docstring. It is the unit or project whose
    queue this belongs to, NOT the scope of the thing being announced: a request about
    a project that routes to the Business Unit Admin is addressed to the UNIT, because
    that is where the admin's binding lives.

    Never raises: see the module docstring. A caller inside a transaction that
    matters gets None and carries on.
    """
    if not recipient_user_id and not recipient_role:
        logger.error(
            "notification not emitted — no recipient (kind=%s title=%r)", kind, title
        )
        return None

    # An unscoped role address is undeliverable rather than broadly deliverable. The
    # same judgement as the no-recipient case above and refused the same way: a
    # programming error at the call site, logged loudly, and never at the cost of the
    # operation being announced. Dropping one notification is the smaller failure —
    # the alternative is every unit's admin reading this one.
    if recipient_role and not recipient_scope_id and _needs_scope(recipient_role):
        logger.error(
            "notification not emitted — role address '%s' has no scope "
            "(kind=%s title=%r); pass recipient_scope_id",
            recipient_role, kind, title,
        )
        if not recipient_user_id:
            return None
        # It also named a person, so it is still deliverable to them. Drop only the
        # half that cannot be addressed.
        recipient_role = None

    notification_id = str(_uuid.uuid4())
    try:
        await db.execute(
            text(
                "INSERT INTO notifications "
                "  (id, tenant_id, kind, title, body, href, project_id, run_id, "
                "   recipient_user_id, recipient_role, recipient_scope_kind, "
                "   recipient_scope_id) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :kind, :title, :body, :href, "
                "        CAST(:pid AS uuid), CAST(:rid AS uuid), :user, :role, "
                "        :scope_kind, CAST(:scope_id AS uuid))"
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
                "scope_kind": recipient_scope_kind if recipient_role else None,
                "scope_id": recipient_scope_id if recipient_role else None,
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
    """Everything addressed to this person or to a queue they hold, newest first."""
    params: dict[str, Any] = {"user": user_id, "now": datetime.now(tz=timezone.utc)}
    if role:
        params["role"] = role
    where = [_addressed_to_caller(with_role=bool(role))]
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
    params: dict[str, Any] = {"user": user_id, "now": datetime.now(tz=timezone.utc)}
    if role:
        params["role"] = role

    result = await db.execute(
        text(
            "UPDATE notifications SET read_at = :now "
            f"WHERE {_addressed_to_caller(with_role=bool(role))} AND read_at IS NULL"
        ),
        params,
    )
    return result.rowcount or 0
