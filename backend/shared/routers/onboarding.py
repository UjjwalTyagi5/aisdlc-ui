"""Admit someone to the organisation — the Organization Admin's half of the handover.

TWO PEOPLE ONBOARD SOMEONE, and this endpoint is only the first of them. The
Organization Admin admits a person and says whether they RUN a business unit or WORK
in one. Two answers, and nothing more — the eleven working roles are not theirs to
guess at. The unit's own admin then says what that person actually does.

THREE ACTS, ONE TRANSACTION:
  1. create the account (idempotent on email — re-onboarding an existing person
     places them rather than failing)
  2. bind them to the unit
  3. raise a `role_assignment` request so the unit's admin knows they owe this
     person a job

The third is the point, not a garnish. Without it somebody lands in a unit and nobody
is told to give them a role — they sit with the `artifact:view` floor, able to sign in
and do nothing, with no record of why. That request is the record, and it closes when a
role is actually assigned rather than by being approved.

THE ROLE VALIDATION HERE IS THE REAL GATE, not the dialog's. A picker offering two
options is a convenience; a request naming `developer` because someone kept an old
client open, or curled it, has to be refused for the same reason the picker does not
offer it.
"""
from __future__ import annotations

import logging
import secrets
import uuid as _uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth.passwords import hash_password
from shared.authz.dependency import require_permission
from shared.authz.grant import grant_role
from shared.authz.read_scope import is_org_wide
from shared.db import get_db_session, get_db_session_superuser
from shared.services import governance_requests as governance
from shared.services import notifications

logger = logging.getLogger(__name__)

onboarding_router = APIRouter(
    dependencies=[Depends(require_permission("artifact:view"))],
)

# The only two answers an Organization Admin gives. Mirrors ORG_ASSIGNABLE_ROLES in
# frontend/lib/roles.ts.
ORG_ASSIGNABLE = ("bu_admin", "contributor")


class OnboardIn(BaseModel):
    email: EmailStr
    displayName: Optional[str] = Field(default=None, max_length=160)
    workspaceId: Optional[str] = None
    role: str = Field(min_length=1, max_length=64)


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _invalid(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


@onboarding_router.post("/onboarding", status_code=201)
async def onboard(
    body: OnboardIn, request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    tenant_id = _tenant_id(request)

    # Admitting someone to the ORGANISATION is org-wide authority, not `member:manage`
    # — a Business Unit Admin assigns roles inside their unit and never decides who
    # belongs to the organisation or to which unit.
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": "Onboarding is an Organization Admin action.",
            },
        )

    if body.role not in ORG_ASSIGNABLE:
        raise _invalid(
            "invalid_role",
            "Onboarding assigns Business Unit Admin or Contributor. Every other role "
            "is granted by a business unit's admin.",
        )

    # A contributor with no unit belongs to nobody: no admin is prompted for their
    # role, so they would sit with no access and nothing to explain why.
    if body.role == "contributor" and not body.workspaceId:
        raise _invalid(
            "invalid_input",
            "A Contributor needs a business unit — its admin is who gives them a role.",
        )

    workspace = None
    if body.workspaceId:
        workspace = (
            await db.execute(
                text(
                    "SELECT id, display_name FROM workspaces "
                    "WHERE id = CAST(:w AS uuid) AND organization_id = CAST(:t AS uuid)"
                ),
                {"w": body.workspaceId, "t": tenant_id},
            )
        ).first()
        if workspace is None:
            raise HTTPException(status_code=404, detail="not found")

    email = str(body.email).lower()

    # ── 1. the account ───────────────────────────────────────────────────────
    # `users` is a global table with no RLS, so it is written on the superuser
    # session; everything after this is tenant-scoped.
    async with get_db_session_superuser() as s:
        existing = (
            await s.execute(text("SELECT id FROM users WHERE lower(email) = :e"), {"e": email})
        ).first()
        if existing is not None:
            user_id = existing.id
            created = False
        else:
            user_id = str(_uuid.uuid4())
            # A password nobody knows, including us. There is no invite-email path
            # yet, so the account exists and cannot be signed into until an admin
            # sets one — which is the honest state. Generating a WEAK placeholder
            # would be an account anybody could guess their way into.
            await s.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                    "VALUES (:i, :e, :p, CAST(:t AS uuid), true)"
                ),
                {
                    "i": user_id, "e": email,
                    "p": hash_password(secrets.token_urlsafe(32)),
                    "t": tenant_id,
                },
            )
            created = True

    # ── 2. the placement ─────────────────────────────────────────────────────
    if workspace is not None:
        try:
            await grant_role(
                user_id, str(workspace.id), body.role,
                tenant_id=tenant_id, scope_kind="business_unit",
                granted_by=getattr(request.state, "user_id", None),
            )
        except ValueError as exc:
            raise _invalid("invalid_role", str(exc))

    # ── 3. the obligation ────────────────────────────────────────────────────
    # Only a Contributor generates one. A Business Unit Admin was given their job by
    # this very act; a Contributor was given a home and still needs one.
    request_id = None
    if body.role == "contributor" and workspace is not None:
        name = email.split("@", 1)[0].replace(".", " ").title()
        try:
            raised = await governance.create_request(
                db,
                tenant_id=tenant_id,
                initiator_id=getattr(request.state, "user_id", "") or "",
                initiator_name="Organization Admin",
                initiator_role="org_admin",
                request_type="role_assignment",
                title=f"Role needed: {name} in {workspace.display_name}",
                description=(
                    f"{name} was placed in {workspace.display_name} and is waiting for a "
                    "role. They can sign in but cannot do anything until they have one."
                ),
                workspace_id=str(workspace.id),
                target_ref=user_id,
                payload={"userId": user_id, "email": email},
                system_raised=True,
            )
            request_id = raised["id"]
        except governance.GovernanceError as exc:
            # The placement already happened and is the thing that matters; a request
            # that could not be raised is logged and surfaced, not rolled back over.
            logger.error("onboarding: role_assignment request not raised: %s", exc)

        await notifications.emit(
            db,
            tenant_id=tenant_id,
            kind="member_awaiting_role",
            title=f"{name} needs a role",
            body=f"Placed in {workspace.display_name} and waiting on you.",
            href="/users?awaiting=1",
            recipient_role="bu_admin",
        )

    await db.flush()
    logger.info(
        "onboarded %s as %s into %s (created=%s)",
        email, body.role, body.workspaceId, created,
    )
    return {
        "userId": user_id,
        "email": email,
        "role": body.role,
        "workspaceId": str(workspace.id) if workspace is not None else None,
        # False when the person already existed and was simply placed — the caller
        # shows "added to Payments" rather than "invited".
        "created": created,
        # Present only for a Contributor: the obligation now sitting with the unit's
        # admin. Null for a BU Admin, who was given their job by this act.
        "roleRequestId": request_id,
    }
