"""The Testing chat does what the Requirements chat does with a finished document:
raise it for approval, and publish an approved one to Confluence or SharePoint.

The testing documents were already filed as drafts in the project's Documents
(project_record.record_outputs). What was missing is the chat:
- "send the test cases for approval" never reached the node that binds tools, and no
  tool could do it there anyway;
- Confluence/SharePoint are bound only when the project granted them to the Testing
  stage, and when they were not, the model answered anyway — with no tool to publish;
- a model that could not call tools got a plain, tool-less answer: the shape that turns
  into "published successfully" with nothing published.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

pytestmark = pytest.mark.unit


def _ingest():
    from agents_orchestrator.testing_agent.Nodes import ingest_input

    return ingest_input


# ── routing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "send the test cases for approval",
    "Can you submit test_cases.docx for approval?",
    "raise the QA report for approval please",
    "upload the approved test cases to confluence",
    "publish qa_report.html to SharePoint",
])
def test_approval_and_publishing_requests_reach_the_tool_path(prompt):
    assert _ingest()._asks_for_a_tool(prompt) is True


@pytest.mark.parametrize("prompt", ["yes, approve", "approve and run the unit tests", "hi"])
def test_the_staged_run_approval_is_not_mistaken_for_a_document_request(prompt):
    assert _ingest()._asks_for_a_tool(prompt) is False


# ── the tool loop ────────────────────────────────────────────────────────────


@tool
async def publish_approved_to_confluence(space: str = "", filename: str = "") -> str:
    """fake"""
    return f"Published {filename} to {space}"


class _Model:
    """Returns the scripted replies in order; records the tools it was bound to."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.bound: list[str] = []
        self.seen: list = []

    def bind_tools(self, tools):
        self.bound = [t.name for t in tools]
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        return self.replies.pop(0)


async def _answer(prompt, *, model, connector_tools=()):
    ing = _ingest()
    with patch.object(ing, "get_llm", lambda: model), \
         patch("shared.tools.stage_tools.tools_for_stage", AsyncMock(return_value=list(connector_tools))), \
         patch("shared.tools.mcp_runtime.get_mcp_tools", lambda: []):
        return await ing._answer_with_optional_mcp(prompt, [], "(no prior test run)")


@pytest.mark.asyncio
async def test_the_chat_binds_approval_documents_and_granted_connector_tools():
    model = _Model([AIMessage(content="ok")])
    await _answer("upload test_cases.docx to confluence", model=model,
                  connector_tools=[publish_approved_to_confluence])
    assert {"raise_document_for_approval", "list_project_documents", "read_document",
            "publish_approved_to_confluence"} <= set(model.bound)


@pytest.mark.asyncio
async def test_the_chat_raises_a_document_with_the_shared_tool():
    model = _Model([
        AIMessage(content="", tool_calls=[{"name": "raise_document_for_approval",
                                           "args": {"filename": "test_cases.docx"}, "id": "c1"}]),
        AIMessage(content="Raised test_cases.docx for approval; a project admin decides."),
    ])
    with patch("shared.tools.document_approval.raise_for_approval",
               AsyncMock(return_value="Raised 'test_cases.docx' for approval.")) as raised:
        out = await _answer("send test_cases.docx for approval", model=model)

    raised.assert_awaited_once_with("test_cases.docx", stage="testing")
    assert out.startswith("Raised test_cases.docx")
    tool_result = model.seen[1][-1]
    assert "Raised 'test_cases.docx' for approval." in tool_result.content


@pytest.mark.asyncio
async def test_an_ungranted_connector_is_named_not_improvised():
    model = _Model([AIMessage(content="Published successfully!")])
    out = await _answer("upload the approved test cases to confluence", model=model, connector_tools=[])

    assert "Confluence" in out and "Testing stage" in out and "Settings → Tools per stage" in out
    assert "Nothing was published" in out
    assert model.seen == [], "the model is not asked to answer a request it has no tool for"


@pytest.mark.asyncio
async def test_a_model_that_cannot_use_tools_is_said_so_not_answered_without_them():
    class _NoTools(_Model):
        def bind_tools(self, tools):
            raise NotImplementedError("tool calling unsupported")

    model = _NoTools([AIMessage(content="Published successfully!")])
    out = await _answer("send test_cases.docx for approval", model=model)

    assert "cannot use tools" in out
    assert model.seen == []


@pytest.mark.asyncio
async def test_publish_space_then_publish_fits_in_the_tool_budget():
    """list spaces → create space → list documents → publish → answer is five model
    calls; the old budget of four ended it with "please rephrase" before publishing."""
    steps = [
        AIMessage(content="", tool_calls=[{"name": "list_project_documents", "args": {}, "id": f"c{i}"}])
        for i in range(4)
    ] + [AIMessage(content="Published test_cases.docx.")]
    model = _Model(steps)
    with patch("shared.tools.project_documents.make_document_tools"):
        out = await _answer("publish test_cases.docx to confluence", model=model,
                            connector_tools=[publish_approved_to_confluence])
    assert out == "Published test_cases.docx."


def test_the_testing_chat_prompt_says_it_raises_and_never_approves():
    import inspect

    src = inspect.getsource(_ingest()._answer_with_optional_mcp)
    assert "raise_document_for_approval" in src
    assert "never approve" in src.lower()
