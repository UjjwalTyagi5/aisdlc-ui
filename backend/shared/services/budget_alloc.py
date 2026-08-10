"""Hierarchical budget allocation guards (org ⊇ workspace ⊇ project).

Single source of truth for the rule: a child's budget can't push the sum of its
siblings past the parent's budget, and a parent can't be dropped below what its
children already commit. Used by the create endpoints (workspaces/projects) and by
the Cost-page budget setter (cost.py).

Effective-cap model: a scope with no explicit budget falls back to its per-scope
default (env DEFAULT_*_BUDGET_USD). This lets the limits apply to rows created before
budgets existed, without a data backfill. Defaults nest: org 100 ⊇ workspace 50 ⊇
project 25.

Raises HTTPException(409) with a user-facing "Budget low" message on violation.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import (
    DEFAULT_ORG_BUDGET_USD,
    DEFAULT_PROJECT_BUDGET_USD,
    DEFAULT_WORKSPACE_BUDGET_USD,
)

_EPS = 1e-6
_DEFAULTS = {
    "org": DEFAULT_ORG_BUDGET_USD,
    "workspace": DEFAULT_WORKSPACE_BUDGET_USD,
    "project": DEFAULT_PROJECT_BUDGET_USD,
}


def effective_cap(scope: str, explicit) -> float:
    """Explicit budget when set (> 0), else the scope's default cap."""
    if explicit is not None and float(explicit) > 0:
        return float(explicit)
    return _DEFAULTS[scope]


def _fmt(x: float) -> str:
    return f"${x:,.2f}"


async def _scalar(db: AsyncSession, sql: str, params: dict) -> float:
    return float((await db.execute(text(sql), params)).scalar() or 0.0)


async def _org_cap(db: AsyncSession, tenant_id: str) -> float:
    v = (await db.execute(
        text("SELECT monthly_budget_usd FROM organizations WHERE id = :t"), {"t": tenant_id},
    )).scalar()
    return effective_cap("org", v)


async def _workspace_cap(db: AsyncSession, tenant_id: str, ws_id: str) -> float:
    v = (await db.execute(
        text("SELECT monthly_budget_usd FROM workspaces WHERE id = :w AND organization_id = :t"),
        {"w": ws_id, "t": tenant_id},
    )).scalar()
    return effective_cap("workspace", v)


async def _committed_workspaces(db: AsyncSession, tenant_id: str, exclude_id: str | None) -> float:
    sql = ("SELECT COALESCE(SUM(COALESCE(monthly_budget_usd, :d)), 0) FROM workspaces "
           "WHERE organization_id = :t")
    p: dict = {"t": tenant_id, "d": DEFAULT_WORKSPACE_BUDGET_USD}
    if exclude_id:
        sql += " AND id <> :x"
        p["x"] = exclude_id
    return await _scalar(db, sql, p)


async def _committed_projects(db: AsyncSession, tenant_id: str, ws_id: str, exclude_id: str | None) -> float:
    sql = ("SELECT COALESCE(SUM(COALESCE(monthly_budget_usd, :d)), 0) FROM projects "
           "WHERE workspace_id = :w AND tenant_id = :t AND archived = false")
    p: dict = {"w": ws_id, "t": tenant_id, "d": DEFAULT_PROJECT_BUDGET_USD}
    if exclude_id:
        sql += " AND id <> :x"
        p["x"] = exclude_id
    return await _scalar(db, sql, p)


async def assert_workspace_fits(
    db: AsyncSession, tenant_id: str, new_budget, exclude_id: str | None = None, *, on_create: bool = False,
) -> None:
    """A workspace budget can't push the sum of workspaces past the org budget."""
    cap = await _org_cap(db, tenant_id)
    others = await _committed_workspaces(db, tenant_id, exclude_id)
    val = effective_cap("workspace", new_budget)
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
    """A project budget can't push the sum of projects past the workspace budget."""
    cap = await _workspace_cap(db, tenant_id, workspace_id)
    others = await _committed_projects(db, tenant_id, workspace_id, exclude_id)
    val = effective_cap("project", new_budget)
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
    """The org budget can't be set below what its workspaces already commit."""
    committed = await _committed_workspaces(db, tenant_id, None)
    if effective_cap("org", new_org_budget) < committed - _EPS:
        raise HTTPException(
            status_code=409,
            detail=(f"Workspaces already allocate {_fmt(committed)}; the org budget can't be set below that."),
        )


async def assert_workspace_covers_projects(db: AsyncSession, tenant_id: str, ws_id: str, new_ws_budget) -> None:
    """A workspace budget can't be set below what its projects already commit."""
    committed = await _committed_projects(db, tenant_id, ws_id, None)
    if effective_cap("workspace", new_ws_budget) < committed - _EPS:
        raise HTTPException(
            status_code=409,
            detail=(f"This workspace already allocates {_fmt(committed)} to its projects; its budget can't "
                    f"be set below that."),
        )
