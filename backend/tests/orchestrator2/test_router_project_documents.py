"""The router is told what the project has approved.

THE BUG. `route` answers questions about existing work itself — that is the direct-reply
path — and the only context that call had was the conversation and the agent roster. A
project with an approved BRD on its Requirements page was therefore described by its
own Orchestrator as "a fresh or empty project context". The block built by
`project_documents.approved_documents_context` has to REACH the routing prompt, and the
prompt has to say what to do with it.

Mirrors `test_router_continuity.py`: the delta the feature adds is asserted, never the
whole prompt (the roster names every agent already, so a whole-prompt search proves
nothing), and the end-to-end shape goes through `route` so a `route` that computes the
block and then sends the plain prompt is caught.
"""
from __future__ import annotations

import inspect

import pytest

from agents_orchestrator.orchestrator2 import router as rtr

_BLOCK = (
    "--- APPROVED DOCUMENTS IN THIS PROJECT ---\n\n"
    "### Requirements\n"
    "- **QuickLink_BRD_v1.docx** — id `942db0a8-8920-4864-9274-fa6cef071039`\n"
    "--- END APPROVED DOCUMENTS IN THIS PROJECT ---\n"
)


def test_route_accepts_the_projects_documents():
    """Keyword-only and defaulted to empty: a run with no project, or a project with
    nothing approved, is the ordinary case, not an error."""
    param = inspect.signature(rtr.route).parameters["documents"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default == ""


def _documents_text(block):
    """Only what the documents ADDED to the prompt."""
    base = rtr._system_prompt()
    built = rtr._system_prompt_with_documents(base, block)
    assert built.startswith(base), "the documents must add to the prompt, not rewrite it"
    return built[len(base):]


def test_the_block_reaches_the_prompt_verbatim():
    added = _documents_text(_BLOCK)
    assert _BLOCK in added
    assert "QuickLink_BRD_v1.docx" in added


def test_the_prompt_says_to_answer_what_exists_from_the_list():
    """The whole point: "are there any artifacts in this project?" must be answered
    from the record, and the prompt is where the model learns that."""
    added = _documents_text(_BLOCK).lower()
    assert "read_document" in added
    assert "empty" in added or "no documents" in added or "nothing" in added, (
        "the prompt must forbid the 'fresh or empty project' answer outright"
    )


def test_the_prompt_says_where_a_request_about_a_document_goes():
    """Reading, exporting or building on a document is agent work, and the agent that
    produced the document's stage is the one with `read_document` for it."""
    added = _documents_text(_BLOCK).lower()
    assert "export" in added or "pdf" in added
    assert "stage" in added or "produced" in added


def test_no_documents_means_no_addition():
    """A project with nothing approved costs nothing — not an empty heading, not a
    stray rule that an agent reads as a list it was not shown."""
    assert rtr._system_prompt_with_documents(rtr._system_prompt(), "") == rtr._system_prompt()


def test_documents_and_continuity_compose():
    """Both are per-turn facts about the run; neither may erase the other."""
    with_both = rtr._system_prompt_with_continuity(
        "development", documents=_BLOCK,
    )
    assert with_both.startswith(rtr._system_prompt())
    assert f"{rtr._TOOL_PREFIX}development" in with_both[len(rtr._system_prompt()):]
    assert "QuickLink_BRD_v1.docx" in with_both


@pytest.mark.asyncio
async def test_route_sends_the_documents_to_the_model(monkeypatch):
    seen = {}

    async def _fake_ask(text, **kwargs):
        seen["system"] = kwargs.get("system_prompt") or ""
        return rtr.RoutingDecision(agent_id=None, reason="r", direct_reply="there is a BRD")

    monkeypatch.setattr(rtr, "_ask_model", _fake_ask)
    await rtr.route(
        "are there any artifacts in this project?", history=[], run_id="r1",
        tenant_id="t1", project_id="p1", model_id=None, offering_id=None,
        documents=_BLOCK,
    )
    assert seen["system"] != rtr._system_prompt(), (
        "the model was sent the plain prompt — it was never told what the project holds"
    )
    assert "QuickLink_BRD_v1.docx" in seen["system"]


@pytest.mark.asyncio
async def test_route_without_documents_sends_the_plain_prompt(monkeypatch):
    seen = {}

    async def _fake_ask(text, **kwargs):
        seen["system"] = kwargs.get("system_prompt") or ""
        return rtr.RoutingDecision(agent_id=None, reason="r", direct_reply="hello")

    monkeypatch.setattr(rtr, "_ask_model", _fake_ask)
    await rtr.route(
        "hi", history=[], run_id="r1", tenant_id="t1", project_id="p1",
        model_id=None, offering_id=None,
    )
    assert seen["system"] == rtr._system_prompt()


def test_the_documents_prompt_names_no_routing_tool_outside_the_registry():
    """The same guard `test_router.py` keeps on the base prompt, applied to the text
    this feature adds: a `route_to_*` typed into prose is advertised to the model and
    derived from nothing."""
    import re

    added = _documents_text(_BLOCK)
    named = set(re.findall(rf"{rtr._TOOL_PREFIX}(\w+)", added))
    assert named <= set(rtr.REGISTRY), f"unknown routing tools named: {named - set(rtr.REGISTRY)}"
