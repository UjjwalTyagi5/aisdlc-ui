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
import re
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import INVITE_TOKEN_TTL_HOURS
from shared.authz.dependency import require_permission
from shared.authz.grant import UnitAlreadyAdministeredError, grant_role
from shared.authz.grant_guard import assert_can_grant_role
from shared.authz.read_scope import active_binding, is_org_wide
from shared.db import get_db_session, get_db_session_superuser
from shared.services import email_templates, password_setup
from shared.services import governance_requests as governance
from shared.services import notifications
from shared.services.email import send_email

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


def _name_from_email(email: str) -> str:
    """"farah.khan@bank.com" -> "Farah Khan". A guess, and labelled as one.

    Only used when the admin did not type a name. It is a placeholder until the person
    signs in and sets their own, not an attempt to be authoritative about what anybody
    is called.
    """
    local = email.split("@", 1)[0]
    parts = [p for p in re.split(r"[._\-+]+", local) if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) or email


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (name[:2] or "?").upper()


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

    # Belt and braces over the org-wide gate above, and not redundant: `is_org_wide`
    # also passes on `settings:manage`, which no shipped role grants but a custom
    # role or an override could. Someone who reached here that way must still not
    # confer a Business Unit Admin's permissions without holding them.
    await assert_can_grant_role(db, request, body.role)

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
    # The name the dialog shows in its confirmation toast. Taken from what the admin
    # typed when they typed one, derived from the local part otherwise — deriving it
    # here rather than in the client keeps one answer to "what is this person called"
    # for the toast, the roster and the notification body.
    display_name = (body.displayName or "").strip() or _name_from_email(email)
    initials = _initials(display_name)

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
            # password_hash stays NULL, which is the honest representation of "no
            # password has been chosen yet". This used to be a hash of 32 random bytes
            # nobody kept — the effect was identical (`verify_password` refuses either)
            # but it claimed a credential existed. NULL says what is true, and it is what
            # lets the invite link be the one way in.
            await s.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                    "VALUES (:i, :e, NULL, CAST(:t AS uuid), true)"
                ),
                {"i": user_id, "e": email, "t": tenant_id},
            )
            created = True

        # ── the invitation ───────────────────────────────────────────────────
        # Issued only for a NEW account. Re-onboarding somebody who already exists
        # places them somewhere new; it must not mint a fresh set-password link for an
        # account that already has a working password, because that link would be a way
        # to take the account over.
        invite_token = None
        if created:
            invite_token = await password_setup.issue(
                s, user_id=user_id, purpose="invite",
                ttl_hours=INVITE_TOKEN_TTL_HOURS,
            )

    # ── 2. the placement ─────────────────────────────────────────────────────
    if workspace is not None:
        try:
            await grant_role(
                user_id, str(workspace.id), body.role,
                tenant_id=tenant_id, scope_kind="business_unit",
                granted_by=getattr(request.state, "user_id", None),
            )
        except UnitAlreadyAdministeredError as exc:
            # 409, not 422: the request is well-formed and would be valid against a unit
            # that had no admin. The conflict is with the state of the world, and the
            # message names the incumbent so the caller knows who to remove.
            raise HTTPException(
                status_code=409,
                detail={"code": "unit_already_administered", "message": str(exc)},
            )
        except ValueError as exc:
            raise _invalid("invalid_role", str(exc))

    # ── 3. the obligation ────────────────────────────────────────────────────
    # Only a Contributor generates one. A Business Unit Admin was given their job by
    # this very act; a Contributor was given a home and still needs one.
    request_id = None
    notified_bu_admin = False
    if body.role == "contributor" and workspace is not None:
        name = display_name
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

        # WHETHER ANYONE IS ACTUALLY LISTENING. The notification addresses a ROLE, so
        # emitting it into a unit with no admin appointed puts an obligation on nobody
        # and reports success. That case is no longer rare: a unit now starts with no
        # admin — creating one stopped auto-appointing its creator — so the Org Admin
        # must be told "nobody was notified, appoint an admin or assign the role
        # yourself", which is exactly what the dialog says when this is False.
        has_admin = (
            await db.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {active_binding()} "
                    f"  AND rb.scope_kind = 'business_unit' AND rb.scope_id = :w "
                    f"  AND rb.role_name = 'bu_admin' LIMIT 1"
                ),
                {"w": workspace.id, "now": datetime.now(tz=timezone.utc)},
            )
        ).first() is not None

        if has_admin:
            await notifications.emit(
                db,
                tenant_id=tenant_id,
                kind="member_awaiting_role",
                title=f"{name} needs a role",
                body=f"Placed in {workspace.display_name} and waiting on you.",
                href="/users?awaiting=1",
                recipient_role="bu_admin",
                # THIS unit's admins, not every unit's. The person was placed here
                # and it is this unit's admin who owes them a role.
                recipient_scope_kind="business_unit",
                recipient_scope_id=str(workspace.id),
            )
            notified_bu_admin = True
        else:
            logger.warning(
                "onboarding: %s placed in %s, which has no admin — nobody was notified",
                email, workspace.display_name,
            )

    await db.flush()

    # ── 4. the invitation email ──────────────────────────────────────────────
    # Sent AFTER the placement, so the link lands on an account that already has its
    # role: somebody who clicks immediately gets a working session rather than an empty
    # shell. Not transactional with it, deliberately — a mail server being down must not
    # undo an onboarding that is otherwise correct, and the admin can resend.
    #
    # `invited` is reported honestly. When SMTP is unconfigured this is False and the UI
    # says the account was created but no email went, which is the difference between an
    # admin who knows to follow up and one who assumes the person was told.
    invited = False
    if invite_token:
        subject, text_body, html_body = email_templates.invite_email(
            invite_token, INVITE_TOKEN_TTL_HOURS
        )
        invited = await send_email(email, subject, text_body, html_body)
        if not invited:
            logger.warning(
                "onboarding: invite email NOT delivered to %s — account has no password "
                "and no link. Resend once SMTP is configured.", email,
            )

    logger.info(
        "onboarded %s as %s into %s (created=%s, invited=%s)",
        email, body.role, body.workspaceId, created, invited,
    )
    # THE KEYS ARE THE FRONTEND'S, not this router's. `OnboardingResult` in
    # frontend/lib/schemas/onboarding.ts was written against the mock and never matched
    # what this endpoint returned — it wants identityId/displayName/initials/
    # membershipStatus/notifiedBusinessUnitAdmin and got userId/created/roleRequestId, so
    # the dialog failed schema validation on every successful onboarding.
    #
    # Reconciled towards the FRONTEND because its shape is the one with consumers: the
    # dialog renders the name, and branches on notifiedBusinessUnitAdmin to tell an admin
    # whether anyone was actually asked to finish the job. Renaming those away would mean
    # deleting working UX to satisfy a serialiser.
    return {
        "identityId": user_id,
        "email": email,
        "displayName": display_name,
        "initials": initials,
        "workspaceId": str(workspace.id) if workspace is not None else None,
        "role": body.role,
        # Null with no unit: there is no membership to have a status, and saying
        # "invited" would name one that does not exist.
        "membershipStatus": "invited" if workspace is not None else None,
        # False when the unit has no admin to notify — see the check above.
        "notifiedBusinessUnitAdmin": notified_bu_admin,
        # Whether the set-password email actually left the building. False for an account
        # that already existed (no link is issued) and False when SMTP is unconfigured —
        # in which case nobody has been told how to sign in, and the admin needs to know.
        "invited": invited,
        # False when the person already existed and was simply placed — the caller
        # shows "added to Payments" rather than "invited".
        "created": created,
        # Present only for a Contributor: the obligation now sitting with the unit's
        # admin. Null for a BU Admin, who was given their job by this act.
        "roleRequestId": request_id,
    }
