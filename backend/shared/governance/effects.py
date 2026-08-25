"""What approving a request actually DOES.

A decision that changes only the request's own status is a note, not a governance
action. When someone approves "raise Payments' cap to $16,000", the cap has to move —
otherwise the queue records agreement and the thing agreed to never happens, which is
the failure mode most likely to go unnoticed: everyone believes it was handled.

APPLIED IN THE SAME TRANSACTION as the status change, deliberately. Two statements that
can half-succeed give you a request marked approved over a budget that never moved, and
no way to tell from either row which of the two is wrong.

NOT EVERY TYPE HAS AN EFFECT, and that is not an omission. For `access_request`,
`connector_access`, `mcp_server`, `user_onboarding`, `agent_access` and `other`, the
DECISION IS THE OUTCOME — the approver then does the thing by hand on the page that owns
it, and the request is the record that they were asked and said yes. Wiring a side
effect onto those would mean this module performing grants it has no business
performing.

Two types have an effect the backend cannot perform yet; each raises
`EffectNotAvailable` rather than silently approving into a void:
  role_assignment       closed by ASSIGNING a role, not by approving; the write lives in
                        `PATCH /workspaces/{id}/members/{userId}`.
  cross_bu_assignment   there is no cross-BU grant table, so a loan cannot be recorded.

`project_creation` used to be a third: `POST /projects` created the project directly,
so approval had nothing to flip. Migration 0028 gave it a pending state
(`Project.approval_status`), so approving now activates it — see
`_apply_project_creation` below. It is also, so far, the only type whose REJECTION
needs a real effect rather than a bare status flip: every other type's rejection
leaves nothing behind to undo, but a rejected project_creation still has to flip its
project row out of `pending_approval` or the project is stuck looking live-pending
forever. See `apply_on_reject`.
"""
from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class EffectNotAvailable(Exception):
    """Approving would have no way to take effect, so the approval is refused.

    Refusing is the conservative answer. Recording an approval whose consequence
    cannot be applied leaves the request looking settled while nothing changed,
    and the person who approved it has no reason to check.
    """

    def __init__(self, request_type: str, detail: str):
        super().__init__(detail)
        self.request_type = request_type
        self.detail = detail


_NOT_APPLICABLE = {
    "role_assignment": (
        "A role-assignment request closes when the role is actually assigned, from "
        "Users or from this queue — not by approving it."
    ),
    "cross_bu_assignment": (
        "Lending a contributor across business units is not implemented: there is "
        "nowhere to record the grant."
    ),
}

# The decision is the whole outcome. Approving records that the approver agreed;
# they then act on the page that owns the thing.
_DECISION_IS_THE_OUTCOME = frozenset(
    {
        "access_request",
        # `connector_access` used to live here — approving it recorded agreement and
        # changed nothing, so the requester still had to go and set the grant by hand
        # and an approved request granted no access at all. It now has a real effect
        # below, which is what makes approval a gate rather than a note.
        "mcp_server",
        "user_onboarding",
        "agent_access",
        "model_credential",
        "other",
    }
)


async def apply_on_approve(db: AsyncSession, request: dict[str, Any]) -> Optional[str]:
    """Perform the consequence of approving `request`. Returns a short audit note.

    Raises EffectNotAvailable when the type's consequence has no implementation,
    so the caller can refuse the decision rather than record a hollow one.
    """
    rtype = request["type"]

    if rtype in _NOT_APPLICABLE:
        raise EffectNotAvailable(rtype, _NOT_APPLICABLE[rtype])

    if rtype in _DECISION_IS_THE_OUTCOME:
        return None

    if rtype == "budget_increase":
        return await _apply_budget_increase(db, request)
    if rtype == "project_archive":
        return await _apply_project_archive(db, request)
    if rtype == "project_creation":
        return await _apply_project_creation(db, request)
    if rtype == "model_provider_access":
        return await _apply_model_provider_access(db, request)
    if rtype == "connector_access":
        return await _apply_connector_access(db, request)
    if rtype.startswith("agent_default_"):
        return await _apply_agent_default(db, request)

    # Unreachable while REQUEST_TYPES and the branches above agree. Refusing beats
    # silently approving a type nobody wired up.
    raise EffectNotAvailable(rtype, f"No approval effect is defined for '{rtype}'.")


