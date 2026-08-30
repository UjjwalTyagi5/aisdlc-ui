"""Governance requests — raise, route, decide, escalate, withdraw.

THE RULES LIVE HERE, NOT IN THE CONTROLLER
------------------------------------------
Same reasoning as `approval_requests.py`, and the same stakes. A controller-level
check protects exactly one HTTP route; these operations are reachable from more than
one — the queue, a project page's archive button, an onboarding flow filing a
role_assignment on someone's behalf. Each of those would need its own copy of the
self-approval rule, and the copy that gets forgotten is the hole.

FIVE RULES, AND THE ORDER THEY ARE CHECKED IN
---------------------------------------------
1. SELF-APPROVAL IS BLOCKED. Checked before "already decided", so a second attempt by
   the initiator is still reported as self-approval — the more specific and more
   actionable of the two answers.
2. ONLY THE CURRENT APPROVER DECIDES. `current_approver_role` is computed from routing
   at creation and rewritten on escalation; a caller holding the right permission but
   the wrong role is refused. Permission says what kind of act you may perform; this
   says whether this particular request is yours to answer.
3. A REQUEST CLIMBS, IT DOES NOT DEAD-END — but only to its requester's ceiling. A
   contributor's ask stops with their Project Admin rather than landing in front of an
   Org Admin with no context for it.
4. APPROVAL MUST BE ABLE TO TAKE EFFECT. If the consequence cannot be applied, the
   decision is refused rather than recorded. See `shared/governance/effects.py`.
5. AN AGENT-STUDIO PROMOTION MUST HAVE A PASSING EVALUATION. Scoped to exactly the
   three `agent_default_org`/`agent_default_workspace`/`agent_default_project`
   request types (sub-project 4, spec §3.3, checkpoint 2) — a belt-and-suspenders
   re-check performed in `decide()`'s approve path, immediately before the effect
   is applied, because the draft could have been edited into a new, unevaluated
   version — or never evaluated at all, if the request predates the gate — in the
   time between `propose()`/`propose_skill()` and this decision. Covers both
   Behavior (`AgentProfile`) and Skills (`AgentSkill`) requests, which share these
   three types: the payload's `skillKey` key (present only in Skills' payload) is
   the discriminator used to pick the right `target_type` for
   `latest_passing_evaluation`. Reuses the existing `EffectUnavailable`/422 shape
   rule 4 already raises on a failed effect — no new exception type for this rule.

WHY 400 AND NOT 403 FOR SELF-APPROVAL
-------------------------------------
403 says "you may not do this kind of thing". Self-approval is not that — the caller
may hold the exact role required and would be allowed to decide this same request had
someone else raised it. What is wrong is the combination, which is a bad request.

TIMELINE EVENTS ARE APPEND-ONLY at the grant level (`sdlc_app` has INSERT and SELECT on
`governance_request_events` and nothing else), so nothing here can tidy up a history
after the fact even by accident.
"""
from __future__ import annotations

import json
import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.permissions import ROLE_SCOPE
from shared.authz.read_scope import live_binding
from shared.governance import routing
from shared.governance.effects import EffectNotAvailable, apply_on_approve, apply_on_reject
from shared.services import notifications

logger = logging.getLogger(__name__)


