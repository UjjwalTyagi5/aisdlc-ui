"""The tools a stage gets, derived from the grant instead of hand-written per agent.

THE DEFECT THIS CLOSES. Reach is per-project DATA — `projects.tool_access_modes`, keyed
`{agent_id}::connector::{target_ref}`, written by the "Tools per stage" picker and
resolved per request. Wiring was per-agent CODE — a literal list of tools fixed when the
module imported. Two surfaces of different shapes with nothing checking one against the
other, so a grant could name a connector the agent had no tool for, and did.

That is not a hypothetical. Asked to publish an approved BRD to Confluence, the PM agent
replied that the platform "only publishes to the project's SharePoint library" — while
Confluence sat granted and read-write in that project's settings. The permission was
real, the tool did not exist, and nothing anywhere reconciled the two. Measured across
the repo at the time: 120 grantable (agent, connector) pairs, 23 with a tool behind them.

THE FIX IS TO STOP MAINTAINING THE SECOND LIST. An agent no longer names its connectors;
it asks what this project granted THIS stage and binds that. Adding a connector to a
project is then a configuration change, not a code change, which is what the picker
already implied it was.

WHY THIS CAN BE PER REQUEST. `resolve_chat_model(tools=...)` is called from the agent
node on every invocation, not at import, and the tenant and project are already in
scope there through `config.ws_helper` contextvars — the same way every connector tool
already resolves its session. So the tool list can be derived per run rather than baked.

THE LEVEL FILTERS THE LIST, IT DOES NOT ONLY GUARD THE CALL. A stage granted `read` gets
no write tools at all, rather than write tools that raise `ConnectorAccessDenied` when
used. Binding a tool the grant forbids makes the model offer an action, attempt it, and
fail — three wasted steps and a confusing transcript, where the honest rendering of
"read-only" is simply not offering to write. `ScopedConnector` still enforces the level
underneath; this is defence in depth, not a replacement for it.

FAILURE IS EMPTY, NEVER AN EXCEPTION. A grant lookup that errors yields no connector
tools and logs — an agent with fewer tools degrades to answering from its own knowledge,
while an agent that raises on model resolution is a dead conversation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectorToolSpec:
    """How to build one connector's tools, and which of them write.

    `write_tools` is declared rather than inferred from the name. A guess like
    "starts with publish_ or create_" is right until somebody adds `create_report`
    that writes nothing, or `sync_board` that writes plenty — and being wrong in the
    permissive direction hands a read-only stage a write tool.
    """

    factory: Callable[[str, str], list]
    write_tools: frozenset[str]


def _confluence_spec() -> ConnectorToolSpec:
    from shared.tools.confluence_artifacts import make_confluence_tools  # noqa: PLC0415

    return ConnectorToolSpec(
        factory=make_confluence_tools,
        write_tools=frozenset({
            "create_confluence_space",
            "create_confluence_page",
            "update_confluence_page",
            "comment_on_confluence_page",
            "publish_approved_to_confluence",
            "attach_file_to_confluence_page",
        }),
    )


def _sharepoint_spec() -> ConnectorToolSpec:
    from shared.tools.sharepoint_artifacts import make_sharepoint_tools  # noqa: PLC0415

    return ConnectorToolSpec(
        factory=make_sharepoint_tools,
        write_tools=frozenset({"publish_approved_to_sharepoint"}),
    )


def _sonarqube_spec() -> ConnectorToolSpec:
    from shared.tools.sonarqube_quality import make_sonarqube_tools  # noqa: PLC0415

    return ConnectorToolSpec(
        factory=make_sonarqube_tools,
        write_tools=frozenset(
            {"comment_on_sonarqube_issue", "transition_sonarqube_issue"}
        ),
    )


#: kind -> how to build its tools, for the connectors this registry owns. Kinds are
#: added here as their factories are written; `_WIRED_ELSEWHERE` below covers the ones
#: whose tools exist but are bound by an older mechanism.
_SPECS: dict[str, Callable[[], ConnectorToolSpec]] = {
    "confluence": _confluence_spec,
    "sharepoint": _sharepoint_spec,
    "sonarqube": _sonarqube_spec,
}

#: Kinds whose agent tools exist but are bound by an OLDER mechanism than this registry,
#: so granting them works while `_SPECS` knows nothing about them. Listed so the picker
#: can tell "wired" from "does nothing" — leaving them out would make it hide settings
#: that function.
#:
#: jira / azure_devops   `_BOARD_TOOLS` in requirements_agent/agents/planning.py — one
#:                       provider-agnostic list resolving its board per call, imported
#:                       by the agents that need it. Moving it into this registry means
#:                       unpicking that resolution and belongs in its own change.
#: figma                 three tools in design_architecture_agent/tools/figma_tools.py,
#:                       whose session helper hardcodes `agent_id="design"`. Making it a
#:                       factory would change WHICH stages can use Figma — a product
#:                       decision, not a mechanical one.
_WIRED_ELSEWHERE: frozenset[str] = frozenset({"jira", "azure_devops", "figma"})

#: Kinds a project can grant today that NO agent has a tool for. Granting one is a
#: setting that cannot take effect: the permission is stored, the picker shows it, and
#: no agent can act on it. Named here rather than inferred from absence so that adding a
#: connector to the catalogue without tools is a deliberate act with a visible cost.
#:
#: github / github_actions have connector operations (issues; workflow dispatch, runs
#: and logs) and no tools yet — the same shape sonarqube had, and the same fix.
#:
#: slack / ms_teams are a genuine open question rather than an oversight: both are
#: reached today as NOTIFICATION targets (shared/services/notification_targets.py), not
#: as agent tools, so a grant may be serving that purpose. Whether they should also be
#: agent tools is a product call.
UNWIRED_KINDS: frozenset[str] = frozenset(
    {"github", "github_actions", "slack", "ms_teams"}
)


def wired_kinds() -> frozenset[str]:
    """Connector kinds an agent can actually act on, however they are bound.

    What the "Tools per stage" picker should treat as real. A kind outside this set can
    still be granted — the grant is stored and enforced — but nothing will use it.
    """
    return frozenset(_SPECS) | _WIRED_ELSEWHERE


async def _granted_level(kind: str, tenant_id: str, project_id: str, agent_id: str) -> Optional[str]:
    """This stage's access level for one connector, or None for no grant at all."""
    from shared.authz.connector_grants import effective_access  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as db:
        return await effective_access(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            target_ref=kind,
            kind="connector",
            agent_id=agent_id,
        )


