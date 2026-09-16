"""Standalone Documentation Agent API — interactive doc-generation chat.

WebSocket /ws — streaming chat ("generate the API reference for this branch")
POST /chat/   — REST (non-streaming)
GET  /docset/{session_id}          — the session's generated documents (list + contents)
GET  /docset/{session_id}/doc/{doc_id} — one document's contents

Mirrors the Deployment / Code Review / Security standalone agents. The prepared
target (cloned repo + detected stack + upstream-artifact summary) is bound by
project_id; BYO MCP tools are injected here (the WS path bypasses the pipeline spine).
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from uuid import uuid4
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    UploadFile,
    File,
    WebSocket,
    WebSocketDisconnect,
)
from langchain_core.messages import HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.agent_access import assert_agent_access_for_chat

from agents_orchestrator.documentation_agent.agents.compiler import app as doc_app
from agents_orchestrator.documentation_agent.prompts.doc_prompt import DOC_SYSTEM_PROMPT
from agents_orchestrator.documentation_agent.config.session_state import (
    clear_session, get_prepared, get_session,
)
from config.agent_context import parse_pipeline_context, set_agent_folder
from shared.tools.document_tools import (
    attachment_message_contents,
    attachment_paths_from_context,
)
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.connection_manager import manager
from config.env import AGENT_RUNTIME_MODE
from config.websocket_utils import set_websocket_context
from config.ws_helper import set_session_id, set_user_id
from shared.audit import AuditCallbackHandler
from shared.observability import agent_trace
from shared.audit.service import audit_service
from shared.db import get_db_session, get_db_session_for_tenant
from shared.services.conversation_service import persist_turn
from shared.services.prompt_runtime import prompt_override_scope
from shared.services.skill_runtime import skill_context_scope
from shared.services.standalone_prompt import resolve_agent_turn

logger = logging.getLogger("documentation_agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(_h)

documentation_standalone_router = APIRouter()


def _project_id_from_message(message_data: dict) -> str | None:
    pc = parse_pipeline_context(message_data.get("pipeline_context") or {})
    return (
        message_data.get("project_id")
        or (message_data.get("context") or {}).get("project_id")
        or (pc.get("project_id") if isinstance(pc, dict) else None)
    )


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _doc_context_block(s) -> str:
    if not s.work_dir:
        return ""
    tgt = f"PR #{s.pr_id} (source {s.source_branch})" if s.mode == "pr" else f"branch '{s.source_branch}'"
    langs = ", ".join(s.languages) if s.languages else "to be detected"
    return (
        f"You are documenting {tgt} in repo '{s.repo_name}' (project '{s.ado_project}'). "
        f"The repo is checked out for you. Detected languages: {langs}. "
        f"Upstream platform artifacts: {s.upstream_summary or 'none found for this project'}.\n"
        f"Generate ONLY the deliverable the user asks for, ground every claim in the repo "
        f"or an upstream artifact, and SAVE each finished document with save_document so it "
        f"appears in the user's document list. Do not open a docs PR unless explicitly asked.\n"
    )


async def _load_mcp_tools(tenant_id: str, project_id: str | None) -> list:
    from config.env import MCP_ENABLED
    if not MCP_ENABLED or not tenant_id:
        return []
    try:
        from sqlalchemy import select
        from shared.services import mcp_registry, mcp_client
        from shared.models.orm import Project
        server_ids = None
        if project_id:
            async with get_db_session_for_tenant(tenant_id) as session:
                proj = (await session.execute(select(Project).where(Project.id == uuid.UUID(project_id)))).scalar_one_or_none()
            server_ids = ((proj.mcp_servers if proj else None) or {}).get("documentation") or None
        if not server_ids:
            return []
        configs = await mcp_registry.resolve_server_configs(tenant_id, server_ids, agent_id="documentation")
        return await mcp_client.load_tools(configs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Documentation chat MCP load failed: %s", exc)
        return []


async def _stream(state: dict, config: dict, websocket: WebSocket, session_id: str) -> str:
    """Stream the model's ANSWERS to the client; returns the text sent.

    THE READER SAW THE AGENT THINK OUT LOUD. Every AI chunk without a tool call went out
    as it arrived — including "Let me read the repo first…" from a message that then
    called a tool (a streamed chunk carries no tool call until the message completes).
    `AnswerStream` releases a message's text only once it is known not to call a tool.

    A FAILURE IS RAISED, even after some text: it used to be logged and swallowed once
    anything had streamed, and the turn announced "Documentation updated".
    """
    from langgraph.errors import GraphRecursionError

    from shared.services.answer_stream import AnswerStream  # noqa: PLC0415

    answers = AnswerStream(_extract_text)
    final = ""

    async def _send(text: str) -> None:
        nonlocal final
        if not text:
            return
        final += text
        await manager.send_personal_message(
            json.dumps({"type": "stream_chunk", "content": text, "session_id": session_id}), websocket
        )

    try:
        async for chunk in doc_app.astream(state, stream_mode="messages", config=config):
            await _send(answers.feed(chunk[0] if isinstance(chunk, tuple) else chunk))
        await _send(answers.close())
    except GraphRecursionError:
        await _send(answers.close())
        await _send("\n\n> ⚠️ Step limit reached. Send another message to continue.")
    await manager.send_personal_message(json.dumps({"type": "stream_end", "session_id": session_id}), websocket)
    return final


#: Exceptions raised by a model provider's SDK: their text can echo a BYOK key and does
#: not say who fixes the problem, so they are answered by `friendly_model_error`.
_PROVIDER_MODULES = frozenset({"litellm", "openai", "anthropic", "httpx"})


def _failure_reason(exc: BaseException) -> str:
    from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

    if (type(exc).__module__ or "").split(".")[0] in _PROVIDER_MODULES:
        return friendly_model_error(exc)
    return str(exc) or type(exc).__name__


async def _filed_documents_note(tenant_id: str, project_id: str) -> str:
    """The Documentation documents already filed on this project, for a conversation
    that starts after they were written — so "send the handover for approval" acts on
    the saved document, by its file name, instead of writing it again."""
    if not (tenant_id and project_id):
        return ""
    from sqlalchemy import select  # noqa: PLC0415

    from shared.models.orm import Artifact  # noqa: PLC0415

    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            rows = (await db.execute(
                select(Artifact).where(
                    Artifact.project_id == uuid.UUID(project_id), Artifact.stage == "documentation",
                ).order_by(Artifact.created_at.desc()).limit(15)
            )).scalars().all()
    except Exception:  # noqa: BLE001 — a note, never a failed turn
        logger.warning("filed-documents lookup failed for project %s", project_id, exc_info=True)
        return ""
    status = {"draft": "draft, not yet raised", "pending": "raised, waiting on the approver",
              "approved": "approved", "rejected": "rejected"}
    lines = [
        f"- {(a.blob_path or '').replace(chr(92), '/').rsplit('/', 1)[-1]} "
        f"({status.get(a.approval_status or 'draft', a.approval_status)}"
        + (f", {a.created_at:%d %b %Y %H:%M} UTC" if getattr(a, "created_at", None) else "") + ")"
        for a in rows if a.blob_path and a.artifact_type != "story"
    ]
    if not lines:
        return ""
    return ("\nDocuments this agent has already filed on this project, newest first:\n"
            + "\n".join(lines)
            + "\nA request to send, raise, publish or explain one of these is about that saved "
            "document — act on it by its file name; do not write it again.\n")


@documentation_standalone_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    ticket = websocket.query_params.get("ticket", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(code=4401, reason='{"error": "invalid_or_expired_ticket"}')
        return
    if AGENT_RUNTIME_MODE == "enterprise":
        expected = websocket.query_params.get("tenant_id", "")
        if expected and claims.get("tenant_id", "") != expected:
            await websocket.close(code=4403, reason='{"error": "tenant_mismatch"}')
            return
    set_agent_folder("orchestrator")
    await manager.connect(websocket)
    user_id = claims.get("user_id", "")
    tenant_id = claims.get("tenant_id", "") if claims else ""
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            session_id = message_data.get("session_id", str(uuid4()))
            manager.register_session(websocket, session_id)
            set_websocket_context(manager, session_id)
            set_session_id(session_id)
            set_user_id(user_id)
            if message_data.get("type") in ("user_message_with_files", "user_message"):
                await _process_ws_message(message_data, websocket, user_id, tenant_id)
            elif message_data.get("type") == "session_cleanup":
                clear_session(session_id)
            else:
                await manager.send_personal_message(json.dumps({"type": "echo", "message": data}), websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("Documentation WS error: %s", e)
        manager.disconnect(websocket)


async def _process_ws_message(message_data: dict, websocket: WebSocket, user_id, tenant_id: str = ""):
    session_id = message_data.get("session_id", str(uuid4()))
    try:
        project_id = _project_id_from_message(message_data)
        s = get_session(session_id)
        if tenant_id and not s.tenant_id:
            s.tenant_id = tenant_id
        if project_id and not s.project_id:
            s.project_id = project_id

        # Gate every message, not just the first — a session can be reused across
        # projects on the client side, and the ticket only proves who the caller is,
        # not which project they may act on (nor, on its own, that they're even a
        # member of it — see assert_agent_access_for_chat's docstring).
        _effective_project = project_id or s.project_id
        _effective_tenant = tenant_id or s.tenant_id
        async with get_db_session_for_tenant(_effective_tenant) as _access_db:
            _effective_project = await assert_agent_access_for_chat(
                _access_db, tenant_id=_effective_tenant, project_id=_effective_project,
                user_id=user_id, agent_id="documentation",
            )
        project_id = _effective_project
        s.project_id = _effective_project

        # THE TOOL CONTEXT, and without it several of this agent's tools are inert
        # rather than broken — which is why nobody noticed. `chat_artifacts` and
        # `shared/tools/project_documents` both read the tenant and project from
        # `ws_helper` contextvars, and this handler was the only agent entry point that
        # never set them: the requirements, design and PM APIs all do. So
        # `list_project_documents` answered "no project context in this session" and a
        # generated document was never recorded as an artifact at all. Both failures
        # look like "there is nothing here" rather than an error.
        #
        # `doc_tools._sharepoint_session` already worked around this by reading
        # `s.project_id` off the session state; setting the contextvars fixes it at the
        # source instead, for every tool that expects the same convention.
        from config.ws_helper import set_project_id, set_tenant_id  # noqa: PLC0415
        set_tenant_id(_effective_tenant or None)
        set_project_id(_effective_project or None)

        if not s.target_bound:
            prepared = get_prepared(tenant_id or s.tenant_id, project_id or s.project_id)
            if prepared:
                for k, v in prepared.items():
                    setattr(s, k, v)
                s.target_bound = True
                # A RECORD RESTORED FROM DISK CARRIES NO TOKEN, deliberately — one
                # credential exists, in the credential store, and a copy beside the
                # checkout would outlive its revocation. So it is resolved here, as
                # the person the target was prepared for.
                #
                # An empty result is not fatal: reading a checked-out repository needs
                # no token, and only the push paths do — they say so themselves rather
                # than failing halfway through a git command.
                if not getattr(s, "pat", ""):
                    from shared.services import prepared_targets  # noqa: PLC0415

                    s.pat = await prepared_targets.resolve_secret(
                        tenant_id=s.tenant_id or tenant_id or "",
                        project_id=s.project_id or project_id or "",
                        owner_id=str(getattr(s, "owner_id", "") or ""),
                        provider=str(getattr(s, "provider", "") or ""),
                    )

        incoming = message_data.get("messages", [])
        if incoming:
            user_text = "\n".join((m.get("content", "") if isinstance(m, dict) else str(m)) for m in incoming)
        else:
            user_text = (message_data.get("task_intent") or message_data.get("text")
                         or "Generate the documentation set for this branch.")

        # THE TRANSCRIPT, like every other agent's — the drawer lists this agent's past
        # conversations, and each opened empty because nothing was ever written.
        await persist_turn(
            session_id, "user", user_text, tenant_id=tenant_id or s.tenant_id or None,
            author_id=str(user_id) if user_id else None,
        )

        # THE PAGE'S MODEL PICKER. The graph already read `state["offering_id"]`; this
        # handler never put it there, so every turn ran on whichever connection resolved
        # first — a revoked Azure key, on the day it was noticed.
        _model_state = {
            "tenant_id": tenant_id,
            "project_id": project_id or s.project_id,
            "model_id": message_data.get("model_id"),
            "offering_id": message_data.get("offering_id"),
        }
        first = not s.system_injected
        if first:
            ctx = _doc_context_block(s) + await _filed_documents_note(
                tenant_id or s.tenant_id, project_id or s.project_id,
            )
            content = (ctx + "\n" + user_text) if ctx else user_text
            state = {"messages": [HumanMessage(content=content)], **_model_state}
            s.system_injected = True
        else:
            state = {"messages": [HumanMessage(content=user_text)], **_model_state}

        # THE FILE THE PERSON ATTACHED, read here rather than left as a path. Uploads go
        # through POST /conversations/{id}/attachments and arrive as refs on
        # pipeline_context; `attachment_message_contents` extracts the text and names
        # anything it could not read.
        #
        # WITHOUT THIS the chip appears in the transcript and the agent answers "I don't
        # see any document attached" — the failure that looks like success, and one this
        # platform has already shipped twice on other agents.
        for _content in attachment_message_contents(
            attachment_paths_from_context(message_data.get("pipeline_context"))
        ):
            state["messages"].append(HumanMessage(content=_content))

        audit = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
        _lf_cbs, _lf_meta = await agent_trace(session_id=session_id, tenant_id=tenant_id, user_id=user_id, agent_type="documentation", project_id=_project_id_from_message(message_data))
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 140, "callbacks": [audit, *_lf_cbs], "metadata": _lf_meta}
        await manager.broadcast({"type": "message_received", "session_id": session_id, "message": "Generating…"})

        from shared.tools.mcp_runtime import set_mcp_tools, clear_mcp_tools
        if not s.mcp_loaded:
            s.mcp_tools = await _load_mcp_tools(tenant_id, project_id)
            s.mcp_loaded = True
        set_mcp_tools(s.mcp_tools)
        # Agent-profile prompt layer (design §3.4): the compiler graph node self-injects via
        # get_prompt_override("documentation") or DOC_SYSTEM_PROMPT (verbatim, no MCP note),
        # so resolve the org/workspace/project profile over the BARE constant and set it into
        # the prompt_runtime contextvar for this turn. Fail-soft to base on any miss/error.
        _injected, _skills = await resolve_agent_turn(
            "documentation", DOC_SYSTEM_PROMPT,
            tenant_id or s.tenant_id, project_id or s.project_id,
        )
        try:
            async with prompt_override_scope("documentation", _injected):
                async with skill_context_scope("documentation", _skills):
                    reply = await _stream(state, config, websocket, session_id)
        finally:
            clear_mcp_tools()
        await persist_turn(
            session_id, "agent", reply, tenant_id=tenant_id or s.tenant_id or None,
            author_id="documentation", model=message_data.get("model_id"),
        )

        await manager.broadcast({
            "type": "activity_update",
            "activity": {"id": str(uuid4()), "type": "complete", "session_id": session_id,
                         "message": "Documentation updated", "time": "Just now"},
        })
    except Exception as e:
        logger.error("Documentation WS process error: %s", e)
        reason = _failure_reason(e)
        await manager.send_agent_response("Error Agent", f"An error occurred: {reason}", session_id)
        await persist_turn(
            session_id, "agent", f"An error occurred: {reason}", tenant_id=tenant_id or None,
            author_id="documentation",
        )
        # THE TURN MUST SAY IT IS OVER, and that it failed — the chat BFF ends a run on
        # activity_update{complete} or agent_completed{success: false}; without them a
        # dead model key left the drawer on "Agent is working" with the composer locked.
        await manager.broadcast({
            "type": "agent_completed", "session_id": session_id, "success": False, "error": reason,
        })
        await manager.send_personal_message(
            json.dumps({"type": "stream_end", "session_id": session_id}), websocket
        )
        await manager.broadcast({
            "type": "activity_update",
            "activity": {"id": str(uuid4()), "type": "complete", "session_id": session_id,
                         "message": "Documentation failed", "time": "Just now"},
        })


@documentation_standalone_router.post("/chat/")
async def chat(
    request: Request,
    session_id: str = Form(...),
    user_id: str = Form(...),  # kept for wire compatibility; NOT trusted for identity
    text: str = Form(None),
    pipeline_context: str = Form(None),
    uploaded_files: List[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db_session),
):
    # Identity comes from the verified session, never from the form body — see
    # assert_agent_access_for_chat's docstring and Security's chat() (the reference
    # implementation this mirrors) for why the field above must not be trusted.
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""

    # This route has no `project_id` Form field of its own — the session (bound
    # earlier via the WS path or /docset prepare) already carries it. Resolve it to a
    # real project the caller is actually a member of, and check their role's reach
    # to this agent on THIS project specifically, before trusting it for anything.
    s = get_session(session_id)
    s.project_id = await assert_agent_access_for_chat(
        db, tenant_id=str(real_tenant_id), project_id=s.project_id,
        user_id=str(real_user_id), agent_id="documentation",
    )
    s.tenant_id = s.tenant_id or real_tenant_id

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    set_agent_folder("orchestrator")
    first = not s.system_injected
    user_text = text or "Generate the documentation set for this branch."
    if first:
        ctx = _doc_context_block(s)
        state = {"messages": [HumanMessage(content=(ctx + "\n" + user_text) if ctx else user_text)]}
        s.system_injected = True
    else:
        state = {"messages": [HumanMessage(content=user_text)]}

    # Same as the socket path: refs uploaded to this session, extracted server-side.
    for _content in attachment_message_contents(
        attachment_paths_from_context(parse_pipeline_context(pipeline_context or {}))
    ):
        state["messages"].append(HumanMessage(content=_content))
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 140}
    # Agent-profile prompt layer (design §3.4): resolve over the BARE constant (the compiler
    # node uses it verbatim) using the session's bound tenant/project. Fail-soft.
    _injected, _skills = await resolve_agent_turn(
        "documentation", DOC_SYSTEM_PROMPT, s.tenant_id, s.project_id
    )
    final = ""
    async with prompt_override_scope("documentation", _injected), \
            skill_context_scope("documentation", _skills):
        async for chunk in doc_app.astream(state, stream_mode="messages", config=config):
            msg = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(msg, ToolMessage):
                continue
            if hasattr(msg, "content") and msg.content and not (hasattr(msg, "tool_calls") and msg.tool_calls):
                final += _extract_text(msg.content)
    return {"conversation_id": session_id, "responses": final or "No response generated."}


def _doc_list(s) -> list[dict]:
    return [
        {"id": d["id"], "type": d["type"], "title": d["title"], "filename": d["filename"],
         "format": d.get("format", "md"), "bytes": d.get("bytes", 0)}
        for d in s.generated_docs
    ]


@documentation_standalone_router.get("/docset/{session_id}")
async def get_docset(session_id: str):
    """Return the session's generated documents (list + full contents for the viewer)."""
    s = get_session(session_id)
    return {
        "documents": s.generated_docs,
        "doc_list": _doc_list(s),
        "pr_url": s.pr_url,
        "context": {
            "repo_name": s.repo_name, "ado_project": s.ado_project, "mode": s.mode or "branch",
            "source_branch": s.source_branch, "pr_id": s.pr_id or None, "head_sha": s.head_sha,
            "languages": s.languages, "upstream_summary": s.upstream_summary,
        },
    }


@documentation_standalone_router.get("/docset/{session_id}/doc/{doc_id}")
async def get_doc(session_id: str, doc_id: str):
    s = get_session(session_id)
    for d in s.generated_docs:
        if d["id"] == doc_id:
            return d
    raise HTTPException(status_code=404, detail="document not found")


@documentation_standalone_router.get("/sessions")
async def get_sessions():
    return {"current_session": "documentation"}