def _queue_scope(
    role: Optional[str], *, workspace_id: Optional[str], project_id: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """Where the queue for `role` lives — the scope a notification is addressed to.

    THE APPROVER'S LEVEL, NOT THE REQUEST'S, and the difference is the whole reason
    this is a function. A request about one project that routes to the Business Unit
    Admin is addressed to the UNIT: that is where their binding is, and addressing it
    to the project would reach nobody at all. Getting this backwards fails silently —
    a notification nobody matches looks exactly like a notification nobody sent.

    Returns (None, None) for the Organization Admin, whose queue is the organization
    and needs no scope.
    """
    scope = ROLE_SCOPE.get(role or "")
    if role is None or scope == "organization":
        return None, None
    if scope == "business_unit":
        return "business_unit", workspace_id
    # Project-scoped, and `custom`, whose level is configurable. Prefer the project
    # when the request names one; otherwise the unit, which still finds them — the
    # overlap rule in `notifications` matches a project binding against its parent.
    if project_id:
        return "project", project_id
    return "business_unit", workspace_id


class GovernanceError(Exception):
    """Base failure, carrying a machine-readable code the client branches on."""

    code = "GOVERNANCE_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status


class RequestNotFound(GovernanceError):
    code = "NOT_FOUND"
    http_status = 404


class SelfApprovalBlocked(GovernanceError):
    code = "SELF_APPROVAL_BLOCKED"
    http_status = 400


class NotYourQueue(GovernanceError):
    code = "NOT_CURRENT_APPROVER"
    http_status = 403


class AlreadyClosed(GovernanceError):
    code = "ALREADY_CLOSED"
    http_status = 409


class CannotEscalate(GovernanceError):
    code = "CANNOT_ESCALATE"
    http_status = 409


class EffectUnavailable(GovernanceError):
    code = "EFFECT_UNAVAILABLE"
    http_status = 422


# "Still open" as a SQL fragment rather than a bound array parameter. The values
# come from `routing.OPEN_STATUSES` — a code constant, never user input — and
# asyncpg will not encode a "{a,b}" string as text[] anyway: it wants a real list,
# and passing one through a CAST just moves the type ambiguity around. Inlining the
# constant is both correct and the clearer read at the call sites.
_OPEN_SQL = "status IN (" + ", ".join(f"'{s}'" for s in routing.OPEN_STATUSES) + ")"

_SELECT_COLUMNS = (
    "r.id, r.tenant_id, r.type, r.status, r.workspace_id, r.project_id, r.title, "
    "r.summary, r.description, r.priority, r.requested_by, r.requested_by_id, "
    "r.requested_by_role, r.current_approver_role, r.approval_stage, "
    "r.escalation_count, r.target_ref, r.payload, r.attachments, r.decided_by, "
    "r.decided_at, r.reason, r.created_at"
)


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


async def _row_to_dict(db: AsyncSession, row: Any) -> dict[str, Any]:
    """One request in the shape `lib/schemas/governance-approval.ts` validates.

    Names are joined rather than denormalised onto the row: a unit renamed after a
    request was raised should show its current name in the queue, because the queue
    is about what to do now. The TIMELINE is the opposite — see `_events`.
    """
    workspace_name = (
        await db.execute(
            text("SELECT display_name FROM workspaces WHERE id = :w"), {"w": row.workspace_id}
        )
    ).scalar()
    project_name = None
    if row.project_id:
        project_name = (
            await db.execute(
                text("SELECT display_name FROM projects WHERE id = :p"), {"p": row.project_id}
            )
        ).scalar()

    return {
        "id": str(row.id),
        "tenantId": str(row.tenant_id),
        "type": row.type,
        "status": row.status,
        "workspaceId": str(row.workspace_id),
        # A unit deleted out from under a request would otherwise fail the
        # frontend's non-nullable `workspaceName`. Falling back to the id keeps
        # the row renderable and visibly odd rather than un-renderable.
        "workspaceName": workspace_name or str(row.workspace_id),
        "projectId": str(row.project_id) if row.project_id else None,
        "projectName": project_name,
        "title": row.title,
        "summary": row.summary,
        "description": row.description,
        "priority": row.priority,
        "requestedBy": row.requested_by,
        "requestedById": row.requested_by_id,
        "requestedByRole": row.requested_by_role,
        "requestedAt": _iso(row.created_at),
        "decidedBy": row.decided_by,
        "decidedAt": _iso(row.decided_at),
        "reason": row.reason,
        "attachments": row.attachments or [],
        "currentApproverRole": row.current_approver_role,
        "approvalStage": row.approval_stage,
        "escalationCount": row.escalation_count,
        "targetRef": row.target_ref,
        "payload": row.payload,
        "timeline": await _events(db, str(row.id)),
    }


async def _events(db: AsyncSession, request_id: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT id, kind, at, actor, actor_role, to_role, note "
                "FROM governance_request_events "
                "WHERE request_id = CAST(:r AS uuid) ORDER BY seq ASC"
            ),
            {"r": request_id},
        )
    ).fetchall()
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "at": _iso(r.at),
            "actor": r.actor,
            "actorRole": r.actor_role,
            "toRole": r.to_role,
            "note": r.note,
        }
        for r in rows
    ]


