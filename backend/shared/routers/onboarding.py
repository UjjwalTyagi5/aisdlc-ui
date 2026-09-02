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

from config.env import INVITE_TOKEN_TTL_MINUTES
from shared.authz.dependency import require_permission
from shared.authz.grant import UnitAlreadyAdministeredError, grant_role
from shared.authz.grant_guard import assert_can_grant_role
from shared.authz.read_scope import active_binding, assert_can_write_workspace, is_org_wide
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

# What a BUSINESS UNIT ADMIN may onboard someone as, inside a unit they administer.
# The delivery-tier built-ins minus `contributor` and `custom` — mirrors
# BUSINESS_UNIT_ASSIGNABLE_BUILTIN_ROLES in frontend/hooks/use-assignable-roles.ts.
#
# `bu_admin` is absent and must stay absent: it is an ORG-level appointment
# (ORG_ASSIGNABLE above), and one-admin-per-unit is enforced in grant.py. A unit
# admin who could appoint one could hand their own unit to somebody else.
#
# `contributor` is absent for a different reason. It means "placed, awaiting a role
# from this unit's admin", and raises a role_assignment request addressed to exactly
# that admin — so a unit admin choosing it would be filing a request against
# themselves. They hold the authority the placeholder is waiting for, so they name
# the role now. That is act 3 in _onboard_person, which self-skips for any role
# other than `contributor`.
#
# Custom roles are NOT here. They live in their own table and bind through
# role_bindings.custom_role_id rather than role_name, so grant_role's role_name path
# cannot express one. A unit admin onboards with a built-in role and assigns a custom
# one afterwards from the same page (AssignBusinessUnitRoleDialog), which is the
# existing route for it.
UNIT_ASSIGNABLE = (
    "project_admin",
    "ba",
    "architect",
    "developer",
    "qa",
    "security_engineer",
    "devops_engineer",
    "data_engineer",
    "scrum_master",
)


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

    # TWO CALLERS, TWO SCOPES.
    #
    # Deciding who belongs to the ORGANISATION, and to which unit, is org-wide
    # authority. Staffing a unit you already administer is not the same act and never
    # was: it names somebody, places them where the caller already writes, and gives
    # them a role the caller already grants from the Users page a moment later.
    #
    # This used to be a flat `if not is_org_wide: 403`, which left a Business Unit
    # Admin raising a `user_onboarding` request so an Organization Admin could press a
    # button on their behalf — an approval step over a decision that was entirely
    # theirs, in a unit nobody else administers.
    if is_org_wide(request):
        if body.role not in ORG_ASSIGNABLE:
            raise _invalid(
                "invalid_role",
                "Onboarding assigns Business Unit Admin or Contributor. Every other role "
                "is granted by a business unit's admin.",
            )
    else:
        # A unit is REQUIRED here. Leaving somebody unplaced is an organisation-level
        # state — no unit admin is answerable for them — so it is not this caller's to
        # create, and `workspaceId` being optional on the model is for the branch above.
        if not (body.workspaceId or "").strip():
            raise _invalid(
                "unit_required",
                "Choose the business unit to onboard them into.",
            )

        # THE SCOPE CHECK. `member:manage` says the caller administers a unit, not
        # WHICH one — this is the half that asks. 404 rather than 403 throughout, so a
        # unit the caller does not administer is not confirmed to exist by the error.
        await assert_can_write_workspace(db, request, body.workspaceId)

        if body.role not in UNIT_ASSIGNABLE:
            raise _invalid(
                "invalid_role",
                "Choose the role this person will hold in your business unit. "
                "Business Unit Admin is an organization-level appointment, and "
                "Contributor would file a request back to you.",
            )

    # Belt and braces over the gates above, and not redundant for either branch:
    # `is_org_wide` also passes on `settings:manage`, which no shipped role grants but
    # a custom role or an override could, and the scoped branch has checked WHERE the
    # caller may write without yet checking WHAT they may confer. Nobody grants a role
    # carrying access-authority they do not hold themselves.
    await assert_can_grant_role(db, request, body.role)

    return await _onboard_person(
        db,
        tenant_id=tenant_id,
        email=str(body.email),
        display_name=body.displayName,
        workspace_id=body.workspaceId,
        role=body.role,
        actor_id=getattr(request.state, "user_id", None),
    )


