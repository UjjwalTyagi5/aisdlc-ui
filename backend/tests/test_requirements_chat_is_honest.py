"""The Requirements chat says what happened — no fabricated documents, no silent retries.

THE TURN THAT PROMPTED THIS. "Can you create a BRD based on this transcript", with the
transcript attached. The agent called generate_brd(["QuickLink_Discovery_Call_transcript.md"])
— the attachment's name, the only handle it had — and the tool refused it ("has not yet
been uploaded"), because it accepted only `.ref` files from upload_file. upload_file then
refused the name too ("local file not found"), generate_brd refused again, and no document
was ever written. The reply opened with those three errors glued together — the stream
forwarded ToolMessage text as if the agent had said it — and then announced "BRD Generated
Successfully" with a link to example.com/download/QuickLink_BRD.docx.

Each test below pins one cause:
- tool output never reaches the user as the agent's words;
- a streaming failure is reported, not answered by re-running the whole graph;
- an attachment is found by the name the model is shown;
- generating a BRD writes the designed Word document and returns its real link, and a
  file that could not be published is an error, not "Saved";
- a link in the reply that no tool produced is called out.
"""
from __future__ import annotations

import inspect
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_approved_documents(monkeypatch):
    """The resolver also looks up the project's approved documents; these tests are about
    attachments, so the project has none — and no test here reaches the database."""
    from agents_orchestrator.requirements_agent.agents import planning

    monkeypatch.setattr(planning, "_approved_documents", AsyncMock(return_value=[]))

BRD = """## Executive Summary
QuickLink shortens internal URLs.

## Project Objectives
- Track clicks per campaign

## Project Scope
In scope: shortening.
"""


# ── the stream ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_output_is_never_streamed_as_the_agents_reply(monkeypatch):
    from agents_orchestrator.requirements_agent import requirements_agent_api as api

    async def _astream(state, stream_mode, config):
        yield (ToolMessage(content="Error: file x.md has not yet been uploaded", tool_call_id="t1"), {})
        # Real streams give each message's chunks its id — which is how the answer is told
        # apart from the message that called the tool (see shared/services/answer_stream).
        yield (AIMessageChunk(content="", id="m1", tool_calls=[{"name": "generate_brd", "args": {}, "id": "t2"}]), {})
        yield (AIMessageChunk(content="The BRD could not be generated.", id="m2"), {})

    sent = []

    class _Manager:
        async def send_personal_message(self, message, websocket):
            sent.append(json.loads(message)["content"])

    monkeypatch.setattr(api.planning_app, "astream", _astream)
    monkeypatch.setattr(api, "manager", _Manager())

    out = await api._stream_agent_response({}, {}, websocket=None, session_id="s1")

    assert sent == ["The BRD could not be generated."]
    assert out == "The BRD could not be generated."


@pytest.mark.asyncio
async def test_a_streaming_failure_is_raised_to_the_turn_not_swallowed(monkeypatch):
    from agents_orchestrator.requirements_agent import requirements_agent_api as api

    async def _astream(state, stream_mode, config):
        raise RuntimeError("AuthenticationError: invalid subscription key")
        yield  # pragma: no cover

    monkeypatch.setattr(api.planning_app, "astream", _astream)
    with pytest.raises(RuntimeError):
        await api._stream_agent_response({}, {}, websocket=None, session_id="s1")


def test_the_turn_never_reruns_the_graph_when_the_stream_is_empty():
    """The old path: streaming swallowed its exception, returned "", and the handler ran
    `planning_app.stream(...)` — the whole graph again, every tool call again — so a dead
    API key failed twice and a board write would have happened twice."""
    from agents_orchestrator.requirements_agent import requirements_agent_api as api

    src = inspect.getsource(api)
    assert "planning_app.stream(" not in src


# ── attachments, by the name the model is shown ──────────────────────────────


@pytest.mark.asyncio
async def test_an_attachment_is_found_by_the_name_in_the_conversation(tmp_path):
    from agents_orchestrator.requirements_agent.agents import planning
    from config.ws_helper import set_session_id

    transcript = tmp_path / "QuickLink_Discovery_Call_transcript.md"
    transcript.write_text("PM: we need click tracking.", encoding="utf-8")
    set_session_id("sess-attach")
    planning.register_source_files("sess-attach", [str(transcript)])

    paths, err = await planning._resolve_source_files(["QuickLink_Discovery_Call_transcript.md"])

    assert err is None
    assert paths == [str(transcript)]


