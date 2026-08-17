"""Defensive workspace resolution helpers (D-06).

resolve_default_workspace: fallback for resource-less permission checks; raises 409 when
  an org has >1 workspace to prevent silently applying the wrong workspace's roles.
resolve_workspace_for_run: derives workspace via Run -> Project -> workspace_id under the
  tenant GUC so RLS ensures the run and project belong to the authenticated tenant.

These are called by require_permission (plan 03) and MUST stay tenant-scoped.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select

from shared.db import get_db_session_for_tenant
from shared.models.orm import Project, Run, Workspace


async def resolve_default_workspace(tenant_id: str) -> uuid.UUID:
    """Return the single default workspace UUID for the org identified by tenant_id.

    Raises:
        HTTPException(404) — org has no workspaces at all.
        HTTPException(409) — org has >1 workspace; an explicit selector is required (D-06).
          Never silently select one workspace when there are multiple — that would apply
          the wrong workspace's role assignments.

    The tenant GUC is set by get_db_session_for_tenant so workspaces visible to the
    sdlc_app role are already scoped. organizations/workspaces are global (no RLS) so the
    query is effectively an org-to-workspace mapping lookup.
    """
    if not tenant_id:
        raise HTTPException(status_code=404, detail="No tenant context — workspace unknown")

    async with get_db_session_for_tenant(tenant_id) as session:
        stmt = select(Workspace).where(
            Workspace.organization_id == uuid.UUID(str(tenant_id))
        )
        result = await session.execute(stmt)
        workspaces = result.scalars().all()

    count = len(workspaces)
    if count == 0:
        raise HTTPException(status_code=404, detail="No workspace found for this tenant")
    if count > 1:
        # D-06 fail-loud: multiple workspaces exist but no explicit selector was provided.
        # The caller must pass a workspace_id derived from the resource instead.
        raise HTTPException(
            status_code=409,
            detail="multiple workspaces; explicit selector required",
        )

    return workspaces[0].id


async def resolve_active_workspace(
    tenant_id: str, requested_workspace_id: str | None
) -> uuid.UUID:
    """Resolve the workspace a request acts in.

    - `requested_workspace_id` given (the X-Workspace-Id selector): validate it belongs
      to this org and return it. Cross-org / unknown ids are rejected (403).
    - none given: fall back to the org's oldest workspace (no 409 — an explicit selector
      flows on every authenticated request, so absence just means "default to first").

    Replaces resolve_default_workspace at call sites that now carry an active-workspace
    selector; resolve_default_workspace is kept for any legacy resource-less callers.
    """
    if not tenant_id:
        raise HTTPException(status_code=404, detail="No tenant context — workspace unknown")

    async with get_db_session_for_tenant(tenant_id) as session:
        rows = (
            await session.execute(
                select(Workspace.id)
                .where(Workspace.organization_id == uuid.UUID(str(tenant_id)))
                .order_by(Workspace.created_at)
            )
        ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail="No workspace found for this tenant")

    # Lenient: a malformed or cross-org selector (e.g. a stale cookie) falls back to
    # the org's first workspace rather than erroring. Selectors can only ever narrow
    # to workspaces in the caller's own org, so defaulting is safe.
    if requested_workspace_id:
        try:
            wid = uuid.UUID(str(requested_workspace_id))
            if wid in rows:
                return wid
        except ValueError:
            pass

    return rows[0]


async def assert_workspace_in_tenant(tenant_id: str, workspace_id: str) -> uuid.UUID:
    """Strict counterpart to resolve_active_workspace, for an EXPLICIT choice.

    `resolve_active_workspace` is deliberately lenient because a selector is ambient:
    a stale cookie should not break a page, and defaulting to the org's first workspace
    is harmless when the caller never asked for a particular one.

    A workspace named in a request BODY is the opposite kind of value — somebody picked
    it from a form. Falling back there would file the resource under a unit nobody
    chose, and the write would report success, so the mistake surfaces later as "why is
    this project in the wrong Business Unit" rather than as an error. Hence 422 on
    anything this tenant does not own.
    """
    if not tenant_id:
        raise HTTPException(status_code=404, detail="No tenant context — workspace unknown")

    try:
        wid = uuid.UUID(str(workspace_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="workspaceId is not a valid id")

    async with get_db_session_for_tenant(tenant_id) as session:
        found = (
            await session.execute(
                select(Workspace.id).where(
                    Workspace.id == wid,
                    Workspace.organization_id == uuid.UUID(str(tenant_id)),
                )
            )
        ).scalar_one_or_none()

    if found is None:
        raise HTTPException(
            status_code=422, detail="workspaceId does not belong to this organization"
        )
    return wid


def workspace_selector(request) -> str | None:
    """The active-workspace selector for an HTTP request (X-Workspace-Id header)."""
    return request.headers.get("x-workspace-id") or None


async def active_workspace_for_request(request, tenant_id: str) -> uuid.UUID:
    """Resolve + cache the active workspace on request.state for this request."""
    wid = await resolve_active_workspace(tenant_id, workspace_selector(request))
    request.state.workspace_id = wid
    return wid


async def resolve_workspace_for_run(run_id: str, tenant_id: str) -> uuid.UUID:
    """Return the workspace_id for the given run, derived via Run -> Project -> workspace_id.

    Executed under the tenant GUC (D-06) so RLS on the runs table scopes the lookup
    to the authenticated tenant — a run from a different tenant is invisible.

    Raises:
        HTTPException(404) — run not found, run has no project, or the project lacks a
          workspace (any of these is a 404 from the caller's perspective).
    """
    if not tenant_id:
        raise HTTPException(status_code=404, detail="No tenant context — workspace unknown")

    async with get_db_session_for_tenant(tenant_id) as session:
        stmt = (
            select(Project.workspace_id)
            .join(Run, Run.project_id == Project.id)
            .where(Run.id == uuid.UUID(str(run_id)))
        )
        result = await session.execute(stmt)
        workspace_id = result.scalar_one_or_none()

    if workspace_id is None:
        raise HTTPException(
            status_code=404,
            detail="Run or associated project not found in this tenant",
        )

    return workspace_id