async def _onboard_person(
    db: AsyncSession,
    *,
    tenant_id: str,
    email: str,
    display_name: Optional[str],
    workspace_id: Optional[str],
    role: str,
    actor_id: Optional[str],
) -> dict[str, Any]:
    """The three acts the module docstring's "THREE ACTS, ONE TRANSACTION" describes
    — extracted so a second caller (the `user_onboarding` governance effect,
    approved by an Organization Admin) can perform the exact same admission
    rather than a second, looser copy of it.

    NOT the authority check. `is_org_wide` and `assert_can_grant_role` stay in
    `onboard()` above, because both read the live caller's session
    (`request.state`) and have no meaning for a governance decision, whose
    "caller" is whoever approved the request — that decision's own standing is
    verified by the effect that calls this (`currentApproverRole == "org_admin"`,
    the same standing this route requires via `is_org_wide`), not by re-deriving
    a permission set here. `role not in ORG_ASSIGNABLE` also stays in the route:
    it is what keeps an Organization Admin's own picker to two answers, and the
    effect's only caller (`_apply_user_onboarding`) never passes anything else.

    THREE SEPARATE TRANSACTION BOUNDARIES, preserved exactly as they already were
    in `onboard()` before this extraction — not a new atomicity concern this
    function introduces. Act 1 uses `get_db_session_superuser()` because `users`
    is a global, non-RLS table; act 2's `grant_role()` opens its own independent
    session internally; only act 3 (the `role_assignment` sub-request and its
    notification) runs on the passed-in `db`. A real Organization Admin onboarding
    someone directly already had this shape — a failure between acts already left
    the same partial states it always could — and reaching the same body through
    one more entry point does not change that.
    """
    # A contributor with no unit belongs to nobody: no admin is prompted for their
    # role, so they would sit with no access and nothing to explain why.
    if role == "contributor" and not workspace_id:
        raise _invalid(
            "invalid_input",
            "A Contributor needs a business unit — its admin is who gives them a role.",
        )

    workspace = None
    if workspace_id:
        workspace = (
            await db.execute(
                text(
                    "SELECT id, display_name FROM workspaces "
                    "WHERE id = CAST(:w AS uuid) AND organization_id = CAST(:t AS uuid)"
                ),
                {"w": workspace_id, "t": tenant_id},
            )
        ).first()
        if workspace is None:
            raise HTTPException(status_code=404, detail="not found")

    email = email.lower()
    # The name the dialog shows in its confirmation toast. Taken from what the admin
    # typed when they typed one, derived from the local part otherwise — deriving it
    # here rather than in the client keeps one answer to "what is this person called"
    # for the toast, the roster and the notification body.
    resolved_name = (display_name or "").strip() or _name_from_email(email)
    initials = _initials(resolved_name)

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
                ttl_minutes=INVITE_TOKEN_TTL_MINUTES,
            )

    # ── 2. the placement ─────────────────────────────────────────────────────
    if workspace is not None:
        try:
            await grant_role(
                user_id, str(workspace.id), role,
                tenant_id=tenant_id, scope_kind="business_unit",
                granted_by=actor_id,
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
    if role == "contributor" and workspace is not None:
        name = resolved_name
        try:
            raised = await governance.create_request(
                db,
                tenant_id=tenant_id,
                initiator_id=actor_id or "",
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
            invite_token, INVITE_TOKEN_TTL_MINUTES
        )
        invited = await send_email(email, subject, text_body, html_body)
        if not invited:
            logger.warning(
                "onboarding: invite email NOT delivered to %s — account has no password "
                "and no link. Resend once SMTP is configured.", email,
            )

    logger.info(
        "onboarded %s as %s into %s (created=%s, invited=%s)",
        email, role, workspace_id, created, invited,
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
        "displayName": resolved_name,
        "initials": initials,
        "workspaceId": str(workspace.id) if workspace is not None else None,
        "role": role,
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
