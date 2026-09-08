"""The project's connector, bound for the duration of one turn.

WHY THIS MODULE EXISTS. The agents' board and repo tools do not take credentials as
arguments — they read a connector off the `config.connectors.context` contextvar.
`development_agent/tools/git_tools.py::_active_ado_creds` says so in its own words:
"With no connector bound, this returns ("", "") — there is no process-wide PAT to
fall back on." Nothing in `orchestrator2` bound one, so the Development agent, asked
to pull code on a project with Azure DevOps connected, correctly answered that no ADO
credentials were configured. It could not see them.

This is the adapter D10a anticipated. That decision reuses the agents' compiled graphs
and never their `*_agent_api.py` wrappers, on the measurement that the wrappers hold
permission machinery the Orchestrator does not need — and it recorded the cost: "if
some agent's graph turns out to depend on state its wrapper used to provide, that
agent needs an adapter. The registry is the natural place for such an adapter, so this
stays reversible per agent." The connector is that state, and this is that adapter.
It is deliberately NOT a copy of the wrapper: it takes the connector and nothing else.

WHAT MAKES A CONNECTOR RESOLVE, and why every argument is load-bearing:

  · `tenant_id`  — whose secret store is read.
  · `project_id` — the connector is bound to this project's effective access (unit
                   grant ∩ project narrowing). Without it the connector permits
                   nothing, which is the correct answer for a turn that cannot be
                   scoped, and exactly what the Orchestrator's project-scoping rule
                   demands: the run's project decides, never the client frame.
  · `owner_id`   — the turn's own user. A project admin can save an Azure DevOps PAT
                   for just their own project (`project_integration_credentials`),
                   and on this platform that is how it is usually done. Resolved
                   WITHOUT an owner the connector comes back with no PAT at all,
                   which the agent reports identically to having no connector — so
                   omitting it silently reproduces the bug this module fixes.
  · `agent_id`   — the agent IS the access decision since migration 0024: the level
                   lives per (agent, tool), so project scope alone resolves to no
                   access.

CREDENTIALS DO NOT OUTLIVE THE TURN. `clear_connector()` runs in a `finally`, on every
path including a failed turn. A connector left bound would hand the next turn — quite
possibly another project's — this one's live PAT.

FAILING TO RESOLVE ONE IS NOT AN ERROR. A project with nothing connected is an
ordinary state. The turn proceeds unbound and the agent says it cannot reach the
board, which is true and is the one case where that message is the right answer.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from agents_orchestrator.orchestrator2.project_config import project_maps

logger = logging.getLogger(__name__)

#: What a project with no per-agent selection means. Matches
#: `workflows.activities._base.stage_connector_kind`'s own default, so the pipeline
#: and the Orchestrator agree about an unconfigured project rather than disagreeing
#: quietly.
DEFAULT_CONNECTOR_KIND = "azure_devops"


async def _project_connectors(tenant_id: str, project_id: str) -> dict:
    """The project's `{agent_id: [kind, ...]}` selection map.

    Delegates to `project_config.project_maps`, which reads both per-agent maps in one
    tenant-scoped query — `mcp.py` needs the other one at the same moment, for the same
    turn, and two reads of one row is one read too many. Fail-soft there, so a project
    with nothing selected falls back to `DEFAULT_CONNECTOR_KIND` rather than ending the
    turn.
    """
    connectors, _mcp = await project_maps(tenant_id, project_id)
    return connectors


async def connector_kind_for(
    agent_id: str, *, tenant_id: str, project_id: str
) -> str:
    """Which connector kind this agent should get on this project.

    The project's first selected kind wins — the contextvar holds one connector at a
    time, so a multi-kind selection is resolved here rather than deferred to a tool
    that has no way to choose. Unselected falls back to `DEFAULT_CONNECTOR_KIND`.
    """
    selection = await _project_connectors(tenant_id, project_id)
    kinds = selection.get(agent_id) if isinstance(selection, dict) else None
    if kinds:
        return kinds[0]
    return DEFAULT_CONNECTOR_KIND


async def _resolve_connector(
    *, kind: str, tenant_id: str, project_id: str, owner_id: str, agent_id: str
) -> Any:
    """The credentialed connector for this (project, agent, person).

    Split out as its own function so tests can replace the resolution without
    reaching into `config.connector_factory`, and so the argument list — every member
    of which is load-bearing, see the module docstring — is stated in one place.
    """
    from config.connector_factory import get_connector_for_session

    return await get_connector_for_session(
        kind=kind,
        tenant_id=tenant_id,
        project_id=project_id,
        owner_id=owner_id,
        agent_id=agent_id,
    )


@asynccontextmanager
async def bound_connector(
    agent_id: str,
    *,
    tenant_id: str,
    project_id: str | None,
    owner_id: str,
) -> AsyncIterator[bool]:
    """Bind the project's connector for this turn; always unbind it afterwards.

    Yields whether one was actually bound — useful to a caller that wants to log it,
    and deliberately not an error either way.
    """
    injected = False
    if tenant_id and project_id:
        try:
            kind = await connector_kind_for(
                agent_id, tenant_id=tenant_id, project_id=str(project_id)
            )
            connector = await _resolve_connector(
                kind=kind,
                tenant_id=tenant_id,
                project_id=str(project_id),
                owner_id=owner_id,
                agent_id=agent_id,
            )
            from config.connectors.context import set_connector

            set_connector(connector)
            injected = True
        except Exception as exc:  # noqa: BLE001 — an unconnected project is normal
            logger.warning(
                "orchestrator2 bound no connector for agent=%s project=%s: %s — the "
                "turn proceeds and the agent will report it cannot reach the board",
                agent_id, project_id, exc,
            )
    try:
        yield injected
    finally:
        if injected:
            try:
                from config.connectors.context import clear_connector

                clear_connector()
            except Exception:  # noqa: BLE001 — nothing useful remains to do
                logger.exception("orchestrator2 could not clear the bound connector")
