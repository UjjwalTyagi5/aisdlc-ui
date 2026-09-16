"""The Documentation agent files its deliverables like every other agent: a Word document
with a page view, as a DRAFT the chat can raise for approval.

WHAT IT DID. `save_document` wrote a bare `.md`, registered that, and told the model the
document was PENDING — "waiting on an owner" — when it was a draft nobody had raised. The
Documents panel listed a file that opened as nothing; no `file_generated` frame carried
its id, so the page could not open it; the agent had no tool to raise it; the handler
ignored the page's model picker, wrote no transcript, streamed the model's narration, and
left the drawer on "Agent is working" when the model failed.
"""
from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import litellm
import pytest
from langchain_core.messages import AIMessageChunk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.documentation_agent import documentation_standalone_api as api  # noqa: E402
from agents_orchestrator.documentation_agent.config.session_state import clear_session, get_session  # noqa: E402
from agents_orchestrator.documentation_agent.tools import doc_tools  # noqa: E402

HANDOVER_MD = """# Handover — QuickLink

## Scope of this handover
The URL shortener service.

## Known risks and open issues
| Risk | Severity |
|---|---|
| tar 6.2.1 via sqlite3 | High |
"""


class _Manager:
    def __init__(self) -> None:
        self.frames: List[Dict[str, Any]] = []

    async def broadcast(self, payload):
        self.frames.append(payload)

    async def send_personal_message(self, message, _ws):
        self.frames.append(json.loads(message))

    async def send_agent_response(self, agent_name, message, session_id):
        self.frames.append({"type": "agent_response", "agent_name": agent_name, "message": message})

    def types(self):
        return [f.get("type") for f in self.frames]


@pytest.fixture
def doc_session(tmp_path, monkeypatch):
    from config.ws_helper import set_session_id

    set_session_id("doc-save-test")
    s = get_session("doc-save-test")
    s.repo_name, s.ado_project, s.source_branch, s.head_sha, s.mode = "QuickLink", "QuickLink", "main", "082f91e49bf0", "branch"
    s.generated_docs = []
    mgr = _Manager()
    monkeypatch.setattr(doc_tools, "manager", mgr)
    monkeypatch.setattr(doc_tools, "broadcast_log", lambda *_a, **_k: None)
    monkeypatch.setattr(doc_tools, "_output_dir", lambda _s: tmp_path)
    yield s, mgr, tmp_path
    clear_session("doc-save-test")


async def _save(**over):
    args = {"doc_type": "handover", "title": "Handover — QuickLink", "filename": "handover-quicklink.md",
            "markdown_contents": HANDOVER_MD, **over}
    return await doc_tools.save_document.ainvoke(args)


async def test_a_saved_document_is_filed_as_a_word_draft_with_its_page_copy(doc_session):
    s, mgr, out = doc_session
    register = AsyncMock(return_value="art-1")
    with patch("shared.services.chat_artifacts.register_generated_file", register):
        reply = await _save()

    assert (out / "handover-quicklink.md").read_text(encoding="utf-8") == HANDOVER_MD
    assert (out / "handover-quicklink.docx").stat().st_size > 0
    # The WORD file is the document; its markdown sibling is what the page renders.
    name, path, _url = register.await_args.args
    assert name == "handover-quicklink.docx" and path.endswith("handover-quicklink.docx")
    assert register.await_args.kwargs["stage"] == "documentation"
    [frame] = [f for f in mgr.frames if f.get("type") == "file_generated"]
    assert frame["artifact_id"] == "art-1" and frame["filename"] == "handover-quicklink.docx"
    assert "DRAFT" in reply and "PENDING" not in reply
    assert 'raise_document_for_approval with "handover-quicklink.docx"' in reply
    assert s.generated_docs[-1]["artifact_id"] == "art-1"


async def test_a_document_that_could_not_be_recorded_says_so(doc_session):
    _s, mgr, _out = doc_session
    with patch("shared.services.chat_artifacts.register_generated_file", AsyncMock(return_value=None)):
        reply = await _save()
    assert "could NOT be recorded" in reply and "DRAFT" not in reply
    assert "file_generated" not in mgr.types()


async def test_a_word_file_that_cannot_be_written_is_an_error_not_a_saved_document(doc_session, monkeypatch):
    from agents_orchestrator.documentation_agent import documentation_document

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(documentation_document, "write_documentation_docx", boom)
    register = AsyncMock(return_value="art-1")
    with patch("shared.services.chat_artifacts.register_generated_file", register):
        reply = await _save()
    assert reply.startswith("ERROR") and "disk full" in reply and "NOT in the project's Documents" in reply
    register.assert_not_awaited()


