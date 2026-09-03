"""Shared run scope for agent entry paths (spec Part B).

A single context manager that assembles "what an agent needs to run correctly"
for the duration of a graph invocation:

  1. Tenant connector — resolved from tenant_id and injected via set_connector()
     so the board tools (list_board_projects, list_board_items, …) can reach
     ADO/Jira. Fail-soft: a missing tenant or a resolution failure leaves the
     connector unset and the board tools fail closed individually with a
     user-facing message (config/connectors/context.py raises on get when unset).
  2. Upstream context — context_broker.build_context(session_id, agent_id) returns
     a formatted block of the agents the target reads from (AGENT_REGISTRY
     input_artifacts). Requirements declares none → "" (no-op). Design/Dev consume
     it in their later slots; callers prepend scope.context_block to the user
     message when it is non-empty.

REQ-M3-10 credential hygiene: clear_connector() runs in finally so the connector
reference never survives the run, even on error. Only clears when this scope set
it (so a caller's outer scope, if any, is not disturbed).

This is the single helper the worker, the stage runner, and the WS/REST chat
handlers converge on. This slot wires the Requirements chat handlers; the other
paths already inject the connector and adopt this helper in their own slots.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from config.connectors.context import clear_connector, set_connector
from config.connector_factory import get_connector_for_session
from config.context_broker import build_context

logger = logging.getLogger(__name__)


@dataclass
class AgentRunScope:
    """What the agent needs for one run. Yielded by agent_run_scope()."""

    agent_id: str
    tenant_id: Optional[str]
    session_id: Optional[str]
    connector_injected: bool = False
    context_block: str = ""
    mcp_tool_count: int = 0


_BOARD_KINDS = ("azure_devops", "jira", "github_issues", "linear")


def _pick_board_kind(assigned: list[str]) -> Optional[str]:
    """Choose one board kind from a stage's assigned list, or None if empty.

    azure_devops is the lowest priority — used only when it is the SOLE board — so a
    stage with both ADO and another provider (e.g. Jira) resolves to the other one.
    This honors "don't fall back to Azure DevOps" when multiple boards are assigned.
    """
    non_ado = [k for k in assigned if k != "azure_devops"]
    return (non_ado or assigned or [None])[0]


async def stage_board_kinds(
    tenant_id: Optional[str], project_id: Optional[str], agent_id: str
) -> list[str]:
    """EVERY board kind wired to this stage, in the order the stage picker stored them.

    `_stage_board_kind` below answers "which ONE board does this stage run on by
    default"; this answers "which boards may it reach at all". They are different
    questions, and conflating them is what made a project with both Jira and Azure
    DevOps reachable only through whichever one the picker happened to choose — an
    agent asked to write to the other had no way to say so.

    Same read, same filter, no fallback to the legacy project-wide `provider_kind`,
    for the same reason: that column defaults to azure_devops for nearly every project.
    An empty list means the stage has no board, which is a real answer.
    """
    if not (tenant_id and project_id):
        return []
    try:
        import uuid as _uuid  # noqa: PLC0415

        from shared.db import get_db_session_for_tenant  # noqa: PLC0415
        from shared.models.orm import Project  # noqa: PLC0415

        async with get_db_session_for_tenant(tenant_id) as session:
            project = await session.get(Project, _uuid.UUID(str(project_id)))
            conns = (getattr(project, "connectors", None) if project else None) or {}
            return [k for k in (conns.get(agent_id) or []) if k in _BOARD_KINDS]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "stage_board_kinds: lookup failed (project=%s agent=%s): %s",
            project_id, agent_id, type(exc).__name__,
        )
        return []


async def _stage_board_kind(
    tenant_id: Optional[str], project_id: Optional[str], agent_id: str
) -> Optional[str]:
    """Board connector kind the project assigned to this stage (jira/azure_devops/…),
    or None when the stage has no board assigned.

    Reads ONLY the explicit per-stage Project.connectors[agent_id] (filtered to real
    board kinds). Deliberately does NOT fall back to the legacy project-wide
    provider_kind — that column defaults to "azure_devops" for nearly every project, so
    using it as a fallback is exactly the silent ADO the per-stage model replaced.
    Returns None when the stage has no board assigned (or on lookup failure) → the caller
    injects no connector and board tools fail closed ("connect a board"), never ADO.
    """
    if not (tenant_id and project_id):
        return None
    try:
        import uuid as _uuid  # noqa: PLC0415

        from shared.db import get_db_session_for_tenant  # noqa: PLC0415
        from shared.models.orm import Project  # noqa: PLC0415

        async with get_db_session_for_tenant(tenant_id) as session:
            project = await session.get(Project, _uuid.UUID(str(project_id)))
            conns = (getattr(project, "connectors", None) if project else None) or {}
            assigned = [k for k in (conns.get(agent_id) or []) if k in _BOARD_KINDS]
            legacy = getattr(project, "provider_kind", None) if project else None
            kind = _pick_board_kind(assigned)
            logger.info(
                "agent_run_scope: stage=%s resolved board kind=%s "
                "(connectors[%s]=%s, legacy provider_kind=%s ignored)",
                agent_id, kind, agent_id, conns.get(agent_id), legacy,
            )
            return kind
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "agent_run_scope: board-kind lookup failed (project=%s agent=%s): %s — "
            "no board connector injected (no azure_devops fallback)",
            project_id, agent_id, type(exc).__name__,
        )
    return None


@asynccontextmanager
async def agent_run_scope(
    *,
    agent_id: str,
    tenant_id: Optional[str],
    session_id: Optional[str],
    project_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> AsyncIterator[AgentRunScope]:
    """Inject the tenant connector + upstream context for one agent graph run.

    Args:
        agent_id: AGENT_REGISTRY id ("requirements", "design", …) — selects which
            upstream artifacts build_context fetches.
        tenant_id: Tenant whose board connector to inject. Falsy → no connector.
        session_id: Run/session id for build_context. Falsy → no upstream context.
        owner_id: the turn's own user. Lets the board connector and any MCP
            servers prefer THIS person's saved credential over the tenant/org
            one — see BaseConnector._resolve_credential_override and
            mcp_registry.resolve_server_configs. Omitted, behaviour is
            unchanged (tenant/org credentials only).

    Yields:
        AgentRunScope with .connector_injected and .context_block populated.
    """
    scope = AgentRunScope(agent_id=agent_id, tenant_id=tenant_id, session_id=session_id)

    if tenant_id:
        # Inject the board connector the project assigned to THIS stage (jira /
        # azure_devops / …) — not a hardcoded provider — so the chat agent's board
        # tools target the same board the Pull-stories UI does. When the stage has no
        # board assigned, inject nothing (no azure_devops fallback): board tools then
        # fail closed with a "connect a board" message rather than hitting ADO.
        kind = await _stage_board_kind(tenant_id, project_id, agent_id)
        if kind:
            try:
                # Bound to this project's effective access (unit grant ∩ project
                # narrowing). Board tools then fail closed on an operation the
                # grant does not admit, the same way they do with no connector.
                #
                # `agent_id` is REQUIRED, not decorative. Since migration 0024 the
                # level is stored per (stage, tool), and `effective_access` returns
                # None for a caller that names no stage — so passing project_id
                # alone resolved to no access and every board tool in every chat
                # turn was denied, however the project was granted. Omitting it
                # fails in the safe direction, which is why it read as a broken
                # board rather than a bug. See copilot_api.py, which already does
                # this.
                connector = await get_connector_for_session(
                    kind=kind, tenant_id=tenant_id, project_id=str(project_id or ""),
                    owner_id=owner_id or "",
                    agent_id=agent_id,
                )
                set_connector(connector)
                scope.connector_injected = True
            except Exception as exc:  # noqa: BLE001
                # Fail-soft: board tools fail closed individually if called without a
                # connector. A resolution error must not crash the chat turn.
                logger.warning(
                    "agent_run_scope: connector resolution failed for tenant=%s kind=%s: %s",
                    tenant_id, kind, type(exc).__name__,
                )
                scope.connector_injected = False
        else:
            logger.info(
                "agent_run_scope: stage=%s has no board assigned — no connector injected "
                "(board tools fail closed; no azure_devops fallback)",
                agent_id,
            )

    if session_id:
        try:
            scope.context_block = await build_context(session_id, agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent_run_scope: build_context failed for agent=%s: %s",
                agent_id, type(exc).__name__,
            )
            scope.context_block = ""

    # MCP: resolve the project's per-stage server selection and bind those tools for
    # the duration of the run (interactive-chat surface). project_id comes from the
    # chat's pipeline_context; absent project / disabled MCP → no tools (no-op).
    from shared.services.mcp_injection import (  # noqa: PLC0415
        mcp_tools_scope,
        project_stage_server_ids,
    )

    server_ids = await project_stage_server_ids(tenant_id, project_id, agent_id)
    try:
        async with mcp_tools_scope(
            tenant_id, server_ids, agent_id, project_id=project_id, owner_id=owner_id,
        ) as _mcp_tools:
            scope.mcp_tool_count = len(_mcp_tools)
            yield scope
    finally:
        if scope.connector_injected:
            clear_connector()
