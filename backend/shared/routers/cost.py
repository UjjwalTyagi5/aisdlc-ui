"""Cost attribution router (REQ-M9-07, REQ-M9-09).

Exposes GET /cost — a per-tenant spend aggregate over agent_call_logs, grouped
by (agent_type, model), plus the computed LLM budget-utilization signal.

Threat mitigations (T-9.2, see milestone-9.2-02-PLAN.md threat_model):
  - T-9.2-05 (Information Disclosure, cross-tenant read): the aggregate query
    filters by AgentCallLog.tenant_id == request.state.tenant_id (defense in
    depth) AND runs under get_db_session, which sets the RLS GUC
    app.current_tenant_id — FORCE RLS on agent_call_logs makes cross-tenant
    rows invisible even if the WHERE clause were dropped.
  - T-9.2-06 (Elevation of Privilege): require_permission("cost:view") gates
    the route (Phase 6 tightening: cost:view restricts to admin/delivery_lead/
    security_auditor per the RBAC matrix — developer/pm/qa/architect lose cost
    access; admin:* wildcards it via has_permission. Previous gate was
    artifact:view, updated in Phase 6 cost reconciliation).
  - T-9.2-07 (Information Disclosure / unbounded label): the
    tenant_llm_budget_utilization gauge carries ONLY a tenant_id label — no
    run_id, no model, no PII (D-M9-02 cardinality discipline).
  - T-9.2-08 (Denial of Service): window_days is bounded 1..365 via Query(ge=,
    le=); the aggregate query relies on the (tenant_id, created_at) index —
    no unbounded full-table scan.

REQ-M9-09 scope note: this module computes the budget-utilization signal and
flags an 80% breach (breached80 in the response + the gauge). Alert DELIVERY
(e.g. Azure Monitor alert rule firing on this gauge) is explicitly deferred to
DLT-9 (settingup) — no notification/alerting call is made here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import (
    DEFAULT_PROJECT_BUDGET_USD,
    LLM_TENANT_BUDGET_OVERRIDES_JSON,
    LLM_TENANT_BUDGET_USD_DEFAULT,
)
from shared.services.budget_alloc import (
    assert_org_covers_workspaces,
    assert_project_fits,
    assert_workspace_covers_projects,
    assert_workspace_fits,
    effective_cap,
)
from shared.authz.dependency import require_permission
from shared.authz.read_scope import allowed_workspace_ids
from shared.db import get_db_session
from shared.routers._schemas import CostBreakdownOut, CostBreakdownRow
from shared.services.budget_store import month_key
from shared.services.metrics import TENANT_LLM_BUDGET_UTILIZATION

cost_router = APIRouter()


def _parse_budget_overrides() -> dict[str, float]:
    """Parse LLM_TENANT_BUDGET_OVERRIDES_JSON once per call.

    Empty/invalid JSON is treated as "no overrides" (default-only) — never
    raises, since a malformed env var must not break GET /cost.
    """
    if not LLM_TENANT_BUDGET_OVERRIDES_JSON:
        return {}
    try:
        raw = json.loads(LLM_TENANT_BUDGET_OVERRIDES_JSON)
        if not isinstance(raw, dict):
            return {}
        return {str(k): float(v) for k, v in raw.items()}
    except (ValueError, TypeError):
        return {}


def resolve_tenant_budget(tenant_id: str) -> float:
    """Resolve the per-tenant LLM budget in USD.

    Config source (documented choice): LLM_TENANT_BUDGET_USD_DEFAULT env (a
    float) with an optional per-tenant override via the
    LLM_TENANT_BUDGET_OVERRIDES JSON env (`{tenant_id: budget_usd}`). A
    per-tenant budget DB table is a future enhancement, NOT required by
    REQ-M9-09.
    """
    overrides = _parse_budget_overrides()
    return overrides.get(str(tenant_id), LLM_TENANT_BUDGET_USD_DEFAULT)


def compute_budget_utilization(tenant_id: str, spend_usd: float, budget_usd: float) -> dict:
    """Compute the budget-utilization ratio, set the gauge, and flag an 80% breach.

    budget_usd <= 0 yields utilization 0 and breached_80 False (no divide-by-zero;
    an unconfigured budget is not treated as "always breached").

    Sets TENANT_LLM_BUDGET_UTILIZATION{tenant_id=...} — labels carry tenant_id
    ONLY (D-M9-02 cardinality discipline; no run_id/model/PII labels).

    REQ-M9-09: returns the computed signal only. Alert DELIVERY on breach is
    DLT-9 (settingup) — do NOT add an alerting/notification call here.
    """
    if budget_usd <= 0:
        utilization = 0.0
        breached_80 = False
    else:
        utilization = spend_usd / budget_usd
        breached_80 = spend_usd >= 0.8 * budget_usd

    TENANT_LLM_BUDGET_UTILIZATION.labels(tenant_id=str(tenant_id)).set(utilization)

    return {
        "utilization": utilization,
        "breached_80": breached_80,
        "budget_usd": budget_usd,
        "spend_usd": spend_usd,
    }


@cost_router.get(
    "",
    response_model=CostBreakdownOut,
    dependencies=[Depends(require_permission("cost:view"))],
)
async def get_cost_breakdown(
    request: Request,
    window_days: int = Query(30, ge=1, le=365),
    workspace: str | None = Query(None, description="Scope to one workspace id (must belong to the tenant)."),
    db: AsyncSession = Depends(get_db_session),
) -> CostBreakdownOut:
    """Return the tenant's (or one workspace's) LLM spend aggregated by model.

    Sourced from Langfuse (the same feed as the /traces page) — the authoritative
    per-model token+cost data, scoped by the mandatory tenant tag (+ the workspace tag
    when `workspace` is given) over the window. This is how the same model configured in
    two workspaces (BYOK, different keys) is separated: pick a workspace to attribute
    per-model spend to it. (agent_call_logs is deprecated; usage_monthly has no model dim.)
    Degrades to empty rows + zero spend when Langfuse is disabled/unreachable.
    """
    tenant_id = request.state.tenant_id

    # WHICH units this caller may total over. `cost:view` is held by bu_admin,
    # project_admin and security_engineer; it says they may see spend, not whose. This
    # endpoint aggregated the entire tenant's, and `spend.py` — the same money, a
    # different route — has narrowed correctly all along. See finding 4 in
    # docs/rbac-audit-2026-08-17.md.
    allowed_ws = await allowed_workspace_ids(db, request)

    # Validate the workspace belongs to the tenant (defense in depth — the Langfuse tenant
    # tag already blocks cross-tenant reads, but reject an unknown/foreign id cleanly).
    _ws_budget = None
    if workspace:
        if allowed_ws is not None and workspace not in allowed_ws:
            # Same 404 as an unknown id: a unit the caller cannot read must not be
            # distinguishable from one that does not exist.
            raise HTTPException(status_code=404, detail="workspace not found in this tenant")
        _row = (await db.execute(
            text("SELECT monthly_budget_usd FROM workspaces WHERE id = :w AND organization_id = :t"),
            {"w": workspace, "t": tenant_id},
        )).first()
        if _row is None:
            raise HTTPException(status_code=404, detail="workspace not found in this tenant")
        _ws_budget = _row[0]

    rows: list[CostBreakdownRow] = []
    total_cost = Decimal("0")
    total_input = 0
    total_output = 0

    from config.env import ENABLE_LANGFUSE  # noqa: PLC0415
    if ENABLE_LANGFUSE:
        from shared.routers.traces import _lf_get  # noqa: PLC0415
        _cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

        # ONE QUERY PER UNIT THE CALLER MAY SEE, summed — rather than one tenant-wide
        # query. Langfuse ANDs its tag filter, so there is no single tag set meaning
        # "these three workspaces"; and post-filtering is not available either, because
        # the daily-metrics endpoint returns figures already aggregated across whatever
        # it matched. Totalling per unit is the only shape that computes the answer FROM
        # the allowed set rather than trimming it afterwards.
        #
        # An org-wide caller keeps the single untagged query — one call, as before.
        if workspace:
            _tag_sets = [[f"tenant:{tenant_id}", f"workspace:{workspace}"]]
        elif allowed_ws is None:
            _tag_sets = [[f"tenant:{tenant_id}"]]
        else:
            # Empty allowed set → no queries at all → zeroes, which is the honest
            # answer for someone with no units rather than the organisation's total.
            _tag_sets = [[f"tenant:{tenant_id}", f"workspace:{w}"] for w in allowed_ws]

        per_model: dict[str, list] = {}  # model -> [in_tok, out_tok, cost, observations]
        for _tags in _tag_sets:
            _data = await _lf_get(
                "/api/public/metrics/daily",
                {"tags": _tags, "fromTimestamp": _cutoff},
            )
            for _day in (_data or {}).get("data") or []:
                for _u in _day.get("usage") or []:
                    _m = _u.get("model")
                    if not _m:  # skip the null-model bucket (non-LLM spans)
                        continue
                    _agg = per_model.setdefault(_m, [0, 0, 0.0, 0])
                    _agg[0] += int(_u.get("inputUsage") or 0)
                    _agg[1] += int(_u.get("outputUsage") or 0)
                    _agg[2] += float(_u.get("totalCost") or 0.0)
                    _agg[3] += int(_u.get("countObservations") or 0)
        for _m, (_i, _o, _c, _n) in per_model.items():
            rows.append(CostBreakdownRow(
                model=_m, inputTokens=_i, outputTokens=_o,
                costUsd=round(_c, 6), callCount=_n,
            ))
            total_input += _i
            total_output += _o
            total_cost += Decimal(str(_c))

    total_cost_usd = float(total_cost)
    # Budget signal scopes to the selected workspace's budget when filtered, else the org
    # budget (DB value authoritative; env default/override is the fallback).
    if workspace:
        budget_usd = effective_cap("workspace", _ws_budget)
    else:
        _org_budget = (await db.execute(
            text("SELECT monthly_budget_usd FROM organizations WHERE id = :t"),
            {"t": tenant_id},
        )).scalar()
        budget_usd = float(_org_budget) if _org_budget is not None else resolve_tenant_budget(tenant_id)
    budget_signal = compute_budget_utilization(tenant_id, total_cost_usd, budget_usd)

    return CostBreakdownOut(
        windowDays=window_days,
        totalCostUsd=total_cost_usd,
        totalInputTokens=total_input,
        totalOutputTokens=total_output,
        rows=rows,
        generatedAt=datetime.now(timezone.utc).isoformat(),
        budgetUsd=budget_signal["budget_usd"],
        utilization=budget_signal["utilization"],
        breached80=budget_signal["breached_80"],
    )


# ── Budget hub (Cost page) ────────────────────────────────────────────────────
# View = cost:view (see the Cost page + spend). Edit = workspace:manage (delivery
# lead + admin). Spend figures are financial data, so both routes sit behind
# cost:view; the mutation additionally requires workspace:manage.

class BudgetRowOut(BaseModel):
    scope: str  # "org" | "workspace" | "project"
    id: str
    name: str
    parentId: str | None = None  # workspace id for a project
    monthlyBudgetUsd: float | None = None
    monthlySpendUsd: float = 0.0
    # Sum of this row's children's budgets (org: Σ workspace caps; workspace: Σ project
    # caps). Lets the UI show "allocated / free". None for projects (leaf).
    allocatedUsd: float | None = None


class BudgetsOut(BaseModel):
    org: BudgetRowOut
    workspaces: list[BudgetRowOut]
    projects: list[BudgetRowOut]
    defaultProjectBudgetUsd: float
    generatedAt: str


class BudgetSetIn(BaseModel):
    scope: str = Field(pattern="^(org|workspace|project)$")
    id: str
    # 0 / null clears the cap (inherit parent / unlimited); positive sets it.
    monthlyBudgetUsd: float | None = Field(default=None, ge=0)


async def _spend_map(db: AsyncSession, tenant_id, scope: str) -> dict[str, float]:
    rows = (await db.execute(
        text("SELECT scope_id, cost_usd FROM usage_monthly "
             "WHERE tenant_id = :t AND scope = :s AND month = :m"),
        {"t": str(tenant_id), "s": scope, "m": month_key()},
    )).all()
    return {str(sid): float(c or 0.0) for sid, c in rows}


@cost_router.get(
    "/budgets",
    response_model=BudgetsOut,
    dependencies=[Depends(require_permission("cost:view"))],
)
async def get_budgets(request: Request, db: AsyncSession = Depends(get_db_session)) -> BudgetsOut:
    """Every workspace + project (with monthly budget + this-month spend) for the tenant.

    Powers the Cost-page budget hub. cost:view gated (spend is financial data)."""
    tenant_id = request.state.tenant_id
    org_spend = await _spend_map(db, tenant_id, "org")
    ws_spend = await _spend_map(db, tenant_id, "workspace")
    proj_spend = await _spend_map(db, tenant_id, "project")

    org_row = (await db.execute(
        text("SELECT display_name, monthly_budget_usd FROM organizations WHERE id = :t"),
        {"t": str(tenant_id)},
    )).first()
    # Every unit and project in the tenant, until the caller's scope narrows it. A
    # Business Unit Admin was reading every sibling unit's budget AND its spend from
    # this one — the sharpest disclosure in the finding, because the figures are
    # financial and the page presents them as a hub.
    allowed_ws = await allowed_workspace_ids(db, request)
    scoped = allowed_ws is not None

    ws_rows = (await db.execute(
        text("SELECT id, display_name, monthly_budget_usd FROM workspaces "
             "WHERE organization_id = :t "
             "  AND (:all OR id = ANY(CAST(:ws AS uuid[]))) "
             "ORDER BY display_name"),
        {"t": str(tenant_id), "all": not scoped, "ws": allowed_ws or []},
    )).all()
    proj_rows = (await db.execute(
        text("SELECT id, display_name, workspace_id, monthly_budget_usd FROM projects "
             "WHERE tenant_id = :t AND archived = false "
             "  AND (:all OR workspace_id = ANY(CAST(:ws AS uuid[]))) "
             "ORDER BY display_name"),
        {"t": str(tenant_id), "all": not scoped, "ws": allowed_ws or []},
    )).all()

    # Effective budgets: explicit value or the per-scope default. Allocation rollups sum
    # the children's EFFECTIVE budgets so the hub's "allocated / free" matches enforcement.
    #
    # For a scoped caller this sums the VISIBLE units only, which is the rule
    # read_scope.py states: a total is computed from the allowed set, not filtered after
    # the fact. An organization-wide "allocated" figure derived from every unit would
    # disclose the size of units the caller cannot open.
    org_allocated = round(sum(effective_cap("workspace", r[2]) for r in ws_rows), 4)
    ws_allocated: dict[str, float] = {}
    for r in proj_rows:
        if r[2]:
            ws_allocated[str(r[2])] = ws_allocated.get(str(r[2]), 0.0) + effective_cap("project", r[3])

    return BudgetsOut(
        org=BudgetRowOut(
            scope="org", id=str(tenant_id),
            name=(org_row[0] if org_row else "Organization"),
            monthlyBudgetUsd=effective_cap("org", org_row[1] if org_row else None),
            # The organisation's own spend for an org-wide caller; the sum of the units
            # they can see otherwise. The org's CAP stays visible either way — it is the
            # ceiling a unit admin allocates under and they cannot read their own
            # headroom without it — but its total SPEND is a fact about every unit,
            # including the ones they cannot open.
            monthlySpendUsd=(
                round(org_spend.get(str(tenant_id), 0.0), 4)
                if not scoped
                else round(sum(ws_spend.get(str(r[0]), 0.0) for r in ws_rows), 4)
            ),
            allocatedUsd=org_allocated,
        ),
        workspaces=[
            BudgetRowOut(
                scope="workspace", id=str(r[0]), name=r[1],
                monthlyBudgetUsd=effective_cap("workspace", r[2]),
                monthlySpendUsd=round(ws_spend.get(str(r[0]), 0.0), 4),
                allocatedUsd=round(ws_allocated.get(str(r[0]), 0.0), 4),
            ) for r in ws_rows
        ],
        projects=[
            BudgetRowOut(
                scope="project", id=str(r[0]), name=r[1], parentId=str(r[2]) if r[2] else None,
                monthlyBudgetUsd=effective_cap("project", r[3]),
                monthlySpendUsd=round(proj_spend.get(str(r[0]), 0.0), 4),
            ) for r in proj_rows
        ],
        defaultProjectBudgetUsd=DEFAULT_PROJECT_BUDGET_USD,
        generatedAt=datetime.now(timezone.utc).isoformat(),
    )


async def _validate_allocation(db: AsyncSession, tenant_id: str, scope: str, id_: str, value: float | None) -> None:
    """Enforce hierarchical budget allocation on a Cost-page budget edit.

    Delegates to shared.services.budget_alloc (same guards the create endpoints use):
    a child can't over-commit its parent, and a parent can't drop below its children.
    """
    if scope == "project":
        ws = (await db.execute(
            text("SELECT workspace_id FROM projects WHERE id = :id AND tenant_id = :t"),
            {"id": id_, "t": tenant_id},
        )).first()
        if ws and ws[0]:
            await assert_project_fits(db, tenant_id, str(ws[0]), value, exclude_id=id_)
    elif scope == "workspace":
        await assert_workspace_fits(db, tenant_id, value, exclude_id=id_)
        await assert_workspace_covers_projects(db, tenant_id, id_, value)
    elif scope == "org":
        await assert_org_covers_workspaces(db, tenant_id, value)


@cost_router.put(
    "/budgets",
    status_code=204,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def set_budget(
    body: BudgetSetIn, request: Request, db: AsyncSession = Depends(get_db_session)
):
    """Set a monthly budget on an org / workspace / project. workspace:manage gated.

    All three scopes are tenant-checked; a positive value sets the cap, 0/null clears it.
    """
    tenant_id = request.state.tenant_id
    value = body.monthlyBudgetUsd or None  # 0 clears
    await _validate_allocation(db, str(tenant_id), body.scope, body.id, value)
    table, id_col, tenant_col = {
        "org": ("organizations", "id", "id"),
        "workspace": ("workspaces", "id", "organization_id"),
        "project": ("projects", "id", "tenant_id"),
    }[body.scope]
    result = await db.execute(
        text(f"UPDATE {table} SET monthly_budget_usd = :v "
             f"WHERE {id_col} = :id AND {tenant_col} = :t"),
        {"v": value, "id": body.id, "t": str(tenant_id)},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"{body.scope} not found in this tenant")
    await db.commit()
    from shared.services.budget_guard import clear_budget_cache  # noqa: PLC0415
    clear_budget_cache()