async def test_an_approved_word_document_publishes_its_markdown_to_confluence(doc_session):
    _s, _mgr, _out = doc_session
    with patch("shared.services.chat_artifacts.register_generated_file", AsyncMock(return_value="art-1")):
        await _save()
    connector = AsyncMock()
    connector.write_adapter = AsyncMock(return_value={"id": "p1", "url": "https://wiki/p1"})
    with patch.object(doc_tools, "_confluence_session", AsyncMock(return_value=((connector, "QL"), ""))), \
         patch("shared.tools.sharepoint_artifacts._approved_documents",
               AsyncMock(return_value=[{"name": "handover-quicklink.docx"}])):
        out = await doc_tools.publish_to_confluence.ainvoke({})
    assert out.startswith("Published 1 document(s)"), out
    assert connector.write_adapter.await_args.kwargs["title"] == "Handover — QuickLink"


def test_the_word_document_does_not_repeat_the_title_as_its_first_heading(tmp_path):
    from docx import Document

    from agents_orchestrator.documentation_agent.documentation_document import write_documentation_docx

    md = tmp_path / "handover.md"
    md.write_text(HANDOVER_MD, encoding="utf-8")
    path = write_documentation_docx(HANDOVER_MD, str(md), doc_type="handover", title="", repo="QuickLink")
    texts = [p.text for p in Document(path).paragraphs if p.text.strip()]
    assert sum(t == "Handover — QuickLink" for t in texts) <= 1
    assert any("Scope of this handover" in t for t in texts)


def test_the_agent_can_raise_its_documents_and_is_told_they_are_drafts():
    from agents_orchestrator.documentation_agent.agents import compiler
    from agents_orchestrator.documentation_agent.prompts.doc_prompt import DOC_SYSTEM_PROMPT

    assert "raise_document_for_approval" in {t.name for t in compiler._tools}
    assert "as a DRAFT" in DOC_SYSTEM_PROMPT and "as PENDING" not in DOC_SYSTEM_PROMPT


# ── the turn ──────────────────────────────────────────────────────────────────


def _auth_error():
    return litellm.exceptions.AuthenticationError(
        message="AzureException AuthenticationError - invalid subscription key", llm_provider="azure", model="gpt-5-mini",
    )