@pytest.mark.asyncio
async def test_an_unknown_name_is_an_error_that_names_what_is_attached(tmp_path):
    from agents_orchestrator.requirements_agent.agents import planning
    from config.ws_helper import set_session_id

    notes = tmp_path / "notes.md"
    notes.write_text("x", encoding="utf-8")
    set_session_id("sess-unknown")
    planning.register_source_files("sess-unknown", [str(notes)])

    paths, err = await planning._resolve_source_files(["transcript.md"])

    assert paths == []
    assert err.startswith("Error:")
    assert "notes.md" in err and "Nothing was generated" in err


@pytest.mark.asyncio
async def test_a_path_that_is_not_an_attachment_is_not_read(tmp_path):
    """The model must not be able to read any file on the server by naming its path."""
    from agents_orchestrator.requirements_agent.agents import planning
    from config.ws_helper import set_session_id

    secret = tmp_path / "secrets.env"
    secret.write_text("KEY=1", encoding="utf-8")
    set_session_id("sess-noread")

    paths, err = await planning._resolve_source_files([str(secret)])

    assert paths == [] and err and err.startswith("Error:")


# ── generating writes the document, or says it did not ───────────────────────


def _bind_turn(tmp_path, monkeypatch, session="sess-brd"):
    from agents_orchestrator.requirements_agent.agents import planning
    from config.ws_helper import set_session_id, set_user_id

    set_session_id(session)
    set_user_id("user-1")
    monkeypatch.setattr(planning.esett, "FILES", str(tmp_path), raising=False)
    monkeypatch.setattr(planning, "_project_display_name", AsyncMock(return_value="QuickLink"))
    monkeypatch.setattr(planning, "_openai_generate", lambda prompt, file_paths=None: BRD)
    return planning


@pytest.mark.asyncio
async def test_generate_brd_writes_the_word_document_and_returns_its_real_link(tmp_path, monkeypatch):
    planning = _bind_turn(tmp_path, monkeypatch)
    transcript = tmp_path / "transcript.md"
    transcript.write_text("PM: we need click tracking.", encoding="utf-8")
    planning.register_source_files("sess-brd", [str(transcript)])
    url = "http://127.0.0.1:8004/generated/user-1/requirements_agent/sess-brd/output/QuickLink_BRD.docx"
    broadcast = AsyncMock(return_value=url)
    monkeypatch.setattr(planning, "broadcast_file_generated", broadcast)

    out = await planning.generate_brd.ainvoke({"file_names": ["transcript.md"], "custom_prompt": ""})

    out_dir = tmp_path / "user-1" / "requirements_agent" / "sess-brd" / "output"
    assert (out_dir / "QuickLink_BRD.docx").is_file()
    assert (out_dir / "QuickLink_BRD.md").is_file(), "the page renders the markdown beside the Word file"
    assert url in out
    assert "Executive Summary" in out, "the model still has the content to answer follow-ups"
    broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_second_brd_does_not_overwrite_the_first(tmp_path, monkeypatch):
    planning = _bind_turn(tmp_path, monkeypatch, session="sess-two")
    monkeypatch.setattr(planning, "broadcast_file_generated", AsyncMock(side_effect=lambda s, name, p: f"http://x/{name}"))

    first = await planning.generate_brd.ainvoke({"file_names": [], "custom_prompt": "a link shortener"})
    second = await planning.generate_brd.ainvoke({"file_names": [], "custom_prompt": "a link shortener"})

    assert "QuickLink_BRD.docx" in first
    assert "QuickLink_BRD_v2.docx" in second


@pytest.mark.asyncio
async def test_a_document_that_could_not_be_published_is_an_error_not_a_success(tmp_path, monkeypatch):
    planning = _bind_turn(tmp_path, monkeypatch, session="sess-nopub")
    monkeypatch.setattr(planning, "broadcast_file_generated", AsyncMock(return_value=""))

    out = await planning.generate_brd.ainvoke({"file_names": [], "custom_prompt": "a link shortener"})

    assert out.startswith("Error:")
    assert "http" not in out


