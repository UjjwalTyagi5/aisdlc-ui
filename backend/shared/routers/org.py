"""Organization rollup for the dashboard — GET /org/overview.

One endpoint rather than four counts assembled in the browser. Each figure here
(how many people, providers, connectors the org has) is derivable client-side
only by fanning out over per-scope list endpoints and de-duplicating, which is
exactly how the Users page ends up double-counting anyone in two units. Counted
here, where the whole set is in hand, it is both correct and one round trip.

Every figure is scoped to what the caller may read. A viewer who is not org-wide
gets their own slice rather than a 403, so the same contract backs a Business
Unit Admin's dashboard without a second shape.

Mirrors the frontend Zod contract in lib/schemas/org-overview.ts — field names
are camelCase on purpose so the response validates against it unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.audit import record_rbac_change
from shared.authz.dependency import require_permission
from shared.authz.read_scope import allowed_workspace_ids, is_org_wide
from shared.db import get_db_session
from shared.routers.connectors import _KNOWN_KINDS
from shared.services import org_settings as org_settings_service
from shared.services.org_settings import OrgSettingsError

logger = logging.getLogger(__name__)

org_router = APIRouter(prefix="/org")


class OrgBudgetRow(BaseModel):
    workspaceId: str
    name: str
    # None = no cap set for this unit (inherits org / unlimited). Deliberately not
    # coerced to 0 — "no budget" and "a budget of zero" are opposite states.
    monthlyBudgetUsd: float | None
    monthlySpendUsd: float
    isActive: bool


class OrgOverviewOut(BaseModel):
    userCount: int
    modelProviderCount: int
    connectorCount: int
    connectorTotalCount: int
    businessUnitCount: int
    projectCount: int
    budgets: list[OrgBudgetRow]
    generatedAt: str


# ── settings ─────────────────────────────────────────────────────────────────

class OrgSettingsOut(BaseModel):
    entraTenantId: Optional[str] = None
    entraClientId: Optional[str] = None
    # Whether a client secret is configured — never the reference, never the value.
    hasClientSecret: bool = False
    mfaRequired: bool = False
    sessionTimeoutMinutes: int = 480
    ssoConfigured: bool = False
    updatedBy: Optional[str] = None
    updatedAt: Optional[str] = None


class SsoPatchIn(BaseModel):
    """Every field optional: absent means "leave alone", which is what PATCH means."""

    entraTenantId: Optional[str] = Field(default=None, max_length=64)
    entraClientId: Optional[str] = Field(default=None, max_length=64)
    # Write-only. It goes to the secret store and is never read back by any endpoint.
    entraClientSecret: Optional[str] = Field(default=None, min_length=1, max_length=512)
    mfaRequired: Optional[bool] = None
    sessionTimeoutMinutes: Optional[int] = None


@org_router.get(
    "/settings",
    response_model=OrgSettingsOut,
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def get_org_settings(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> OrgSettingsOut:
    """Readable by any member: MFA policy and session lifetime govern their own login.

    Nothing sensitive is exposed — the SSO client id is not a credential, and the
    secret is reported only as a boolean.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return OrgSettingsOut(**await org_settings_service.get_settings(db, tenant_id))


@org_router.patch(
    "/settings/sso",
    response_model=OrgSettingsOut,
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def patch_org_sso(
    request: Request,
    body: SsoPatchIn,
    db: AsyncSession = Depends(get_db_session),
) -> OrgSettingsOut:
    """Change identity configuration. Organization Admin only.

    Gated on org-wide authority rather than a permission: this decides how everyone in
    the organization authenticates, so a Business Unit Admin holding unit-level
    authority must not reach it. Redirecting the whole org's login to an attacker's
    Entra tenant is the worst single write on the platform.
    """
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail="Only an Organization Admin can change single sign-on settings.",
        )

    try:
        settings = await org_settings_service.update_sso(
            db,
            tenant_id=tenant_id,
            updated_by=getattr(request.state, "user_id", None),
            entra_tenant_id=body.entraTenantId,
            entra_client_id=body.entraClientId,
            entra_client_secret=body.entraClientSecret,
            mfa_required=body.mfaRequired,
            session_timeout_minutes=body.sessionTimeoutMinutes,
        )
    except OrgSettingsError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc))

    # Audited: an SSO change is an identity-system change, and the trail must show who
    # made it. The payload deliberately records WHICH knobs moved, never their values.
    await record_rbac_change(
        db,
        tenant_id=tenant_id,
        actor_id=getattr(request.state, "user_id", None),
        event_type="org.settings.sso.updated",
        subject_id=tenant_id,
        scope_kind="organization",
        scope_id=tenant_id,
        extra={
            "entra_tenant_id_changed": body.entraTenantId is not None,
            "entra_client_id_changed": body.entraClientId is not None,
            "client_secret_rotated": body.entraClientSecret is not None,
            "mfa_required": body.mfaRequired,
            "session_timeout_minutes": body.sessionTimeoutMinutes,
        },
    )
    return OrgSettingsOut(**settings)


