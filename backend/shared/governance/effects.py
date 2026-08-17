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

Three types have an effect the backend cannot perform yet; each raises
`EffectNotAvailable` rather than silently approving into a void:
  project_creation      there is no pending-project state to activate — `POST /projects`
                        creates directly, so approval has nothing to flip.
  role_assignment       closed by ASSIGNING a role, not by approving; the write lives in
                        `PATCH /workspaces/{id}/members/{userId}`.
  cross_bu_assignment   there is no cross-BU grant table, so a loan cannot be recorded.
"""
from __future__ import annotations

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
    "project_creation": (
        "Approving a project-creation request cannot create the project: there is no "
        "pending-project state for it to activate. Create the project directly instead."
    ),
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
        "connector_access",
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
    if rtype == "model_provider_access":
        return await _apply_model_provider_access(db, request)
    if rtype.startswith("agent_default_"):
        return await _apply_agent_default(db, request)

    # Unreachable while REQUEST_TYPES and the branches above agree. Refusing beats
    # silently approving a type nobody wired up.
    raise EffectNotAvailable(rtype, f"No approval effect is defined for '{rtype}'.")


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
