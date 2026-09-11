"""The shared Track 3 agent graph: the agent→tools→agent loop, capping, failures.

The model is faked at the one seam the graph owns (`_chat_client`) so the loop, the
tool dispatch and the output cap are exercised for real without a provider.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from agents_orchestrator.modernization_common import graph as graph_module
from agents_orchestrator.modernization_common.graph import (
    TOOL_OUTPUT_CAP,
    build_tool_agent_graph,
    cap_tool_output,
)


@tool
async def big_output() -> str:
    """Return far more text than the cap allows."""
    return "x" * (TOOL_OUTPUT_CAP + 8_000)


@tool
async def explode() -> str:
    """Always fails."""
    raise RuntimeError("boom")


class _ScriptedModel:
    """Answers with the scripted AIMessages in order, recording what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list] = []
        self.bound: list[str] = []

    def bind_tools(self, tools):
        self.bound = [t.name for t in tools]
        return self

    async def ainvoke(self, messages, **_kwargs):
        self.seen.append(list(messages))
        return self.replies.pop(0)


@pytest.fixture
def scripted(monkeypatch):
    def install(replies):
        model = _ScriptedModel(replies)
        monkeypatch.setattr(graph_module, "_chat_client", lambda resolved, max_tokens: model)
        monkeypatch.setattr(
            "shared.services.model_resolver.get_resolved_model",
            lambda: SimpleNamespace(alias="test", model="m", extra_kwargs={},
                                    max_cost_per_call_usd=None),
        )
        return model
    return install


def _call(name: str, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": call_id}])


def test_cap_tool_output_says_it_truncated():
    capped = cap_tool_output("y" * (TOOL_OUTPUT_CAP + 10))
    assert capped.startswith("y" * TOOL_OUTPUT_CAP)
    assert "truncated" in capped
    assert cap_tool_output("short") == "short"


async def test_loop_runs_the_tool_and_caps_its_output(scripted):
    model = scripted([_call("big_output", "c1"), AIMessage(content="done")])
    app = build_tool_agent_graph(agent_type="discovery", tools=[big_output], checkpoint_name="t1")
    final = await app.ainvoke(
        {"messages": [HumanMessage(content="go")], "tenant_id": "t"},
        {"configurable": {"thread_id": "cap"}},
    )
    tool_messages = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert len(tool_messages[0].content) < TOOL_OUTPUT_CAP + 200
    assert "truncated" in tool_messages[0].content
    assert final["messages"][-1].content == "done"
    assert "big_output" in model.bound


async def test_a_failing_tool_answers_its_call_instead_of_crashing(scripted):
    scripted([_call("explode", "c9"), AIMessage(content="recovered")])
    app = build_tool_agent_graph(agent_type="discovery", tools=[explode], checkpoint_name="t2")
    final = await app.ainvoke(
        {"messages": [HumanMessage(content="go")], "tenant_id": "t"},
        {"configurable": {"thread_id": "fail"}},
    )
    answer = next(m for m in final["messages"] if isinstance(m, ToolMessage))
    assert answer.tool_call_id == "c9"
    assert "boom" in answer.content
    assert final["messages"][-1].content == "recovered"


async def test_no_model_configured_is_a_reply_not_an_exception(monkeypatch):
    from shared.services.model_resolver import NoModelConfiguredError

    async def refuse(*_a, **_k):
        raise NoModelConfiguredError("none")

    monkeypatch.setattr("shared.services.model_resolver.get_resolved_model", lambda: None)
    monkeypatch.setattr("shared.services.model_resolver.resolve_model_for_run", refuse)
    app = build_tool_agent_graph(agent_type="discovery", tools=[big_output], checkpoint_name="t3")
    final = await app.ainvoke(
        {"messages": [HumanMessage(content="go")], "tenant_id": "t", "project_id": "p"},
        {"configurable": {"thread_id": "nomodel"}},
    )
    assert "No usable model" in final["messages"][-1].content