async def _emit(
    db: AsyncSession,
    *,
    tenant_id: str,
    request_id: str,
    kind: str,
    actor: str,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    to_role: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Append one timeline entry.

    `actor` is a DISPLAY NAME and is stored rather than joined, unlike the unit and
    project names above. The trail has to read correctly years later, after the
    person has been deactivated and their row stops resolving — "approved by
    (unknown)" is a worse record than a name that is now out of date.
    """
    await db.execute(
        text(
            "INSERT INTO governance_request_events "
            "  (id, tenant_id, request_id, kind, actor, actor_id, actor_role, to_role, note) "
            "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:r AS uuid), :k, :actor, "
            "        :actor_id, :actor_role, :to_role, :note)"
        ),
        {
            "id": str(_uuid.uuid4()),
            "t": tenant_id,
            "r": request_id,
            "k": kind,
            "actor": actor,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "to_role": to_role,
            "note": note,
        },
    )


async def _load(db: AsyncSession, request_id: str) -> Any:
    try:
        rid = str(_uuid.UUID(str(request_id)))
    except (ValueError, AttributeError):
        raise RequestNotFound("governance request not found")
    row = (
        await db.execute(
            text(f"SELECT {_SELECT_COLUMNS} FROM governance_requests r WHERE r.id = CAST(:i AS uuid)"),
            {"i": rid},
        )
    ).first()
    if row is None:
        # RLS lands another tenant's request here too, which is the intended
        # answer: not found and not permitted are indistinguishable from outside.
        raise RequestNotFound("governance request not found")
    return row


async def get_request(db: AsyncSession, request_id: str) -> dict[str, Any]:
    return await _row_to_dict(db, await _load(db, request_id))


# ── raising ──────────────────────────────────────────────────────────────────


async def create_request(
    db: AsyncSession,
    *,
    tenant_id: str,
    initiator_id: str,
    initiator_name: str,
    initiator_role: Optional[str],
    request_type: str,
    title: str,
    description: Optional[str],
    workspace_id: str,
    project_id: Optional[str] = None,
    priority: str = "normal",
    attachments: Optional[list[dict[str, Any]]] = None,
    phase: Optional[str] = None,
    target_ref: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    system_raised: bool = False,
    # New: the client-supplied target, for the types that need one. Distinct
    # from `target_ref` above (which system-raised callers pass directly) —
    # this is the client-facing counterpart, folded into target_ref/payload
    # below exactly like `phase` already is.
    target_id: Optional[str] = None,
    access_level: Optional[str] = None,
    provider_model: Optional[dict[str, str]] = None,
    onboard_email: Optional[str] = None,
) -> dict[str, Any]:
    """Raise a request and route it.

    `initiator_role` and the approver are taken from the SESSION, never the body.
    A client that could name its own role would pick the one whose chain is
    shortest, and a client that could name its own approver would not need a chain
    at all — the whole routing model is only worth writing if the request cannot
    choose its own answer.

    `system_raised` is for the types the platform files on someone's behalf rather
    than a person picking them from a menu (role_assignment when onboarding, an
    agent_default_* proposal, an archive request). Those are absent from every
    tier's raisable list by design, so the raisable check must not be applied to
    them — but the caller has to say so explicitly rather than it being inferred,
    or the check becomes bypassable from the outside.
    """
    if not initiator_id:
        raise GovernanceError(
            "initiator is unknown", code="NO_INITIATOR", http_status=401
        )
    if request_type not in routing.REQUEST_TYPES:
        raise GovernanceError(
            f"unknown request type {request_type!r}", code="INVALID_TYPE", http_status=422
        )
    if priority not in routing.PRIORITIES:
        raise GovernanceError(
            f"unknown priority {priority!r}", code="INVALID_PRIORITY", http_status=422
        )

    if system_raised:
        if request_type not in routing.SYSTEM_RAISED:
            raise GovernanceError(
                f"'{request_type}' is not a system-raised type",
                code="NOT_SYSTEM_RAISED",
                http_status=422,
            )
    else:
        # The Organization Admin is the ceiling: a request they raised would have
        # nobody to decide it.
        if not routing.can_raise_request(initiator_role):
            raise GovernanceError(
                "Organization Admins are the final approval authority and cannot raise "
                "requests.",
                code="CANNOT_RAISE",
                http_status=403,
            )
        if not routing.can_raise_type(initiator_role, request_type):
            raise GovernanceError(
                f"A {initiator_role or 'viewer'} cannot raise a "
                f"{routing.REQUEST_TYPE_LABEL.get(request_type, request_type)} request.",
                code="TYPE_NOT_RAISABLE",
                http_status=403,
            )

    approver = routing.initial_approver_role(request_type, initiator_role)
    stage: Optional[str] = None
    if request_type == "agent_access":
        # Stage one is always the Project Admin: theirs is the cheaper question
        # ("should this person be doing this work"), and a no there saves the
        # agent's owner a decision entirely.
        stage = "project_admin"
        approver = routing.agent_access_approver(stage, phase or "")
        payload = {**(payload or {}), "phase": phase} if phase else payload

    # connector_access and mcp_server read payload.targetId (see
    # _apply_connector_access) — merged the same way phase is, never a raw
    # passthrough.
    if target_id and request_type in ("connector_access", "mcp_server"):
        payload = {**(payload or {}), "targetId": target_id}
    # connector_access alone also carries an access level — _apply_
    # connector_access's project-tier branch reads payload["access"].
    #
    # Falls back to default_access_for(target_id) when the caller sends none — the
    # UI that raises this (Integrations page) has no level picker of its own yet and
    # used to hardcode "read", which the effect's capability-manifest check then
    # refused for Slack/MS Teams (write-only connectors): an approved-looking
    # request that could never actually be granted, discovered only at decide time.
    # default_access_for reads the connector's own real capabilities, so a
    # write-only connector gets "write" instead of a level nothing can honour.
    if request_type == "connector_access" and target_id:
        from shared.authz.connector_capabilities import default_access_for  # noqa: PLC0415

        payload = {**(payload or {}), "access": access_level or default_access_for(target_id)}
    # model_credential AND model_provider_access both carry a (provider,
    # model_id) pair — never a model_providers row id. Neither the project
    # Model Management view nor the BU availability card ever has a specific
    # connection's row id in scope (see plan Task 3's corrected design note);
    # model_provider_access's effect resolves the real row server-side, by
    # provider kind, at decide time.
    if provider_model and request_type in ("model_credential", "model_provider_access"):
        payload = {**(payload or {}), "providerModel": provider_model}
    if onboard_email and request_type == "user_onboarding":
        payload = {**(payload or {}), "onboardEmail": onboard_email}

    # A project names its own business unit — trust that over whatever `workspace_id`
    # the client sent, never the other way round. A caller not currently a member of
    # the target project (raising an access_request, or Orchestrator's "not a member"
    # banner) has no reason to know that project's real unit, so its own client-side
    # default — the caller's OWN unit — is exactly what `workspace_id` holds. Without
    # this, the row is stored under the WRONG unit's queue: `list_requests` scopes
    # visibility on `workspace_id` alone (never `project_id`), so the request becomes
    # invisible to the one unit whose Project Admin `decider_covers_scope` will accept,
    # while every Project Admin who CAN see it is refused — undecidable by anyone, the
    # same failure class the model-availability-card fix closed for model_credential.
    #
    # cross_bu_assignment is the deliberate exception: its `workspace_id` NAMES A
    # DIFFERENT UNIT ON PURPOSE — the "lending" unit being asked to loan a contributor
    # to a project that lives in some OTHER unit (see request_cross_bu_member's own
    # "THE WORKSPACE ON THE REQUEST IS THE TARGET'S HOME UNIT, NOT THE CALLER'S", and
    # its `same_unit` guard, which refuses to raise this exact request when the two
    # would coincide). Overriding it here would make that request impossible to raise
    # at all.
    if project_id and request_type != "cross_bu_assignment":
        resolved_workspace = (
            await db.execute(
                text(
                    "SELECT workspace_id FROM projects "
                    "WHERE id = CAST(:p AS uuid) AND tenant_id = CAST(:t AS uuid)"
                ),
                {"p": project_id, "t": tenant_id},
            )
        ).scalar()
        if resolved_workspace is None:
            raise GovernanceError(
                "That project doesn't exist.", code="PROJECT_NOT_FOUND", http_status=404
            )
        workspace_id = str(resolved_workspace)

    if approver is None:
        raise GovernanceError(
            "This request has nobody above it to decide it.",
            code="NO_APPROVER",
            http_status=422,
        )

    request_id = str(_uuid.uuid4())
    label = routing.REQUEST_TYPE_LABEL.get(request_type, request_type)
    summary = f"{label} requested by {initiator_name}."

    await db.execute(
        text(
            "INSERT INTO governance_requests "
            "  (id, tenant_id, type, status, workspace_id, project_id, title, summary, "
            "   description, priority, requested_by, requested_by_id, requested_by_role, "
            "   current_approver_role, approval_stage, escalation_count, target_ref, "
            "   payload, attachments) "
            "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :type, 'submitted', "
            "        CAST(:w AS uuid), CAST(:p AS uuid), :title, :summary, :descr, "
            "        :priority, :by, :by_id, :by_role, :approver, :stage, 0, :target, "
            "        CAST(:payload AS jsonb), CAST(:attachments AS jsonb))"
        ),
        {
            "id": request_id,
            "t": tenant_id,
            "type": request_type,
            "w": workspace_id,
            "p": project_id,
            "title": title,
            "summary": summary,
            "descr": description,
            "priority": priority,
            "by": initiator_name,
            "by_id": initiator_id,
            "by_role": initiator_role,
            "approver": approver,
            "stage": stage,
            # target_ref defaults to the narrowest scope the request is about, which
            # is what every effect that has one reads.
            "target": target_ref or project_id or workspace_id,
            "payload": json.dumps(payload) if payload is not None else None,
            "attachments": json.dumps(attachments or []),
        },
    )

    await _emit(
        db,
        tenant_id=tenant_id,
        request_id=request_id,
        kind="created",
        actor=initiator_name,
        actor_id=initiator_id,
        actor_role=initiator_role,
    )
    await _emit(
        db,
        tenant_id=tenant_id,
        request_id=request_id,
        kind="assigned",
        actor="System",
        to_role=approver,
        note=f"Routed to the {approver.replace('_', ' ')}.",
    )
    # Tell the queue it landed in. Addressed to the ROLE, not a person: whoever
    # holds it now is who should act, including someone appointed after this was
    # raised — a notification addressed to their predecessor would be invisible to
    # them. Scoped, so it is THAT queue rather than every unit's.
    approver_scope_kind, approver_scope_id = _queue_scope(
        approver, workspace_id=workspace_id, project_id=project_id
    )
    await notifications.emit(
        db,
        tenant_id=tenant_id,
        kind="request_approval_required",
        title=title,
        body=summary,
        href="/approvals",
        recipient_role=approver,
        recipient_scope_kind=approver_scope_kind,
        recipient_scope_id=approver_scope_id,
    )
    await db.flush()

    logger.info(
        "governance request raised: id=%s type=%s by=%s(%s) -> %s",
        request_id, request_type, initiator_id, initiator_role, approver,
    )
    return await get_request(db, request_id)


# ── reading ──────────────────────────────────────────────────────────────────


async def list_requests(
    db: AsyncSession,
    *,
    viewer_id: str,
    allowed_workspace_ids: Optional[list[str]],
    workspace_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """The queue, scoped to what this viewer may see.

    TWO THINGS ARE VISIBLE, and the union is deliberate:
      - requests in a unit the viewer may read, and
      - requests the viewer RAISED, wherever they have climbed to.

    Without the second, an initiator loses sight of their own request the moment it
    escalates past the scope they can read — which is exactly when they most want
    to know where it went.

    `allowed_workspace_ids is None` means org-wide, and an EMPTY LIST is a real
    answer (a viewer bound to nothing), so the two must not be conflated. Getting
    that backwards shows a brand-new account the whole organisation's queue.

    `workspace_id` is the CALLER's narrowing choice — the queue's own filter — and
    is intersected with the scope above rather than replacing it.
    """
    where = ["r.tenant_id = current_setting('app.current_tenant_id', true)::uuid"]
    params: dict[str, Any] = {"viewer": viewer_id}

    if allowed_workspace_ids is not None:
        if allowed_workspace_ids:
            binds = []
            for i, ws in enumerate(allowed_workspace_ids):
                params[f"ws{i}"] = ws
                binds.append(f"CAST(:ws{i} AS uuid)")
            where.append(
                f"(r.workspace_id IN ({', '.join(binds)}) OR r.requested_by_id = :viewer)"
            )
        else:
            where.append("r.requested_by_id = :viewer")

    if workspace_id:
        where.append("r.workspace_id = CAST(:ws AS uuid)")
        params["ws"] = workspace_id
    if status:
        where.append("r.status = :status")
        params["status"] = status

    rows = (
        await db.execute(
            text(
                f"SELECT {_SELECT_COLUMNS} FROM governance_requests r "
                f"WHERE {' AND '.join(where)} ORDER BY r.created_at ASC"
            ),
            params,
        )
    ).fetchall()
    return [await _row_to_dict(db, r) for r in rows]


# ── deciding ─────────────────────────────────────────────────────────────────


async def decider_covers_scope(
    db: AsyncSession, *, decider_id: str, role: str, request: dict[str, Any]
) -> bool:
    """Does `decider_id`'s own `role` binding actually cover THIS request's scope?

    `decide()`'s existing check (`decider_role != request["currentApproverRole"]`)
    only confirms the decider holds the right role NAME somewhere in the tenant —
    `effective_platform_role()` collapses every binding a person holds into one
    "highest standing wins" string with no project/workspace information at all.
    This is the second half: does the decider hold THAT role at a binding that
    actually covers the project or business unit this request names, queried
    directly against role_bindings (never derived from the already-collapsed
    platform-role string, which has thrown that information away by the time it
    reaches here).

    Keyed off ROLE_SCOPE (shared/authz/permissions.py) — the natural scope_kind
    each role is normally bound at — rather than a second, hand-rolled mapping.
    org_admin's natural scope is "organization": tenant-wide by design, always
    covers everything, no query needed.

    FAILS CLOSED for anything ROLE_SCOPE does not resolve to organization/
    business_unit/project (defensive only — `currentApproverRole` is always
    org_admin, bu_admin, project_admin, or an AGENT_OWNER_ROLE delivery role in
    practice, never "custom" or "scrum_master"). This also means a role bound at
    a NON-NATURAL scope_kind (e.g. a project_admin granted at business_unit
    scope — `ROLE_SCOPE` documents this as an advisory default, not an
    enforced one, and `grant_role`'s callers can override it) will not match
    the exact-scope branches below and, absent a project in view, falls to the
    same business-unit fallback as everyone else — consistent with
    `read_scope.governs_unit()`'s established rule that a role bound above its
    natural level does not thereby reach what a role bound AT that level does.

    project_admin (and every delivery role — AGENT_OWNER_ROLE names one for
    agent_access stage two) falls back to ANY live binding of `role` within the
    request's own business unit when the request names no specific project at
    all. `create_request()` never REQUIRES `projectId` for any type — it is
    only ever conventionally supplied by the raising UI — so more than one type
    can reach this in practice, not just the one the design was originally
    built for: `user_onboarding` (raisable by a contributor/developer,
    tier-routes to project_admin first, and genuinely carries no projectId by
    design — onboarding is a business-unit-level act) and `agent_access`
    (`create_request` enforces nothing here either, though its own dedicated
    raise dialog always supplies one in practice — see
    `test_a_stage_one_rejection_ends_it` for a project-less case still exercised
    in this suite). Refusing outright here would make such a request
    permanently undecidable by anyone, a real regression this function must
    not cause. The fallback is still strictly narrower than the unscoped check
    it replaces (rules out every OTHER business unit), just not narrowed to
    one project when no single project exists to narrow to.
    """
    scope_kind = ROLE_SCOPE.get(role)
    if scope_kind == "organization":
        return True
    now = datetime.now(tz=timezone.utc)
    if scope_kind == "business_unit":
        workspace_id = request.get("workspaceId")
        if not workspace_id:
            return False
        row = (
            await db.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    "  AND rb.role_name = :role AND rb.scope_kind = 'business_unit' "
                    "  AND rb.scope_id = CAST(:sid AS uuid)"
                ),
                {"u": decider_id, "role": role, "sid": workspace_id, "now": now},
            )
        ).first()
        return row is not None
    if scope_kind == "project":
        project_id = request.get("projectId")
        if project_id:
            row = (
                await db.execute(
                    text(
                        f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                        "  AND rb.role_name = :role AND rb.scope_kind = 'project' "
                        "  AND rb.scope_id = CAST(:sid AS uuid)"
                    ),
                    {"u": decider_id, "role": role, "sid": project_id, "now": now},
                )
            ).first()
            return row is not None
        workspace_id = request.get("workspaceId")
        if not workspace_id:
            return False
        row = (
            await db.execute(
                text(
                    "SELECT 1 FROM role_bindings rb "
                    "  JOIN projects p ON p.id = rb.scope_id AND rb.scope_kind = 'project' "
                    f"WHERE {live_binding()} AND rb.role_name = :role "
                    "  AND p.workspace_id = CAST(:wid AS uuid)"
                ),
                {"u": decider_id, "role": role, "wid": workspace_id, "now": now},
            )
        ).first()
        return row is not None
    return False


async def decide(
    db: AsyncSession,
    *,
    request_id: str,
    decider_id: str,
    decider_name: str,
    decider_role: Optional[str],
    decision: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """Approve or reject. See the four rules in the module docstring."""
    if decision not in ("approve", "reject"):
        raise GovernanceError(
            "decision must be 'approve' or 'reject'", code="INVALID_DECISION", http_status=422
        )
    if not decider_id:
        raise GovernanceError("decider is unknown", code="NO_DECIDER", http_status=401)

    row = await _load(db, request_id)
    request = await _row_to_dict(db, row)

    # 1 ── self-approval, before the already-closed check on purpose.
    if request["requestedById"] == decider_id:
        logger.warning(
            "self-approval blocked: request=%s user=%s", request["id"], decider_id
        )
        raise SelfApprovalBlocked(
            "You raised this request — it escalates rather than self-approving."
        )

    # 2 ── still open?
    if request["status"] not in routing.OPEN_STATUSES:
        raise AlreadyClosed(f"This request was already {request['status']}.")

    # 3 ── is it yours to answer?
    if decider_role != request["currentApproverRole"]:
        raise NotYourQueue(
            "This request is waiting on the "
            f"{(request['currentApproverRole'] or 'nobody').replace('_', ' ')}."
        )
    # 3b ── AND does your OWN binding actually cover it, not just your role name?
    # Same exception, same message: from the caller's point of view this is a
    # stricter form of "not your queue," not a new error class to learn.
    if not await decider_covers_scope(
        db, decider_id=decider_id, role=decider_role, request=request
    ):
        raise NotYourQueue(
            "This request is waiting on the "
            f"{(request['currentApproverRole'] or 'nobody').replace('_', ' ')}."
        )

    now = datetime.now(tz=timezone.utc)

    # ── the two-stage type ───────────────────────────────────────────────────
    # An approval at stage one is not a decision on the request, it is the
    # request MOVING ON. Recorded apart from an escalation deliberately: an
    # escalation means the first approver did not answer, here they did, and the
    # request advancing is the process working.
    if (
        decision == "approve"
        and request["type"] == "agent_access"
        and request["approvalStage"] == "project_admin"
    ):
        phase = (request.get("payload") or {}).get("phase") or ""
        next_stage = routing.next_agent_access_stage("project_admin", phase)
        if next_stage is not None:
            next_approver = routing.agent_access_approver(next_stage, phase)
            result = await db.execute(
                text(
                    "UPDATE governance_requests SET status = 'pending_review', "
                    "  approval_stage = :stage, current_approver_role = :approver, "
                    "  updated_at = now() "
                    f"WHERE id = CAST(:i AS uuid) AND {_OPEN_SQL}"
                ),
                {
                    "stage": next_stage,
                    "approver": next_approver,
                    "i": request["id"],
                },
            )
            if not result.rowcount:
                raise AlreadyClosed("This request was decided by someone else first.")
            await _emit(
                db,
                tenant_id=request["tenantId"],
                request_id=request["id"],
                kind="approved",
                actor=decider_name,
                actor_id=decider_id,
                actor_role=decider_role,
                to_role=next_approver,
                note=reason or "Stage one approved; the agent's owner decides next.",
            )
            stage_two_kind, stage_two_id = _queue_scope(
                next_approver,
                workspace_id=request["workspaceId"],
                project_id=request["projectId"],
            )
            await notifications.emit(
                db,
                tenant_id=request["tenantId"],
                kind="request_approval_required",
                title=request["title"],
                body=f"Approved by the {(decider_role or '').replace('_', ' ')} — now yours to decide.",
                href="/approvals",
                recipient_role=next_approver,
                recipient_scope_kind=stage_two_kind,
                recipient_scope_id=stage_two_id,
            )
            await db.flush()
            return await get_request(db, request["id"])

    # 4 ── an approval that cannot take effect is refused, not recorded.
    #
    # `decidedBy`/`reason` are set on this in-memory dict here, one step ahead of the
    # UPDATE below that actually writes them — the row is still open at this point, so
    # reading either off `request` would give the pre-decision (null) value. Every
    # effect that wants the decider's name or the stated reason (project_creation's
    # approve/reject, connector_access's `granted_by`) reads it from here.
    request["decidedBy"] = decider_name
    request["reason"] = reason
    effect_note: Optional[str] = None
    if decision == "approve" and request["type"] in (
        "agent_default_org", "agent_default_workspace", "agent_default_project",
    ):
        # Belt-and-suspenders re-check (spec §3.3, checkpoint 2): the draft could
        # have been edited into a new, unevaluated version — or never evaluated at
        # all if this request predates the gate — between propose() and the
        # approver's decision. Covers BOTH Behavior (AgentProfile) and Skills
        # (AgentSkill) requests, which share these three request types; the
        # payload's `skillKey` key is the same discriminator `_apply_agent_default`
        # effectively relies on (Behavior's payload has no `skillKey`, Skills' does).
        from shared.services.eval_gate import latest_passing_evaluation  # noqa: PLC0415

        target_type = "skill" if (request.get("payload") or {}).get("skillKey") else "profile"
        passing = await latest_passing_evaluation(
            request["tenantId"], target_type, request["targetRef"]
        )
        if passing is None:
            # "no longer passing" would overclaim: evaluations are append-only and
            # immutable (final whole-branch review, sub-project 4, Minor #2) — this
            # can only mean "never evaluated" or "the draft moved on to a newer,
            # unevaluated version since propose()", never "was scored again and
            # failed."
            raise EffectUnavailable(
                "This proposal's draft was never evaluated, or has since been "
                "superseded by a newer, unevaluated version.",
                code="EFFECT_UNAVAILABLE",
            )

    status = "approved" if decision == "approve" else "rejected"
    # Guarded on status in the UPDATE as well as checked above: two approvers
    # acting at the same instant both pass the read, and only one should win. The
    # row count tells us which.
    #
    # THIS RUNS BEFORE apply_on_approve/apply_on_reject, DELIBERATELY. Most effects
    # write through this same `db` session and so are atomic with this UPDATE
    # regardless of statement order (nothing commits until the outer session's
    # final commit). But some effects — model_credential's `_apply_model_credential`
    # is the first — call into services (`shared/services/model_grants.py`) that open
    # their OWN session and commit independently of this transaction. For those, if
    # the effect ran BEFORE this guarded UPDATE, a request already closed by a
    # concurrent decide() would still let the effect's independent commit through
    # even though this decision's own status flip gets rejected by AlreadyClosed
    # below — an "effect applied, request never marked approved" half-succeed.
    # Running the guard first closes that race: AlreadyClosed fires before any
    # effect — independently-committing or not — ever runs.
    result = await db.execute(
        text(
            "UPDATE governance_requests SET status = :s, decided_by = :by, decided_at = :at, "
            "  reason = :why, current_approver_role = NULL, approval_stage = NULL, "
            "  updated_at = now() "
            f"WHERE id = CAST(:i AS uuid) AND {_OPEN_SQL}"
        ),
        {
            "s": status,
            "by": decider_name,
            "at": now,
            "why": reason,
            "i": request["id"],
        },
    )
    if not result.rowcount:
        raise AlreadyClosed("This request was decided by someone else first.")

    # A failed effect must still leave the request untouched: this UPDATE has run
    # but not committed (one transaction, per get_db_session), so an exception
    # raised out of decide() from here rolls it back along with everything else —
    # see get_db_session's except-rollback. Do not move this UPDATE outside the
    # transaction the effect runs in, and do not commit early.
    if decision == "approve":
        try:
            effect_note = await apply_on_approve(db, request)
        except EffectNotAvailable as exc:
            raise EffectUnavailable(exc.detail, code="EFFECT_UNAVAILABLE")
    else:
        effect_note = await apply_on_reject(db, request)

    await _emit(
        db,
        tenant_id=request["tenantId"],
        request_id=request["id"],
        kind=status,
        actor=decider_name,
        actor_id=decider_id,
        actor_role=decider_role,
        note=" ".join(filter(None, [reason, effect_note])) or None,
    )
    # The outcome goes to the PERSON who raised it, not to a role. Their request
    # followed them wherever it climbed; the answer should too.
    await notifications.emit(
        db,
        tenant_id=request["tenantId"],
        kind=f"request_{status}",
        title=request["title"],
        body=" ".join(filter(None, [f"{status.capitalize()} by {decider_name}.", reason, effect_note])),
        href="/approvals",
        recipient_user_id=request["requestedById"],
    )
    await db.flush()

    logger.info(
        "governance request %s: id=%s by=%s(%s) raised_by=%s effect=%s",
        status, request["id"], decider_id, decider_role, request["requestedById"], effect_note,
    )
    return await get_request(db, request["id"])


# ── closed by side effect, not by decision ──────────────────────────────────


async def complete_role_assignment(
    db: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    role_label: str,
    decided_by_id: Optional[str],
    decided_by_name: str,
) -> None:
    """Close any open `role_assignment` request this role change discharges.

    A role assignment is not approved, it is DONE — see onboarding.py, which
    raises the request in the first place: "it closes when a role is actually
    assigned rather than by being approved." `decide()` cannot do this: a
    role_assignment request names no role of its own (that is the entire ask),
    so `apply_on_approve` has no case for it, and the frontend's Assign-role
    dialog reaches this role change through `update_workspace_member_role`, not
    through `decide()`, precisely because approving without a chosen role would
    close the request with nobody having said which one.

    NO-OP when no request is open, by design — the docstring on the frontend's
    completeRoleAssignment mock states the same contract: a role changed for any
    OTHER reason (a correction, a later re-assignment) must not invent a request
    to close. NEVER raises: this is a side effect of a role change that already
    committed, not a precondition of it — a governance-table hiccup must not
    undo (or appear to undo) a grant that already took effect.
    """
    try:
        row = (
            await db.execute(
                text(
                    "SELECT id FROM governance_requests "
                    f"WHERE type = 'role_assignment' AND workspace_id = CAST(:w AS uuid) "
                    f"  AND target_ref = :u AND {_OPEN_SQL} "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"w": workspace_id, "u": user_id},
            )
        ).first()
        if row is None:
            return
        request_id = str(row.id)
        now = datetime.now(tz=timezone.utc)
        note = f"Assigned {role_label}."

        result = await db.execute(
            text(
                "UPDATE governance_requests SET status = 'approved', decided_by = :by, "
                "  decided_at = :at, reason = :why, current_approver_role = NULL, "
                "  approval_stage = NULL, updated_at = now() "
                f"WHERE id = :i AND {_OPEN_SQL}"
            ),
            {"by": decided_by_name, "at": now, "why": note, "i": request_id},
        )
        if not result.rowcount:
            return  # closed by someone else between the SELECT and here — fine.

        await _emit(
            db,
            tenant_id=tenant_id,
            request_id=request_id,
            kind="approved",
            actor=decided_by_name,
            actor_id=decided_by_id,
            actor_role=None,
            note=note,
        )
        await db.flush()
        logger.info(
            "governance request auto-closed by role assignment: id=%s workspace=%s user=%s role=%s",
            request_id, workspace_id, user_id, role_label,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort side effect, see docstring
        logger.warning(
            "complete_role_assignment: could not close request for workspace=%s user=%s: %s",
            workspace_id, user_id, type(exc).__name__,
        )


# ── climbing ─────────────────────────────────────────────────────────────────


async def escalate(
    db: AsyncSession,
    *,
    request_id: str,
    actor_id: str,
    actor_name: str,
    actor_role: Optional[str],
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Send a request up a tier when its approver has not answered.

    Open to the current approver AND to the initiator: the person waiting is the
    one who knows it has stalled, and a request that can only be escalated by the
    approver who is ignoring it will never move.
    """
    row = await _load(db, request_id)
    request = await _row_to_dict(db, row)

    if request["status"] not in routing.OPEN_STATUSES:
        raise AlreadyClosed(f"This request was already {request['status']}.")

    is_initiator = request["requestedById"] == actor_id
    if not is_initiator and actor_role != request["currentApproverRole"]:
        raise NotYourQueue("This request is not waiting on you.")

    current = request["currentApproverRole"]
    if not routing.can_escalate(current, request["requestedByRole"]):
        raise CannotEscalate(
            f"The {(current or 'current approver').replace('_', ' ')} is the highest tier "
            "this request can reach. It is decided there or not at all."
        )

    nxt = routing.next_approver_role(current)
    result = await db.execute(
        text(
            "UPDATE governance_requests SET status = 'escalated', "
            "  current_approver_role = :approver, escalation_count = escalation_count + 1, "
            "  updated_at = now() "
            f"WHERE id = CAST(:i AS uuid) AND {_OPEN_SQL}"
        ),
        {
            "approver": nxt,
            "i": request["id"],
        },
    )
    if not result.rowcount:
        raise AlreadyClosed("This request was closed by someone else first.")

    await _emit(
        db,
        tenant_id=request["tenantId"],
        request_id=request["id"],
        kind="escalated",
        actor=actor_name,
        actor_id=actor_id,
        actor_role=actor_role,
        to_role=nxt,
        note=note,
    )
    nxt_scope_kind, nxt_scope_id = _queue_scope(
        nxt, workspace_id=request["workspaceId"], project_id=request["projectId"]
    )
    await notifications.emit(
        db, tenant_id=request["tenantId"], kind="request_escalated",
        title=request["title"], body="Escalated to your queue.", href="/approvals",
        recipient_role=nxt, recipient_scope_kind=nxt_scope_kind,
        recipient_scope_id=nxt_scope_id,
    )
    # And the initiator, who is the person actually waiting.
    await notifications.emit(
        db, tenant_id=request["tenantId"], kind="request_escalated",
        title=request["title"],
        body=f"Escalated to the {(nxt or '').replace('_', ' ')}.",
        href="/approvals", recipient_user_id=request["requestedById"],
    )
    await db.flush()
    logger.info(
        "governance request escalated: id=%s %s -> %s by=%s", request["id"], current, nxt, actor_id
    )
    return await get_request(db, request["id"])


async def cancel(
    db: AsyncSession,
    *,
    request_id: str,
    actor_id: str,
    actor_name: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """Withdraw a request you raised.

    The INITIATOR only. An approver who wants it gone rejects it, which records a
    decision; letting them cancel would let an approver make a request disappear
    without ever answering it.
    """
    row = await _load(db, request_id)
    request = await _row_to_dict(db, row)

    if request["requestedById"] != actor_id:
        raise NotYourQueue(
            "Only the person who raised a request can withdraw it. Reject it instead."
        )
    if request["status"] not in routing.OPEN_STATUSES:
        raise AlreadyClosed(f"This request was already {request['status']}.")

    result = await db.execute(
        text(
            "UPDATE governance_requests SET status = 'cancelled', "
            "  current_approver_role = NULL, approval_stage = NULL, reason = :why, "
            "  updated_at = now() "
            f"WHERE id = CAST(:i AS uuid) AND {_OPEN_SQL}"
        ),
        {
            "why": reason,
            "i": request["id"],
        },
    )
    if not result.rowcount:
        raise AlreadyClosed("This request was closed by someone else first.")

    await _emit(
        db,
        tenant_id=request["tenantId"],
        request_id=request["id"],
        kind="cancelled",
        actor=actor_name,
        actor_id=actor_id,
        note=reason,
    )
    await db.flush()
    logger.info("governance request cancelled: id=%s by=%s", request["id"], actor_id)
    return await get_request(db, request["id"])