async def apply_on_reject(db: AsyncSession, request: dict[str, Any]) -> Optional[str]:
    """The consequence of REJECTING `request`, if any. Returns a short audit note.

    Every other type's rejection is a bare status flip with nothing to undo —
    nothing happened while the request was open, so there is nothing to unwind. Not
    so for `project_creation`: the project row already exists in `pending_approval`
    (created synchronously by POST /projects), so a rejection has to flip it to
    `rejected` explicitly or it is stuck reading as live-pending forever. Unlike
    `apply_on_approve`, never raises — a request can always be refused, even if the
    project it named is somehow already gone (see `_apply_project_creation_reject`).
    """
    if request["type"] == "project_creation":
        return await _apply_project_creation_reject(db, request)
    return None


async def _apply_budget_increase(db: AsyncSession, request: dict[str, Any]) -> str:
    """Move the unit's monthly cap to the amount that was asked for.

    The amount comes from `payload.requestedAmountUsd` — the figure recorded when
    the request was raised, NOT one supplied at decision time. The approver agreed
    to a number they could read; letting the decision carry its own would mean
    approving one figure and applying another.
    """
    payload = request.get("payload") or {}
    amount = payload.get("requestedAmountUsd")
    if amount is None:
        raise EffectNotAvailable(
            "budget_increase", "This request records no requested amount to apply."
        )
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise EffectNotAvailable("budget_increase", "The requested amount is not a number.")
    if amount < 0:
        raise EffectNotAvailable("budget_increase", "The requested amount is negative.")

    result = await db.execute(
        text(
            "UPDATE workspaces SET monthly_budget_usd = :amt, updated_at = now() "
            "WHERE id = CAST(:w AS uuid)"
        ),
        {"amt": amount, "w": request["workspaceId"]},
    )
    if not result.rowcount:
        raise EffectNotAvailable("budget_increase", "That business unit no longer exists.")
    logger.info(
        "governance: budget_increase applied request=%s workspace=%s amount=%s",
        request["id"], request["workspaceId"], amount,
    )
    return f"Monthly cap set to {amount:.2f} USD."


async def _apply_project_archive(db: AsyncSession, request: dict[str, Any]) -> str:
    """Archive the project the request names.

    `target_ref` rather than `project_id`: the two are the same for this type, and
    reading the one the decision was recorded against keeps a request that was
    edited from archiving something else.
    """
    target = request.get("targetRef") or request.get("projectId")
    if not target:
        raise EffectNotAvailable("project_archive", "This request names no project.")
    try:
        _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        raise EffectNotAvailable("project_archive", "This request's project id is malformed.")

    result = await db.execute(
        text(
            "UPDATE projects SET archived = true, updated_at = now() "
            "WHERE id = CAST(:p AS uuid) AND archived = false"
        ),
        {"p": str(target)},
    )
    if not result.rowcount:
        # Already archived is success, not failure — the requester's intent holds
        # either way. Only a missing project is a real problem.
        exists = (
            await db.execute(
                text("SELECT 1 FROM projects WHERE id = CAST(:p AS uuid)"), {"p": str(target)}
            )
        ).first()
        if exists is None:
            raise EffectNotAvailable("project_archive", "That project no longer exists.")
        return "Project was already archived."
    logger.info("governance: project archived request=%s project=%s", request["id"], target)
    return "Project archived."


