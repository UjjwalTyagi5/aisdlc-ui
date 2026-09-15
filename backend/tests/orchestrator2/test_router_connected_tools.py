"""The router is told which tools this project has connected to which agent.

The delta is asserted, never the whole prompt — see `test_router_project_documents.py`
for why — and the end-to-end shape goes through `route` so a `route` that builds the
block and sends the plain prompt is caught.
"""
from __future__ import annotations

import inspect

import pytest

from agents_orchestrator.orchestrator2 import router as rtr

_BLOCK = (
    "--- CONNECTED TOOLS ON THIS PROJECT ---\n"
    "- Requirements: Azure DevOps, Jira, Confluence, SharePoint\n"
    "--- END CONNECTED TOOLS ON THIS PROJECT ---\n"
)
_DOCS = (
    "--- APPROVED DOCUMENTS IN THIS PROJECT ---\n"
    "- **QuickLink_BRD_new.docx** — id `eee5170d`\n"
    "--- END APPROVED DOCUMENTS IN THIS PROJECT ---\n"
)


def test_route_accepts_the_projects_connected_tools():
    param = inspect.signature(rtr.route).parameters["connected_tools"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default == ""


def test_the_block_reaches_the_prompt_verbatim():
    base = rtr._system_prompt()
    built = rtr._system_prompt_with_continuity(None, connected_tools=_BLOCK)
    assert built.startswith(base)
    assert _BLOCK in built[len(base):]


def test_no_connected_tools_means_no_addition():
    assert rtr._system_prompt_with_continuity(None, connected_tools="") == rtr._system_prompt()


def test_tools_documents_and_continuity_compose():
    built = rtr._system_prompt_with_continuity(
        "development", documents=_DOCS, connected_tools=_BLOCK,
    )
    assert built.startswith(rtr._system_prompt())
    assert f"{rtr._TOOL_PREFIX}development" in built
    assert "QuickLink_BRD_new.docx" in built
    assert "Confluence, SharePoint" in built


def test_the_base_prompt_no_longer_names_only_boards_and_repos():
    """The hand-written bullet used to enumerate 'Azure DevOps boards and repositories,
    Jira, GitHub' — a list the model read as exhaustive. Document systems belong in
    it, and the per-project block is named as the authority."""
    prompt = rtr._system_prompt()
    assert "Confluence" in prompt and "SharePoint" in prompt


@pytest.mark.asyncio
async def test_route_sends_the_connected_tools_to_the_model(monkeypatch):
    seen = {}

    async def _fake_ask(text, **kwargs):
        seen["system"] = kwargs.get("system_prompt") or ""
        return rtr.RoutingDecision(agent_id="requirements", reason="r", direct_reply=None)

    monkeypatch.setattr(rtr, "_ask_model", _fake_ask)
    await rtr.route(
        "upload the approved PRD to Confluence", history=[], run_id="r1",
        tenant_id="t1", project_id="p1", model_id=None, offering_id=None,
        connected_tools=_BLOCK,
    )
    assert "Confluence, SharePoint" in seen["system"]
    assert seen["system"] != rtr._system_prompt()


def test_the_added_text_names_no_routing_tool_outside_the_registry():
    import re

    base = rtr._system_prompt()
    added = rtr._system_prompt_with_continuity(None, connected_tools=_BLOCK)[len(base):]
    named = set(re.findall(rf"{rtr._TOOL_PREFIX}(\w+)", added))
    assert named <= set(rtr.REGISTRY)