async def tools_for_stage(agent_id: str, stage: str) -> list:
    """Every connector tool this project granted this stage, at the level it granted.

    `agent_id` and `stage` come from the agent that calls this and never from a tool
    argument or the model — the same contract the factories themselves state. A model
    able to name its own stage could borrow another one's grant.

    Returns [] when there is no session, no grant, or the lookup fails. An agent with
    no connector tools still answers; an agent that raised here would not.
    """
    from config.ws_helper import get_project_id, get_tenant_id  # noqa: PLC0415

    from shared.authz.connector_access import permits  # noqa: PLC0415

    tenant_id = get_tenant_id() or ""
    project_id = get_project_id() or ""
    if not tenant_id or not project_id:
        # Not an error: agent-studio previews and health probes have no project, and
        # a connector tool with no project resolves to a connector that permits
        # nothing anyway.
        return []

    out: list = []
    for kind, build_spec in _SPECS.items():
        try:
            level = await _granted_level(kind, tenant_id, project_id, agent_id)
            if level is None:
                continue
            spec = build_spec()
            for tool in spec.factory(agent_id, stage):
                mode = "write" if tool.name in spec.write_tools else "read"
                if permits(level, mode):
                    out.append(tool)
        except Exception:  # noqa: BLE001 — see the module docstring: empty, never raise
            logger.warning(
                "stage tools: could not resolve %s for stage %s", kind, agent_id,
                exc_info=True,
            )
    return out