async def _apply_project_creation(db: AsyncSession, request: dict[str, Any]) -> str:
    """Activate the project the request named, then seat the contributors it deferred.

    Contributors were deliberately NOT granted at creation time — see
    shared/routers/projects.py's create_project — because seating them on a project
    that might still be rejected would hand out working access to something that
    doesn't officially exist yet. `payload.contributors` carries the (user_id, role,
    extra_agents) rows already validated and resolved at creation time — re-resolving
    emails or re-checking grant permission here would be redundant and would let a
    change to someone's email between creation and approval silently swap who gets
    seated; the approver agreed to the people named at raise time, same discipline as
    `_apply_budget_increase` reading its amount from `payload`, never the decision.
    """
    target = request.get("targetRef") or request.get("projectId")
    if not target:
        raise EffectNotAvailable("project_creation", "This request names no project.")
    try:
        _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        raise EffectNotAvailable("project_creation", "This request's project id is malformed.")

    row = (
        await db.execute(
            text(
                "UPDATE projects SET approval_status = 'active', approval_decided_by = :by, "
                "  approval_decided_at = now(), approval_reason = :reason, updated_at = now() "
                "WHERE id = CAST(:p AS uuid) AND approval_status = 'pending_approval' "
                "RETURNING display_name"
            ),
            {
                "p": str(target),
                "by": request.get("decidedBy"),
                "reason": request.get("reason"),
            },
        )
    ).first()
    if row is None:
        # Already active is success, not failure — same reasoning as
        # _apply_project_archive's "already archived". Only a missing project is a
        # real problem.
        existing = (
            await db.execute(
                text("SELECT display_name FROM projects WHERE id = CAST(:p AS uuid)"),
                {"p": str(target)},
            )
        ).first()
        if existing is None:
            raise EffectNotAvailable("project_creation", "That project no longer exists.")
        return "Project was already active."
    display_name = row[0]

    payload = request.get("payload") or {}
    contributors = payload.get("contributors") or []
    seated = 0
    if contributors:
        from shared.authz.grant import grant_role  # noqa: PLC0415 - import cycle
        from shared.services import notifications  # noqa: PLC0415 - import cycle

        for c in contributors:
            user_id = c.get("userId")
            role_name = c.get("roleName")
            if not user_id or not role_name:
                continue
            exists = (
                await db.execute(
                    text("SELECT 1 FROM users WHERE id = :u"), {"u": user_id}
                )
            ).first()
            if exists is None:
                # A since-deleted account must not block the rest of the project
                # from going live — skip and note it in the audit log, not the row.
                logger.warning(
                    "governance: project_creation contributor %s no longer exists, skipped",
                    user_id,
                )
                continue
            await grant_role(
                user_id, str(target), role_name,
                tenant_id=request["tenantId"], scope_kind="project",
                granted_by=request.get("decidedBy"),
            )
            extra_agents = c.get("extraAgents")
            if extra_agents:
                await db.execute(
                    text(
                        "UPDATE role_bindings SET extra_agents = CAST(:a AS jsonb) "
                        "WHERE user_id = :u AND scope_kind = 'project' AND scope_id = :p "
                        "  AND role_name = :r"
                    ),
                    {"a": json.dumps(extra_agents), "u": user_id, "p": str(target), "r": role_name},
                )
            await notifications.emit(
                db,
                tenant_id=request["tenantId"],
                kind="project_activated",
                title=f'"{display_name}" is now live',
                body=f"You were added as {role_name.replace('_', ' ')}.",
                href=f"/projects/{target}",
                recipient_user_id=user_id,
                project_id=str(target),
            )
            seated += 1

    logger.info(
        "governance: project activated request=%s project=%s contributors=%d",
        request["id"], target, seated,
    )
    return f"Project activated.{f' {seated} contributor(s) added.' if seated else ''}"


async def _apply_project_creation_reject(db: AsyncSession, request: dict[str, Any]) -> Optional[str]:
    """Flip the project out of pending on rejection. No contributor loop —
    they were never seated (see `_apply_project_creation`), so there is nothing to
    undo."""
    target = request.get("targetRef") or request.get("projectId")
    if not target:
        return None
    try:
        _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        return None

    result = await db.execute(
        text(
            "UPDATE projects SET approval_status = 'rejected', approval_decided_by = :by, "
            "  approval_decided_at = now(), approval_reason = :reason, updated_at = now() "
            "WHERE id = CAST(:p AS uuid) AND approval_status = 'pending_approval'"
        ),
        {
            "p": str(target),
            "by": request.get("decidedBy"),
            "reason": request.get("reason"),
        },
    )
    if not result.rowcount:
        return None
    logger.info("governance: project creation rejected request=%s project=%s", request["id"], target)
    return "Project marked rejected."


