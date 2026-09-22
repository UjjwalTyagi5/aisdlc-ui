"""Read a project's code-quality state from SonarQube, and act on its findings.

THIRTEEN OPERATIONS AND NO WAY TO REACH THEM. `config/connectors/sonarqube.py`
implements six reads and seven writes; not one had an agent tool, so a project could
grant SonarQube to its Security or Code Review stage, see it stored and enforced, and
watch nothing happen. This is the same gap `make_confluence_tools` closed for
Confluence — see `shared/tools/stage_tools` for why the two surfaces drifted apart.

WHY THE QUALITY GATE IS THE FIRST TOOL AND NOT THE ISSUE LIST. A gate status is one
word backed by named conditions, and it is the answer to the question anybody actually
asks — "can this ship". An issue list is hundreds of rows that an agent will summarise
badly and a reader will not check. The list is here for when the gate has failed and
somebody needs to know what to fix.

WRITES ARE DELIBERATELY NARROW. The connector can create and DELETE projects, set
quality gates, and change issue severities — administrative acts with consequences
outside this platform and no undo through it. Of its seven writes this exposes two:
commenting on an issue and transitioning one. Both are the kind of thing an agent
triaging a finding should be able to do, both are reversible in SonarQube by a person,
and neither changes what the gate MEANS. Widening this is a product decision; see
`_WRITE_TOOLS` in shared/tools/stage_tools for how the level gates what is bound.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

#: How many issues one call will put in front of a model. SonarQube projects routinely
#: carry thousands; a tool that returns all of them produces a context window of noise
#: and an answer nobody can act on. The refusal names the count so a reader knows the
#: list was cut rather than that the project is clean.
_MAX_ISSUES = 50


async def _resolve(agent_id: str):
    """(connector, project_key) for this session, or (None, reason)."""
    from config.ws_helper import get_project_id, get_tenant_id, get_user_id  # noqa: PLC0415

    tenant_id = get_tenant_id() or ""
    project_id = get_project_id() or ""
    if not tenant_id or not project_id:
        return None, "ERROR: this conversation is not attached to a project."

    try:
        from config.connector_factory import get_connector_for_session  # noqa: PLC0415

        connector = await get_connector_for_session(
            kind="sonarqube", tenant_id=tenant_id,
            project_id=project_id, agent_id=agent_id,
            owner_id=get_user_id() or "",
        )
        return connector, ""
    except Exception as exc:  # noqa: BLE001
        # Type name only: a connector error can carry a token or a host.
        return None, (
            f"ERROR reaching SonarQube: {type(exc).__name__}. It may not be connected "
            "for this project — an admin connects it on the project's Integrations page."
        )


def _needs_project(project: str) -> str:
    return (
        "ERROR: which SonarQube project? Pass its project key — "
        "list_sonarqube_projects returns the keys this account can see."
    ) if not project.strip() else ""


def make_sonarqube_tools(agent_id: str, stage: str) -> list:
    """SonarQube tools bound to one agent.

    `agent_id` fixes which grant is resolved and is set by the agent that registers
    these, never by a tool argument — the contract every factory here states, because a
    model able to name its own stage could borrow another one's access.

    `stage` is unused: SonarQube findings belong to the codebase, not to a pipeline
    stage the way a document does. It stays in the signature so every factory in the
    registry has one shape.
    """

    @tool
    async def list_sonarqube_projects(search: str = "") -> str:
        """List the SonarQube projects this account can see, with their keys."""
        connector, reason = await _resolve(agent_id)
        if connector is None:
            return reason
        try:
            rows = await connector.read_adapter("list_projects", project=search)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR listing SonarQube projects: {type(exc).__name__}"
        if not rows:
            return "No SonarQube projects are visible to this account."
        return "\n".join(
            f"- {r.get('name', '?')} (key: {r.get('key', '?')})" for r in rows[:100]
        )

    @tool
    async def check_quality_gate(project: str = "") -> str:
        """Whether a SonarQube project currently passes its quality gate, and why not.

        THE TOOL TO REACH FOR FIRST. One word backed by the conditions that produced
        it — which is the question behind "is this ready to merge" or "can we deploy".

        Args:
            project: the SonarQube project key.
        """
        refusal = _needs_project(project)
        if refusal:
            return refusal
        connector, reason = await _resolve(agent_id)
        if connector is None:
            return reason
        try:
            gate: Any = await connector.read_adapter("get_quality_gate_status", project=project)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading the quality gate for {project}: {type(exc).__name__}"
        if not gate:
            return f"ERROR: no quality gate result for {project!r}."

        status = gate.get("status") or "unknown"
        failed = [c for c in (gate.get("conditions") or []) if c.get("status") == "ERROR"]
        if status == "OK":
            return f"{project}: quality gate PASSES."
        if not failed:
            # A non-OK status with no failing condition is a real SonarQube state
            # (gate not computed yet, or no analysis). Saying "fails" with nothing to
            # show sends somebody hunting for a condition that is not there.
            return (
                f"{project}: quality gate status is {status!r} with no failing "
                "conditions reported — the project may not have been analysed yet."
            )
        lines = [f"{project}: quality gate FAILS on {len(failed)} condition(s):"]
        lines += [
            f"- {c.get('metric', '?')}: {c.get('actual', '?')} "
            f"(threshold {c.get('threshold', '?')})"
            for c in failed
        ]
        return "\n".join(lines)

    @tool
    async def list_sonarqube_issues(
        project: str = "", severities: str = "", state: str = ""
    ) -> str:
        """The open findings on a SonarQube project — what to fix when the gate fails.

        Args:
            project:    the SonarQube project key.
            severities: comma-separated, e.g. "BLOCKER,CRITICAL". Omit for all.
            state:      issue status filter, e.g. "OPEN". Omit for the default.
        """
        refusal = _needs_project(project)
        if refusal:
            return refusal
        connector, reason = await _resolve(agent_id)
        if connector is None:
            return reason
        try:
            issues = await connector.read_adapter(
                "list_issues", project=project, severities=severities, state=state,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR listing SonarQube issues: {type(exc).__name__}"
        if not issues:
            return f"{project} has no open SonarQube issues matching that filter."

        shown = issues[:_MAX_ISSUES]
        lines = [
            f"- [{i.get('severity', '?')}] {i.get('message', '')} "
            f"({i.get('component', '')}:{i.get('line', '?')}) key={i.get('key', '?')}"
            for i in shown
        ]
        if len(issues) > len(shown):
            # Named, so a truncated list is never mistaken for a short one.
            lines.append(
                f"… {len(issues) - len(shown)} more not shown. Narrow with `severities`."
            )
        return "\n".join(lines)

    @tool
    async def comment_on_sonarqube_issue(issue_key: str = "", text: str = "") -> str:
        """Leave a comment on one SonarQube issue — triage notes, or why it is accepted.

        Args:
            issue_key: from list_sonarqube_issues.
            text:      the comment body.
        """
        if not issue_key.strip() or not text.strip():
            return "ERROR: both an issue key and a comment are required."
        connector, reason = await _resolve(agent_id)
        if connector is None:
            return reason
        try:
            await connector.write_adapter("add_comment", issue_key=issue_key, text=text)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR commenting on {issue_key}: {type(exc).__name__}"
        return f"Commented on SonarQube issue {issue_key}."

    @tool
    async def transition_sonarqube_issue(issue_key: str = "", transition: str = "") -> str:
        """Move one SonarQube issue through its workflow, e.g. confirm or resolve.

        Reversible in SonarQube by a person, which is why this is exposed while the
        connector's administrative writes — creating and deleting projects, setting
        quality gates — are not.

        Args:
            issue_key:  from list_sonarqube_issues.
            transition: the SonarQube transition name, e.g. "confirm", "resolve",
                        "falsepositive", "wontfix", "reopen".
        """
        if not issue_key.strip() or not transition.strip():
            return "ERROR: both an issue key and a transition are required."
        connector, reason = await _resolve(agent_id)
        if connector is None:
            return reason
        try:
            await connector.write_adapter(
                "transition_issue", issue_key=issue_key, transition=transition,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                f"ERROR transitioning {issue_key}: {type(exc).__name__}. "
                f"{transition!r} may not be available from this issue's current state."
            )
        return f"SonarQube issue {issue_key} transitioned to {transition!r}."

    return [
        check_quality_gate,
        list_sonarqube_issues,
        list_sonarqube_projects,
        comment_on_sonarqube_issue,
        transition_sonarqube_issue,
    ]