@org_router.get(
    "/overview",
    response_model=OrgOverviewOut,
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def org_overview(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> OrgOverviewOut:
    allowed = await allowed_workspace_ids(db, request)

    # Every query below runs under the tenant GUC, so RLS already confines it to
    # this organization; `allowed` narrows it further to the caller's units. An
    # empty list is a real answer (a user with no bindings sees zeroes), which is
    # why it is distinguished from None rather than folded into "no filter".
    scoped = allowed is not None
    params: dict = {"ws": allowed or []}

    def _ws_clause(col: str) -> str:
        if not scoped:
            return ""
        return f" AND {col} = ANY(CAST(:ws AS uuid[]))"

    business_unit_count = (await db.execute(
        text(f"SELECT COUNT(*) FROM workspaces w WHERE true{_ws_clause('w.id')}"), params
    )).scalar() or 0

    project_count = (await db.execute(
        text(
            "SELECT COUNT(*) FROM projects p WHERE p.archived = false"
            + _ws_clause("p.workspace_id")
        ),
        params,
    )).scalar() or 0

    # Distinct people, not memberships — someone bound in two units is one person.
    #
    # A scoped viewer counts only people bound INSIDE their units: at the unit
    # itself, or on a project belonging to it. Organization-scoped bindings are
    # deliberately excluded — "this org has N people" is a fact a Business Unit
    # Admin has no claim on, and including it leaked a headcount to a viewer whose
    # every other figure was correctly zero.
    user_count = (await db.execute(
        text(
            "SELECT COUNT(DISTINCT rb.user_id) FROM role_bindings rb "
            "WHERE rb.status <> 'deactivated'"
            + (
                " AND (rb.scope_id = ANY(CAST(:ws AS uuid[])) "
                "  OR rb.scope_id IN (SELECT p.id FROM projects p "
                "                     WHERE p.workspace_id = ANY(CAST(:ws AS uuid[]))))"
                if scoped else ""
            )
        ),
        params,
    )).scalar() or 0

    # workspace_id NULL = org-shared provider, visible to every unit, so it counts
    # for a unit-scoped viewer too.
    model_provider_count = (await db.execute(
        text(
            "SELECT COUNT(*) FROM model_providers mp WHERE true"
            + (
                " AND (mp.workspace_id IS NULL OR mp.workspace_id = ANY(CAST(:ws AS uuid[])))"
                if scoped else ""
            )
        ),
        params,
    )).scalar() or 0

    connector_count = (await db.execute(
        text(
            "SELECT COUNT(*) FROM workspace_connectors wc WHERE wc.enabled = true"
            + _ws_clause("wc.workspace_id")
        ),
        params,
    )).scalar() or 0

    # Spend joins agent_call_logs -> runs -> projects to reach a workspace.
    # r.id::text = acl.run_id rather than casting run_id to uuid: run_id is a free
    # string column and webhook-triggered runs can put a provider key in it, which
    # would make a uuid cast throw for the whole query.
    budget_rows = (await db.execute(
        text(
            "SELECT w.id, w.display_name, w.monthly_budget_usd, w.status, "
            "  COALESCE(( "
            "    SELECT SUM(acl.cost_usd) FROM agent_call_logs acl "
            "    JOIN runs r ON r.id::text = acl.run_id "
            "    JOIN projects p ON p.id = r.project_id "
            "    WHERE p.workspace_id = w.id "
            "      AND acl.created_at >= date_trunc('month', now()) "
            "  ), 0) AS spend "
            "FROM workspaces w WHERE true" + _ws_clause("w.id") +
            " ORDER BY w.display_name"
        ),
        params,
    )).fetchall()

    return OrgOverviewOut(
        userCount=int(user_count),
        modelProviderCount=int(model_provider_count),
        connectorCount=int(connector_count),
        connectorTotalCount=len(_KNOWN_KINDS),
        businessUnitCount=int(business_unit_count),
        projectCount=int(project_count),
        budgets=[
            OrgBudgetRow(
                workspaceId=str(r[0]),
                name=r[1],
                monthlyBudgetUsd=float(r[2]) if r[2] is not None else None,
                monthlySpendUsd=float(r[4] or 0),
                isActive=(r[3] == "active"),
            )
            for r in budget_rows
        ],
        generatedAt=datetime.now(tz=timezone.utc).isoformat(),
    )