async def _apply_model_provider_access(db: AsyncSession, request: dict[str, Any]) -> str:
    """Activate the model provider the request was raised about.

    The provider row is created when the credential is onboarded and sits inactive
    until an Org Admin agrees to it — this is the agreement.
    """
    target = request.get("targetRef")
    if not target:
        raise EffectNotAvailable("model_provider_access", "This request names no provider.")
    try:
        _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        raise EffectNotAvailable(
            "model_provider_access", "This request's provider id is malformed."
        )

    result = await db.execute(
        text(
            "UPDATE model_providers SET status = 'active', updated_at = now() "
            "WHERE id = CAST(:p AS uuid)"
        ),
        {"p": str(target)},
    )
    if not result.rowcount:
        raise EffectNotAvailable("model_provider_access", "That provider no longer exists.")
    logger.info(
        "governance: model provider activated request=%s provider=%s", request["id"], target
    )
    return "Model provider activated."


async def _apply_agent_default(db: AsyncSession, request: dict[str, Any]) -> str:
    """Publish the proposed agent-profile version.

    `target_ref` is the DRAFT version's id, saved before the proposal was raised.
    Approving publishes exactly that version — which is why the proposal carries an
    id rather than the prompt text: the approver agreed to a specific draft, and
    re-reading the text at decision time would publish whatever it had become.

    Reuses `apply_publish_flip` so this and `POST /agent-profiles/{id}/publish`
    cannot disagree about what "published" means (exactly one active version per
    agent per scope).
    """
    from shared.models.orm import AgentProfile  # noqa: PLC0415 - avoids a cycle at import
    from shared.routers.agent_profiles import apply_publish_flip  # noqa: PLC0415

    target = request.get("targetRef")
    if not target:
        raise EffectNotAvailable(request["type"], "This proposal names no draft version.")
    try:
        target_uuid = _uuid.UUID(str(target))
    except (ValueError, AttributeError):
        raise EffectNotAvailable(request["type"], "This proposal's version id is malformed.")

    row = (
        await db.execute(select(AgentProfile).where(AgentProfile.id == target_uuid))
    ).scalar_one_or_none()
    if row is None:
        raise EffectNotAvailable(request["type"], "That draft version no longer exists.")

    siblings = list(
        (
            await db.execute(
                select(AgentProfile).where(
                    AgentProfile.agent_id == row.agent_id,
                    AgentProfile.scope == row.scope,
                    AgentProfile.scope_id == row.scope_id,
                )
            )
        )
        .scalars()
        .all()
    )
    apply_publish_flip(siblings, row.id)
    await db.flush()

    # Published prompts are cached per agent; a stale cache means the approval
    # takes effect on the next process restart rather than the next run.
    try:
        from shared.services.prompt_runtime import invalidate_profile_cache  # noqa: PLC0415

        invalidate_profile_cache(str(request["tenantId"]), row.agent_id)
    except Exception:  # pragma: no cover - cache is best-effort, the write is not
        logger.warning("governance: profile cache invalidation failed for %s", row.agent_id)

    logger.info(
        "governance: agent default published request=%s profile=%s agent=%s v%s",
        request["id"], row.id, row.agent_id, row.version,
    )
    return f"Published {row.agent_id} v{row.version} at {row.scope} scope."


