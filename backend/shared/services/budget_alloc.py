"""Hierarchical budget allocation guards (org ⊇ workspace ⊇ project).

Single source of truth for the rule: a child's budget can't push the sum of its
siblings past the parent's budget, and a parent can't be dropped below what its
children already commit. Used by the create endpoints (workspaces/projects) and by
the Cost-page budget setter (cost.py).

Effective-cap model: NO DEFAULTS. A scope with no explicit budget has no cap, and
nothing below it is bounded by it. The per-scope DEFAULT_*_BUDGET_USD fallbacks are
gone — they handed every scope a ceiling nobody had chosen, and an organization that
never set a budget could hold exactly two default-sized units before the platform
refused a third.

A cap and its children's commitments are both read from EXPLICIT values only, so the
"allocated" total counts what somebody actually set rather than what a default
imagined.

Raises HTTPException(409) with a user-facing "Budget low" message on violation.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_EPS = 1e-6


def effective_cap(scope: str, explicit) -> float | None:
    """The scope's cap, or None when it has none.

    NO DEFAULTS. This used to fall back to DEFAULT_{ORG,WORKSPACE,PROJECT}_BUDGET_USD,
    handing every scope a ceiling nobody had chosen: an organization that never set a
    budget got $100, and with units defaulting to $50 that was two business units
    before the platform refused a third.

    NULL / 0 now reads as "no cap at this level" everywhere — which is what
    `budget_guard` has always meant by it, and what the frontend documents
    (`lib/budget-allocation.ts`: "null = no cap set, so nothing to exceed"). The
    three now agree.

    `scope` stays in the signature so every call site still names the level it is
    asking about; losing that would make a None answer harder to read back.
    """
    return float(explicit) if explicit is not None and float(explicit) > 0 else None


def _fmt(x: float) -> str:
    return f"${x:,.2f}"


async def _scalar(db: AsyncSession, sql: str, params: dict) -> float:
    return float((await db.execute(text(sql), params)).scalar() or 0.0)


async def _org_cap(db: AsyncSession, tenant_id: str) -> float | None:
    """The org's cap, or None when it has none.

    NOT effective_cap("org", v). An organization that never set a budget has NULL
    here, and falling back to DEFAULT_ORG_BUDGET_USD gave it a $100 ceiling nobody
    chose — with workspaces defaulting to $50, that is two business units before the
    platform refuses a third.

    NULL/0 means "no cap" at this level, which is what the runtime spend guard has
    always read it as (budget_guard._load_scope_budgets) and what the frontend
    documents (frontend/lib/budget-allocation.ts: "null = no cap set, so nothing to
    exceed"). Every level now reads it the same way — see effective_cap.
    """
    v = (await db.execute(
        text("SELECT monthly_budget_usd FROM organizations WHERE id = :t"), {"t": tenant_id},
    )).scalar()
    return float(v) if v is not None and float(v) > 0 else None


async def _workspace_cap(db: AsyncSession, tenant_id: str, ws_id: str) -> float | None:
    """The unit's cap, or None when it has none — same reading as _org_cap."""
    v = (await db.execute(
        text("SELECT monthly_budget_usd FROM workspaces WHERE id = :w AND organization_id = :t"),
        {"w": ws_id, "t": tenant_id},
    )).scalar()
    return effective_cap("workspace", v)


async def _committed_workspaces(db: AsyncSession, tenant_id: str, exclude_id: str | None) -> float:
    # ONLY EXPLICIT BUDGETS COUNT. Coalescing an uncapped unit to a default counted
    # allocation nobody had made — two uncapped units "used up" $100 of an org cap
    # they were never charged against.
    sql = ("SELECT COALESCE(SUM(monthly_budget_usd), 0) FROM workspaces "
           "WHERE organization_id = :t")
    p: dict = {"t": tenant_id}
    if exclude_id:
        sql += " AND id <> :x"
        p["x"] = exclude_id
    return await _scalar(db, sql, p)


async def _committed_projects(db: AsyncSession, tenant_id: str, ws_id: str, exclude_id: str | None) -> float:
    sql = ("SELECT COALESCE(SUM(monthly_budget_usd), 0) FROM projects "
           "WHERE workspace_id = :w AND tenant_id = :t AND archived = false")
    p: dict = {"w": ws_id, "t": tenant_id}
    if exclude_id:
        sql += " AND id <> :x"
        p["x"] = exclude_id
    return await _scalar(db, sql, p)


async def assert_workspace_fits(
    db: AsyncSession, tenant_id: str, new_budget, exclude_id: str | None = None, *, on_create: bool = False,
) -> None:
    """A workspace budget can't push the sum of workspaces past the org budget.

    An org with no budget set caps nothing, so there is nothing to exceed — see
    _org_cap. The check applies only where somebody deliberately set a figure.
    """
    cap = await _org_cap(db, tenant_id)
    if cap is None:
        return
    val = effective_cap("workspace", new_budget)
    if val is None:
        return  # an uncapped unit commits nothing to the org's total
    others = await _committed_workspaces(db, tenant_id, exclude_id)
    if others + val > cap + _EPS:
        free = max(0.0, cap - others)
        what = "add a workspace" if on_create else "set this workspace budget"
        raise HTTPException(
            status_code=409,
            detail=(f"Budget low: the org budget is {_fmt(cap)} with {_fmt(others)} already allocated to "
                    f"workspaces ({_fmt(free)} free), but this needs {_fmt(val)}. Increase the org budget "
                    f"to {what}."),
        )


async def assert_project_fits(
    db: AsyncSession, tenant_id: str, workspace_id: str, new_budget, exclude_id: str | None = None, *,
    on_create: bool = False,
) -> None:
    """A project budget can't push the sum of projects past the workspace budget.

    A unit with no cap of its own bounds nothing, so there is nothing to exceed —
    the same rule assert_workspace_fits applies one level up.
    """
    cap = await _workspace_cap(db, tenant_id, workspace_id)
    if cap is None:
        return
    val = effective_cap("project", new_budget)
    if val is None:
        return  # an uncapped project commits nothing to the unit's total
    others = await _committed_projects(db, tenant_id, workspace_id, exclude_id)
    if others + val > cap + _EPS:
        free = max(0.0, cap - others)
        what = "add a project" if on_create else "set this project budget"
        raise HTTPException(
            status_code=409,
            detail=(f"Budget low: the workspace budget is {_fmt(cap)} with {_fmt(others)} already allocated "
                    f"to projects ({_fmt(free)} free), but this needs {_fmt(val)}. Increase the workspace "
                    f"budget to {what}."),
        )


async def assert_org_covers_workspaces(db: AsyncSession, tenant_id: str, new_org_budget) -> None:
    """The org budget can't be set below what its workspaces already commit.

    CLEARING the budget is not "setting it below" anything — it removes the ceiling
    rather than lowering it, so it is always allowed.
    """
    if new_org_budget is None or float(new_org_budget) <= 0:
        return
    committed = await _committed_workspaces(db, tenant_id, None)
    if float(new_org_budget) < committed - _EPS:
        raise HTTPException(
            status_code=409,
            detail=(f"Workspaces already allocate {_fmt(committed)}; the org budget can't be set below that."),
        )


async def assert_workspace_covers_projects(db: AsyncSession, tenant_id: str, ws_id: str, new_ws_budget) -> None:
    """A workspace budget can't be set below what its projects already commit."""
    committed = await _committed_projects(db, tenant_id, ws_id, None)
    new_cap = effective_cap("workspace", new_ws_budget)
    if new_cap is None:
        return  # clearing a cap removes a ceiling rather than lowering one
    if new_cap < committed - _EPS:
        raise HTTPException(
            status_code=409,
            detail=(f"This workspace already allocates {_fmt(committed)} to its projects; its budget can't "
                    f"be set below that."),
        )