@pytest.mark.asyncio
async def test_generate_brd_with_an_unattached_file_generates_nothing(tmp_path, monkeypatch):
    planning = _bind_turn(tmp_path, monkeypatch, session="sess-none")
    broadcast = AsyncMock(return_value="http://x")
    monkeypatch.setattr(planning, "broadcast_file_generated", broadcast)

    out = await planning.generate_brd.ainvoke({"file_names": ["transcript.md"], "custom_prompt": ""})

    assert out.startswith("Error:") and "Nothing was generated" in out
    broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_document_word_uses_the_designed_canvas(tmp_path, monkeypatch):
    from docx import Document

    planning = _bind_turn(tmp_path, monkeypatch, session="sess-export")
    monkeypatch.setattr(planning, "broadcast_file_generated", AsyncMock(return_value="http://x/QuickLink_BRD.docx"))

    out = await planning.export_document.ainvoke({"content": BRD, "filename": "QuickLink_BRD.docx"})

    path = tmp_path / "user-1" / "requirements_agent" / "sess-export" / "output" / "QuickLink_BRD.docx"
    band = "\n".join(c.text for t in Document(str(path)).tables for r in t.rows for c in r.cells)
    assert "BUSINESS REQUIREMENTS DOCUMENT" in band
    assert path.with_suffix(".md").is_file()
    assert "http://x/QuickLink_BRD.docx" in out


def test_the_export_message_names_no_tool_that_does_not_exist():
    from shared.tools.doc_export import export_result_message

    msg = export_result_message("a.docx", "http://x/a.docx")
    assert "save_to_project_artifacts" not in msg
    assert "http://x/a.docx" in msg


# ── a link no tool produced ──────────────────────────────────────────────────


def test_a_link_that_no_tool_or_user_produced_is_found():
    from shared.services.reply_integrity import unsupported_links

    reply = (
        "Download: [QuickLink_BRD.docx](https://example.com/download/QuickLink_BRD.docx). "
        "Board item: https://dev.azure.com/org/p/_workitems/edit/12."
    )
    sources = [
        "Created work item 12: https://dev.azure.com/org/p/_workitems/edit/12",
        "please see https://intranet/brief",
    ]
    assert unsupported_links(reply, sources) == ["https://example.com/download/QuickLink_BRD.docx"]


def test_a_reply_with_only_real_links_is_clean():
    from shared.services.reply_integrity import unsupported_links

    url = "http://127.0.0.1:8004/generated/u/requirements_agent/s/output/QuickLink_BRD.docx"
    assert unsupported_links(f"Here it is: {url}.", [f"Saved 'QuickLink_BRD.docx'. Download it here: {url}"]) == []


def test_the_turn_checks_the_reply_for_links_no_tool_produced():
    from agents_orchestrator.requirements_agent import requirements_agent_api as api

    assert "unsupported_links(" in inspect.getsource(api)


# ── the prompt names only tools that exist ───────────────────────────────────


def test_the_prompt_names_only_tools_the_agent_has():
    from agents_orchestrator.requirements_agent.agents import planning

    names = {t.name for t in planning.tools}
    prompt = planning.INGESTION_SYS_MESSAGE
    for ghost in ("generate_brd_document", "generate_user_stories_document",
                  "generate_risk_register_document", "read_uploaded_file"):
        assert ghost not in names, f"{ghost} exists now — update this test"
        assert ghost not in prompt, f"the prompt tells the model to call {ghost}, which it does not have"
    for real in ("generate_brd", "generate_pdd", "generate_risk_register", "export_document"):
        assert real in names and real in prompt


@pytest.mark.asyncio
async def test_what_the_model_says_before_reading_an_attachment_is_not_the_reply(monkeypatch):
    """Live: with a document attached, the reply opened "the document has not been uploaded
    yet…" — text from the message that went on to read it — and only then answered."""
    from agents_orchestrator.requirements_agent import requirements_agent_api as api

    async def _astream(state, stream_mode, config):
        yield (AIMessageChunk(content="The document has not been uploaded yet; ", id="m1"), {})
        yield (AIMessageChunk(content="let me read it.", id="m1", tool_call_chunks=[
            {"name": "read_document", "args": "{}", "id": "t1", "index": 0}]), {})
        yield (ToolMessage(content="# Discovery call transcript", tool_call_id="t1"), {})
        yield (AIMessageChunk(content="BRD generated: ", id="m2"), {})
        yield (AIMessageChunk(content="QuickLink_BRD.docx", id="m2"), {})

    sent = []

    class _Manager:
        async def send_personal_message(self, message, websocket):
            sent.append(json.loads(message)["content"])

    monkeypatch.setattr(api.planning_app, "astream", _astream)
    monkeypatch.setattr(api, "manager", _Manager())

    out = await api._stream_agent_response({}, {}, websocket=None, session_id="s1")

    assert out == "BRD generated: QuickLink_BRD.docx"
    assert not any("not been uploaded" in s for s in sent)
