"""The project's connected tools, per agent, rendered for the routing prompt.

`connected_tools_context(project_id, tenant_id)` returns a markdown block naming, for
each agent, the connectors this project has granted it — Confluence, SharePoint, Jira,
Azure DevOps, Figma, SonarQube — and `""` when nothing is wired.

WHY THIS EXISTS. The router decides whether a message is an agent's work or its own to
answer, and it decides from a roster that says what each agent does IN GENERAL. Nothing
told it what THIS project had connected. Asked to upload an approved PRD to Confluence,
it answered "No agent on this platform integrates with Confluence. The Requirements
agent works with Azure DevOps or Jira boards only" — minutes after the Requirements
agent, on its own page, had published that document to Confluence. The prompt's own
rule, NEVER DECLINE ON AN AGENT'S BEHALF, cannot hold against a roster that names the
wrong tools.

THE SAME DATA THE AGENTS BIND FROM. `Project.connectors` is the per-stage assignment
the "Tools per stage" picker writes, and `shared/tools/stage_tools.py` derives each
agent's connector tools from it at run time. Rendering it here means the router is told
exactly what the agent it routes to will be able to do — one source, two readers,
which is the whole argument `stage_tools` makes for not keeping a second list.

ONLY KINDS AN AGENT CAN ACT ON. `stage_tools.wired_kinds()` is the set with a tool
behind it; `UNWIRED_KINDS` (Slack, Teams, GitHub today) can be granted and stored but
nothing uses them. Listing those would have the router send work to an agent that
cannot do it — the failure in the other direction.

A FAILED READ IS NOT AN UNWIRED PROJECT — the split every per-turn block on this
socket draws. `""` means nothing is granted; a read that failed raises
`ProjectToolsUnavailableError`, and `ws.py` refuses the turn visibly rather than
routing on a roster it knows to be incomplete.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from agents_orchestrator.orchestrator2.deliverables import DISPLAY_NAME
from agents_orchestrator.orchestrator2.registry import AGENT_IDS
from shared.db import get_db_session_for_tenant
from shared.tools.stage_tools import wired_kinds

logger = logging.getLogger(__name__)


class ProjectToolsUnavailableError(Exception):
    """The project's connector assignments could not be read; what is wired is UNKNOWN.

    Distinct from an empty result on purpose, exactly as the documents block's error
    is. Callers must not catch this and substitute `""`.
    """


HEADER_LINE = "--- CONNECTED TOOLS ON THIS PROJECT ---"
FOOTER_LINE = "--- END CONNECTED TOOLS ON THIS PROJECT ---"

_HEADER = (
    f"{HEADER_LINE}\n\n"
    "This project has connected the tools below to its agents. Each agent listed can use "
    "the tools named for it directly, on this project's own accounts, as part of its "
    "work.\n\n"
)
_RULES = (
    "\nWHAT THIS MEANS FOR YOUR DECISION:\n"
    "- Never say that an agent, or this platform, does not integrate with a tool listed "
    "above. It does, on this project, through the agent it is listed under.\n"
    "- Publishing, uploading or filing an APPROVED document to Confluence or SharePoint "
    "is the work of the agent that PRODUCED the document (a Requirements document is the "
    "Requirements agent's), when that agent has the tool listed above. Route it there and "
    "name the document in `reason`.\n"
    "- Any other request to read from or write to a tool listed above is that agent's "
    "work too. Route it; the agent knows what it can and cannot do with the tool.\n"
)
_FOOTER = f"{FOOTER_LINE}\n"

#: Display names for connector kinds, mirroring the project screen's own labels
#: (`shared/routers/project_scoped.py::_CONNECTOR_KIND_LABEL`). A kind missing here is
#: shown by its id rather than dropped.
_KIND_LABEL: dict[str, str] = {
    "jira": "Jira",
    "azure_devops": "Azure DevOps",
    "azure_repos": "Azure Repos",
    "github": "GitHub",
    "github_actions": "GitHub Actions",
    "slack": "Slack",
    "ms_teams": "Microsoft Teams",
    "sharepoint": "SharePoint",
    "figma": "Figma",
    "confluence": "Confluence",
    "sonarqube": "SonarQube",
}


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _agent_order(agent_id: str) -> tuple[int, str]:
    if agent_id in AGENT_IDS:
        return (0, f"{AGENT_IDS.index(agent_id):03d}")
    return (1, agent_id)


def render(connectors: Mapping[str, Any]) -> str:
    """The block for this assignment map, or `""` when nothing an agent can act on is
    granted. Pure; no I/O."""
    wired = wired_kinds()
    lines: list[str] = []
    for agent_id in sorted((k for k in connectors if k), key=_agent_order):
        kinds = [k for k in (connectors.get(agent_id) or []) if k in wired]
        if not kinds:
            continue
        name = DISPLAY_NAME.get(agent_id, agent_id)
        labels = ", ".join(_KIND_LABEL.get(k, k) for k in kinds)
        lines.append(f"- {name}: {labels}\n")
    if not lines:
        return ""
    return _HEADER + "".join(lines) + _RULES + _FOOTER


async def _load_connectors(project_id: str, tenant_id: str) -> dict[str, Any]:
    """`Project.connectors` for this project, inside the tenant's session."""
    from shared.models.orm import Project  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as db:
        project = await db.get(Project, uuid.UUID(project_id))
        return dict((getattr(project, "connectors", None) if project else None) or {})


async def connected_tools_context(project_id: str | None, tenant_id: str) -> str:
    """The project's connected tools, rendered for the routing prompt — or `""`.

    `""` never means the read failed — that raises `ProjectToolsUnavailableError`.
    """
    if not project_id:
        return ""
    if _as_uuid(project_id) is None or _as_uuid(tenant_id) is None:
        logger.warning(
            "orchestrator2 project tools asked for an unusable id: project=%r tenant=%r",
            project_id, tenant_id,
        )
        return ""
    try:
        connectors = await _load_connectors(str(project_id), str(tenant_id))
    except Exception as exc:  # noqa: BLE001 — unknown != empty; see the class docstring
        logger.warning(
            "orchestrator2 could not read connectors for project=%s tenant=%s: %s — "
            "raising rather than reporting nothing wired",
            project_id, tenant_id, exc,
        )
        raise ProjectToolsUnavailableError(
            "the project's connected tools could not be read"
        ) from exc
    return render(connectors)