@pytest.fixture
def turn(monkeypatch):
    mgr = _Manager()
    saved, states = [], []
    monkeypatch.setattr(api, "manager", mgr)

    @asynccontextmanager
    async def _db(_tenant):
        yield None

    async def _persist(session_id, role, content, **kwargs):
        saved.append((role, content))

    monkeypatch.setattr(api, "get_db_session_for_tenant", _db)
    monkeypatch.setattr(api, "assert_agent_access_for_chat", AsyncMock(return_value="p1"))
    monkeypatch.setattr(api, "get_prepared", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "agent_trace", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(api, "_load_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "resolve_agent_turn", AsyncMock(return_value=("prompt", [])))
    monkeypatch.setattr(api, "_filed_documents_note", AsyncMock(return_value="\nFILED: handover-quicklink.docx (draft)\n"))
    monkeypatch.setattr(api, "persist_turn", _persist)

    async def _run(chunks=(), boom=None, offering="off-grok"):
        class _App:
            @staticmethod
            async def astream(state, **_kwargs):
                states.append(state)
                for text in chunks:
                    yield (AIMessageChunk(content=text, id="m1"), {})
                if boom is not None:
                    raise boom

        monkeypatch.setattr(api, "doc_app", _App)
        clear_session("d1")
        await api._process_ws_message(
            {"type": "user_message_with_files", "session_id": "d1", "project_id": "p1",
             "task_intent": "send the handover for approval", "offering_id": offering},
            websocket=object(), user_id="u1", tenant_id="t1",
        )
        return mgr, saved, states

    yield _run
    clear_session("d1")


def _failed(frames):
    return any(f.get("type") == "agent_completed" and f.get("success") is False for f in frames)


def _complete(frames):
    return [f["activity"]["message"] for f in frames
            if f.get("type") == "activity_update" and f.get("activity", {}).get("type") == "complete"]


async def test_the_pages_model_and_the_filed_documents_reach_the_agent(turn):
    _mgr, _saved, states = await turn(chunks=["Raised."])
    assert states[0]["offering_id"] == "off-grok"
    assert "FILED: handover-quicklink.docx" in states[0]["messages"][0].content


async def test_a_successful_turn_is_written_to_the_transcript_and_ends_complete(turn):
    mgr, saved, _states = await turn(chunks=["Raised."])
    assert saved == [("user", "send the handover for approval"), ("agent", "Raised.")]
    assert not _failed(mgr.frames) and _complete(mgr.frames) == ["Documentation updated"]


async def test_a_model_failure_ends_the_run_as_failed_with_who_fixes_it(turn):
    mgr, saved, _states = await turn(chunks=["Let me look…"], boom=_auth_error())
    assert _failed(mgr.frames) and "stream_end" in mgr.types()
    assert _complete(mgr.frames) == ["Documentation failed"]
    [said] = [f["message"] for f in mgr.frames if f.get("type") == "agent_response"]
    assert "rejected the configured credential" in said and "subscription key" not in said
    assert saved[-1][0] == "agent" and "rejected the configured credential" in saved[-1][1]


async def test_the_prepared_workspace_survives_a_refresh_without_its_credential(monkeypatch):
    from types import SimpleNamespace

    from agents_orchestrator.documentation_agent.config import session_state
    from shared.routers.documentation_workspace import get_prepared_docs

    monkeypatch.setattr(session_state, "get_prepared", lambda *_a: {
        "work_dir": "C:/w", "pat": "SECRET", "repo_url": "https://SECRET@dev.azure.com/x", "provider": "azure_devops",
        "mode": "branch", "ado_project": "QuickLink", "repo_name": "QuickLink", "source_branch": "main",
        "pr_id": "", "head_sha": "082f91e", "languages": ["JavaScript"], "upstream_summary": "BRD approved",
    })
    out = await get_prepared_docs("p1", SimpleNamespace(state=SimpleNamespace(tenant_id="t1")))
    assert out["status"] == "ready" and out["branch"] == "main" and out["languages"] == ["JavaScript"]
    assert "SECRET" not in json.dumps(out)
    monkeypatch.setattr(session_state, "get_prepared", lambda *_a: None)
    assert await get_prepared_docs("p1", SimpleNamespace(state=SimpleNamespace(tenant_id="t1"))) == {"status": None}


# ── upstream results are about THIS repository ────────────────────────────────


def test_a_result_for_another_repository_is_recognised():
    from agents_orchestrator.documentation_agent.tools.doc_tools import _other_repo

    other = {"context": {"repo_name": "Company", "source_branch": "feature/duplicate-table-blue", "head_sha": "37fb6612af67"}}
    assert _other_repo(other, "QuickLink") == "repository 'Company', branch feature/duplicate-table-blue, commit 37fb661"
    assert _other_repo({"context": {"repo_name": "quicklink"}}, "QuickLink") is None
    assert _other_repo({"summary": "no context"}, "QuickLink") is None


async def test_the_upstream_read_drops_a_code_review_of_another_repository(doc_session, monkeypatch):
    """LIVE: the project's newest code review was of a .NET repo reviewed minutes earlier,
    and the handover said the QuickLink branch "implements RadAuthPortal .NET"."""
    from shared.services import artifact_consumption
    from shared.services.artifact_versions import UpstreamRead

    s, _mgr, _out = doc_session
    s.tenant_id, s.project_id = "t1", "11111111-1111-1111-1111-111111111111"
    payloads = {
        "code_review": {"context": {"repo_name": "Company", "source_branch": "feature/duplicate-table-blue", "head_sha": "37fb661"},
                        "summary": "a complete .NET 8 ASP.NET Core MVC application"},
        "security": {"context": {"repo_name": "QuickLink", "branch": "main", "head_sha": "082f91e"}, "summary": "tar via sqlite3"},
    }

    async def fake_read(*, stage, **_kw):
        return UpstreamRead(stage=stage, payload=payloads.get(stage), unenforced=True)

    monkeypatch.setattr(artifact_consumption, "read_upstream_for_agent", fake_read)
    out = json.loads(await doc_tools.read_upstream_artifacts.ainvoke({}))
    assert out["code_review"] is None
    assert "repository 'Company'" in out["code_review_status"] and "not the repository being documented (QuickLink)" in out["code_review_status"]
    assert out["security"]["summary"] == "tar via sqlite3"


async def test_the_graph_resolves_the_pages_model_before_calling_it(monkeypatch):
    """Every Documentation turn failed "No BYOK model resolved for this run": the graph
    READ a resolved model that nothing had resolved."""
    from langchain_core.messages import AIMessage, HumanMessage

    from agents_orchestrator.documentation_agent.agents import compiler
    from shared.services import model_resolver

    calls = []

    async def fake_resolve(tenant_id, model_id, **kw):
        calls.append((tenant_id, model_id, kw))
        return "resolved-grok"

    class _Model:
        async def ainvoke(self, _messages):
            return AIMessage(content="ok")

    monkeypatch.setattr(model_resolver, "resolve_model_for_run", fake_resolve)
    monkeypatch.setattr(compiler, "_resolve_model", lambda _state: _Model())
    out = await compiler.agent_node({"messages": [HumanMessage(content="hi")], "tenant_id": "t1",
                                     "project_id": "p1", "model_id": None, "offering_id": "off-grok"})
    assert calls == [("t1", None, {"offering_id": "off-grok", "project_id": "p1"})]
    assert out["resolved_model"] == "resolved-grok"
