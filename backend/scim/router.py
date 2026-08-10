"""SCIM 2.0 Users endpoint — provision (POST) and deprovision (DELETE).

REQ-M7-16: Idempotent provision on (external_id, tenant_id) via ON CONFLICT upsert.
REQ-M7-17: Deprovision = soft-deactivate (active=false) + JTI denylist revocation.
REQ-M7-23: scim_provision_duration_seconds emitted per operation (metric side only;
            the >15min WARNING alert rule is deferred — repo has no Prometheus
            rule-loading mechanism; see plan <deferred> block).

Security:
  T-7.4-05: Per-tenant bearer credential (verify_scim_credential; fail-closed, D-02).
  T-7.4-07: ON CONFLICT (external_id, tenant_id) DO UPDATE — replay is idempotent no-op.
  T-7.4-08: revoke_all_user_jtis persists the denylist; middleware returns 401 for denied JTIs.
  T-7.4-09: D-05 — no UserWorkspaceRole created; provisioned user has zero permissions.
  T-7.4-10: ENABLE_SCIM startup precondition (enforced in process_api lifespan) raises
             RuntimeError if REDIS_URL is unset — denylist enforcement must not fail-open
             in enterprise mode (fail-closed boot guard, mirrors 7.1/7.3 guards).

URL pattern: path-based tenant routing /scim/v2/{tenant_id}/Users (RESEARCH Pattern 2,
recommendation A) — makes the credential→tenant mapping explicit; IdP configures the
base URL to include tenant_id. No JWT required on these routes (per-tenant bearer IS the auth).
/scim/ prefix is in _PUBLIC_PREFIXES in shared/authz/dependency.py (already there from 7.2).
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import text

from scim.auth import verify_scim_credential
from scim.models import SCIMUserCreate, SCIMUserOut
from shared.auth.denylist import revoke_all_user_jtis
from shared.db import get_db_session_superuser
from shared.services.metrics import SCIM_PROVISION_DURATION

logger = logging.getLogger(__name__)

scim_router = APIRouter(prefix="/scim/v2")


@scim_router.post("/{tenant_id}/Users", status_code=201)
async def provision_user(
    tenant_id: str,
    body: SCIMUserCreate,
    request: Request,
) -> SCIMUserOut:
    """Idempotently provision a user from the IdP.

    ON CONFLICT (external_id, tenant_id) DO UPDATE SET active=true RETURNING ... ensures
    a duplicate externalId from the IdP is a safe no-op (T-7.4-07, SC#3).
    No UserWorkspaceRole is created — deny-by-default (D-05, T-7.4-09).
    """
    if not await verify_scim_credential(request, tenant_id):
        raise HTTPException(status_code=401, detail="SCIM credential invalid")

    with SCIM_PROVISION_DURATION.labels(operation="provision").time():
        user_id = str(_uuid.uuid4())
        async with get_db_session_superuser() as session:
            result = await session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, external_id, active, created_at) "
                    "VALUES (:id, :tenant_id, :external_id, :active, NOW()) "
                    "ON CONFLICT (external_id, tenant_id) "
                    "DO UPDATE SET active = true "
                    "RETURNING id, external_id, active"
                ),
                {
                    "id": user_id,
                    "tenant_id": tenant_id,
                    "external_id": body.externalId,
                    "active": True,
                },
            )
            row = result.fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Provision upsert returned no row")

    logger.info(
        "SCIM provision: tenant=%s external_id=%s user_id=%s",
        tenant_id,
        body.externalId,
        row["id"] if hasattr(row, "__getitem__") else getattr(row, "id", user_id),
    )

    row_id = row["id"] if hasattr(row, "__getitem__") else getattr(row, "id", user_id)
    row_external_id = (
        row["external_id"]
        if hasattr(row, "__getitem__")
        else getattr(row, "external_id", body.externalId)
    )
    row_active = (
        row["active"] if hasattr(row, "__getitem__") else getattr(row, "active", True)
    )

    return SCIMUserOut(
        id=str(row_id),
        userName=body.userName,
        externalId=row_external_id,
        active=bool(row_active),
    )


@scim_router.delete("/{tenant_id}/Users/{scim_user_id}", status_code=204, response_class=Response)
async def deprovision_user(
    tenant_id: str,
    scim_user_id: str,
    request: Request,
) -> Response:
    """Soft-deactivate a user and revoke all live JTIs.

    Sets active=false (keeps the audit trail, makes re-provision reversible per D-01).
    Calls revoke_all_user_jtis so the SCIM deprovision→401 SLA is satisfied (REQ-M7-17).
    The denylist PERSIST call inside revoke_all_user_jtis ensures the set survives
    until the last token would have expired naturally.
    """
    if not await verify_scim_credential(request, tenant_id):
        raise HTTPException(status_code=401, detail="SCIM credential invalid")

    with SCIM_PROVISION_DURATION.labels(operation="deprovision").time():
        async with get_db_session_superuser() as session:
            result = await session.execute(
                text(
                    "UPDATE users SET active = false "
                    "WHERE id = :id AND tenant_id = :tenant_id "
                    "RETURNING id, external_id, id AS sub, active"
                ),
                {"id": scim_user_id, "tenant_id": tenant_id},
            )
            row = result.fetchone()

        if row is not None:
            # Derive the user sub from the 'sub' alias in the RETURNING clause.
            # The SQL returns id AS sub; using the explicit alias makes the intent
            # clear and aligns with the denylist key shape denylist:user:{tid}:{sub}.
            user_sub = (
                row["sub"] if hasattr(row, "__getitem__") else getattr(row, "sub", scim_user_id)
            )
            redis_denylist = getattr(request.app.state, "redis_denylist", None)
            if redis_denylist is not None:
                revoked_count = await revoke_all_user_jtis(redis_denylist, tenant_id, user_sub)
                logger.info(
                    "SCIM deprovision: tenant=%s user_id=%s revoked_jtis=%d",
                    tenant_id,
                    scim_user_id,
                    revoked_count,
                )
            else:
                logger.warning(
                    "SCIM deprovision: redis_denylist unavailable — JTI revocation skipped "
                    "(tenant=%s user_id=%s); enterprise mode requires REDIS_URL (T-7.4-10)",
                    tenant_id,
                    scim_user_id,
                )
        else:
            logger.warning(
                "SCIM deprovision: user not found (tenant=%s user_id=%s)",
                tenant_id,
                scim_user_id,
            )

    return Response(status_code=204)
