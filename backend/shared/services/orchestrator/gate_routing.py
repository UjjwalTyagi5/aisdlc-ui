"""Gate routing — stage-to-owner-role map, approval check, and notify seam.

GATE_OWNER maps each pipeline stage to the role responsible for approving that gate.
can_user_approve reuses the canonical RBAC primitives (_PHASE_PERMISSION + has_permission)
so approval logic is never duplicated.
notify_gate_pending is a best-effort audit seam; it never raises into the gate flow.
"""
from __future__ import annotations

import logging

from shared.authz.permissions import _PHASE_PERMISSION, has_permission

logger = logging.getLogger(__name__)

GATE_OWNER: dict[str, str] = {
    "requirements": "product_manager",
    "design": "tech_lead",
    "development": "tech_lead",
    "code_review": "tech_lead",
    "security": "delivery_lead",
    "testing": "qa_lead",
    "deployment": "sre_lead",
    "documentation": "auto",
}


def gate_owner_role(stage: str) -> str:
    """Return the owning role for the given pipeline stage gate."""
    return GATE_OWNER.get(stage, "product_manager")


def stage_approve_permission(stage: str) -> str | None:
    """Return the permission string required to approve *stage*, or None if unmapped."""
    return _PHASE_PERMISSION.get(stage)


def can_user_approve(perms: list[str], stage: str) -> bool:
    """Return True if perms grant approval rights for the given stage.

    Delegates entirely to _PHASE_PERMISSION (stage→required permission) and
    has_permission (which handles admin:* wildcard), so enforcement is consistent
    with every other RBAC check in the codebase.
    """
    required = _PHASE_PERMISSION.get(stage)
    if not required:
        return False
    return has_permission(perms, required)


async def notify_gate_pending(
    run_id: str, stage: str, owner_role: str, tenant_id: str
) -> None:
    """Emit a best-effort audit event when a gate is awaiting approval.

    Uses AuditEventPayload + audit_service.emit (fire-and-forget).  Any failure
    is logged and swallowed — this seam must never break the gate flow.
    """
    try:
        from shared.audit.models import AuditEventPayload
        from shared.audit.service import audit_service

        payload = AuditEventPayload(
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="gate.notified",
            resource_type="gate",
            resource_id=run_id,
            payload={"stage": stage, "owner_role": owner_role},
        )
        await audit_service.emit(payload)
    except Exception as exc:  # best-effort: notification must never raise into caller
        logger.warning("notify_gate_pending failed (run=%s stage=%s): %s", run_id, stage, exc)