async def _apply_connector_access(db: AsyncSession, request: dict[str, Any]) -> str:
    """Grant the connector access that was asked for, at the tier that approved it.

    THE APPROVAL IS THE GATE, so it has to be the thing that acts. While this type
    sat in `_DECISION_IS_THE_OUTCOME` an approved request granted nothing — somebody
    still had to go and set it by hand, and until they did, an agent hit a denial
    holding an approved request that said otherwise.

    WHICH TIER MAY APPLY WHAT is the same rule the API enforces, restated here
    because an approval is a second door into the same write:

      org_admin   may grant a BUSINESS UNIT (integration_grants) — the only tier
                  that can, since a unit granting itself has no grant. Reach only:
                  the grant carries no level (migration 0024)
      any tier    may set a PROJECT's default (project_connector_access), bounded
                  by nothing above it, exactly as the direct endpoint now is

    Everything comes from the payload recorded when the request was RAISED, never
    from the decision: the approver agreed to a level they could read, and letting
    the decision carry its own would mean approving one thing and applying another.
    """
    from shared.authz.connector_access import is_access_level, label

    payload = request.get("payload") or {}
    target_ref = (payload.get("targetId") or "").strip()
    kind = (payload.get("kind") or "connector").strip()
    access = (payload.get("access") or "").strip()
    scope = (payload.get("scope") or "").strip() or (
        "project" if request.get("projectId") else "unit"
    )

    if not target_ref:
        raise EffectNotAvailable(
            "connector_access", "This request names no connector to grant."
        )
    if not is_access_level(access):
        raise EffectNotAvailable(
            "connector_access",
            "This request records no valid access level (read, write or read_write).",
        )
    if kind not in ("connector", "mcp"):
        raise EffectNotAvailable("connector_access", f"Unknown integration kind {kind!r}.")

    # THE THIRD DOOR carries the manifest check too. A request raised before a
    # connector's capabilities were known — or approved after they changed — would
    # otherwise write a level the connector cannot exercise, which is the hollow
    # grant this validation exists to prevent, arriving by the one route that skips
    # the endpoint doing the checking.
    if kind == "connector":
        from shared.authz.connector_capabilities import unsupported_reason

        reason = unsupported_reason(target_ref, access)
        if reason:
            raise EffectNotAvailable("connector_access", reason)

    decided_by_tier = request.get("currentApproverRole") or ""
    tenant_id = request["tenantId"]

    # ── a grant to the business unit ─────────────────────────────────────────
    if scope == "unit":
        if decided_by_tier != "org_admin":
            raise EffectNotAvailable(
                "connector_access",
                "Only an Organization Admin can give a business unit an integration. "
                "Escalate this request rather than approving it here.",
            )
        workspace_id = request.get("workspaceId")
        if not workspace_id:
            raise EffectNotAvailable(
                "connector_access", "This request names no business unit."
            )
        # No level on the row since migration 0024 — a grant is reach only, and the
        # read/write choice belongs to the project's stages. The `access` the request
        # asked for is deliberately NOT applied here: honouring it would put a level
        # back on the grant by the approval door after the direct endpoint stopped
        # accepting one.
        await db.execute(
            text(
                "INSERT INTO integration_grants "
                "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
                "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid), :by) "
                "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO UPDATE "
                "  SET granted_by = EXCLUDED.granted_by"
            ),
            {
                "t": tenant_id, "k": kind, "r": target_ref, "w": str(workspace_id),
                "by": request.get("decidedBy"),
            },
        )
        logger.info(
            "connector_access approved: unit %s -> %s %s", workspace_id, kind, target_ref
        )
        return (
            f"{target_ref} granted to the business unit. Each project chooses read "
            "or write per stage."
        )

    # ── a narrowing for one project ──────────────────────────────────────────
    project_id = request.get("projectId")
    if not project_id:
        raise EffectNotAvailable(
            "connector_access", "This request names no project to grant access on."
        )

    workspace_id = (
        await db.execute(
            text("SELECT workspace_id FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": str(project_id)},
        )
    ).scalar()
    if workspace_id is None:
        raise EffectNotAvailable("connector_access", "That project no longer exists.")

    granted = (
        await db.execute(
            text(
                "SELECT 1 FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = :w "
                "  AND kind = :k AND target_ref = :r"
            ),
            {"t": tenant_id, "w": workspace_id, "k": kind, "r": target_ref},
        )
    ).scalar()
    if granted is None:
        raise EffectNotAvailable(
            "connector_access",
            f"This project's business unit has not been given {target_ref}. "
            "An Organization Admin has to grant it to the unit first.",
        )
    # THE CEILING CHECK THAT STOOD HERE IS GONE with migration 0024. It refused an
    # approval that handed a project more than the unit's grant allowed; the grant
    # carries no level to exceed now. Reach is still checked above — approving access
    # to an integration the unit was never given remains refused.

    await db.execute(
        text(
            "INSERT INTO project_connector_access "
            "  (tenant_id, project_id, kind, target_ref, access, granted_by) "
            "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), :k, :r, :a, :by) "
            "ON CONFLICT (tenant_id, project_id, kind, target_ref) DO UPDATE "
            "  SET access = EXCLUDED.access, granted_by = EXCLUDED.granted_by"
        ),
        {
            "t": tenant_id, "p": str(project_id), "k": kind, "r": target_ref,
            "a": access, "by": request.get("decidedBy"),
        },
    )
    logger.info(
        "connector_access approved: project %s -> %s %s (%s)",
        project_id, kind, target_ref, access,
    )
    return f"{target_ref} set to {label(access)} for this project."
