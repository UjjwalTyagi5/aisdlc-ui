"""The project's connected tools, rendered for the Orchestrator's routing prompt.

THE FAILURE. Asked "can you upload this approved PRD onto Confluence in the QUICKLINK
space", the Orchestrator answered itself: "No agent on this platform integrates with
Confluence. The Requirements agent works with Azure DevOps or Jira boards only." The
Requirements agent, on its own page, had published that very document to Confluence
minutes earlier. The router's roster said only what each agent does in general; nothing
told it which connectors THIS PROJECT had granted to which agent — and `Project.connectors`
holds exactly that, per stage, and is what the agents' own tool binding reads.

What is pinned:

  * each agent that has wired connectors is listed under its display name, with the
    connectors' display names;
  * kinds no agent has a tool for (`stage_tools.UNWIRED_KINDS`) are left out — telling
    the router an agent can use Slack when nothing can act on Slack is the failure in
    the other direction;
  * agents with nothing granted are not listed; a project with nothing yields `""`;
  * a failed read RAISES — the posture every other per-turn block on this socket takes.
"""
from __future__ import annotations

import pytest

from agents_orchestrator.orchestrator2 import project_tools as pt

_PROJECT = "44444444-4444-4444-4444-444444444444"
_TENANT = "22222222-2222-2222-2222-222222222222"


def _stub(monkeypatch, connectors):
    async def _fake(project_id, tenant_id):
        return dict(connectors)

    monkeypatch.setattr(pt, "_load_connectors", _fake)


async def test_no_project_yields_nothing():
    assert await pt.connected_tools_context(None, _TENANT) == ""
    assert await pt.connected_tools_context("proj-A", _TENANT) == ""


async def test_a_project_with_nothing_wired_yields_nothing(monkeypatch):
    _stub(monkeypatch, {})
    assert await pt.connected_tools_context(_PROJECT, _TENANT) == ""
    _stub(monkeypatch, {"requirements": []})
    assert await pt.connected_tools_context(_PROJECT, _TENANT) == ""


async def test_each_agents_connectors_are_listed_by_display_name(monkeypatch):
    _stub(monkeypatch, {
        "requirements": ["azure_devops", "jira", "confluence", "sharepoint"],
        "design": ["confluence", "figma"],
    })
    out = await pt.connected_tools_context(_PROJECT, _TENANT)

    assert pt.HEADER_LINE in out and pt.FOOTER_LINE in out
    assert "Requirements: Azure DevOps, Jira, Confluence, SharePoint" in out
    assert "Design: Confluence, Figma" in out


async def test_kinds_nothing_can_act_on_are_left_out(monkeypatch):
    _stub(monkeypatch, {"requirements": ["slack", "ms_teams", "github", "confluence"]})
    out = await pt.connected_tools_context(_PROJECT, _TENANT)

    assert "Confluence" in out
    for absent in ("Slack", "Microsoft Teams", "GitHub"):
        assert absent not in out, f"{absent} has no agent tool and must not be promised"


async def test_an_agent_with_only_unwired_kinds_is_not_listed(monkeypatch):
    _stub(monkeypatch, {"requirements": ["slack"], "design": ["confluence"]})
    out = await pt.connected_tools_context(_PROJECT, _TENANT)
    assert "- Requirements:" not in out
    assert "- Design: Confluence" in out


async def test_the_block_says_who_publishes_a_document(monkeypatch):
    """The rule the router needs: filing an approved document to Confluence or
    SharePoint is the producing agent's work, and a listed tool is never 'not
    integrated'."""
    _stub(monkeypatch, {"requirements": ["confluence"]})
    out = await pt.connected_tools_context(_PROJECT, _TENANT)
    lowered = out.lower()
    assert "produced" in lowered
    assert "never" in lowered and "integrat" in lowered


async def test_the_block_names_no_routing_tool():
    """`route_to_*` typed into prose is advertised to the model and derived from
    nothing — the guard the base prompt has, applied here."""
    assert "route_to_" not in pt.render({"requirements": ["confluence"]})


async def test_a_failed_read_raises_rather_than_reporting_nothing_wired(monkeypatch):
    async def _boom(project_id, tenant_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(pt, "_load_connectors", _boom)
    with pytest.raises(pt.ProjectToolsUnavailableError) as info:
        await pt.connected_tools_context(_PROJECT, _TENANT)
    assert isinstance(info.value.__cause__, RuntimeError)
