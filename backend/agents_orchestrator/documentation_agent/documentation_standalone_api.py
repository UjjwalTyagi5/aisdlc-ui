"""Standalone Documentation Agent API — interactive doc-generation chat.

WebSocket /ws — streaming chat ("generate the API reference for this branch")
POST /chat/   — REST (non-streaming)
GET  /docset/{session_id}          — the session's generated documents (list + contents)
GET  /docset/{session_id}/doc/{doc_id} — one document's contents

Mirrors the Deployment / Code Review / Security standalone agents. The prepared
target (cloned repo + detected stack + upstream-artifact summary) is bound by
project_id; BYO MCP tools are injected here (the WS path bypasses the Temporal spine).
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from uuid import uuid4
from typing import List

from fastapi import APIRouter, Form, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException
from langchain_core.messages import HumanMessage, ToolMessage

from agents_orchestrator.documentation_agent.agents.compiler import app as doc_app
from agents_orchestrator.documentation_agent.prompts.doc_prompt import DOC_SYSTEM_PROMPT
from agents_orchestrator.documentation_agent.config.session_state import (
    clear_session, get_prepared, get_session,
)
from config.agent_context import parse_pipeline_context, set_agent_folder
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.connection_manager import manager
from config.env import AGENT_RUNTIME_MODE
from config.websocket_utils import set_websocket_context
from config.ws_helper import set_session_id, set_user_id
from shared.audit import AuditCallbackHandler
from shared.observability import langfuse_langchain_extras
from shared.audit.service import audit_service
from shared.db import get_db_session_for_tenant
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
    from langgraph.errors import GraphRecursionError
    final, got = "", False
    try:
        async for chunk in doc_app.astream(state, stream_mode="messages", config=config):
            msg = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(msg, ToolMessage) or not hasattr(msg, "content"):
                continue
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                continue
            text = _extract_text(msg.content)
            if not text:
                continue
            final += text
            got = True
            await manager.send_personal_message(
                json.dumps({"type": "stream_chunk", "content": text, "session_id": session_id}), websocket
            )
    except GraphRecursionError:
        await manager.send_personal_message(
            json.dumps({"type": "stream_chunk", "content": "\n\n> ⚠️ Step limit reached. Send another message to continue.", "session_id": session_id}), websocket
        )
    except Exception as e:
        logger.error("Documentation stream error: %s", e)
        if not got:
            raise
    await manager.send_personal_message(json.dumps({"type": "stream_end", "session_id": session_id}), websocket)
    return final


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
        if not s.target_bound:
            prepared = get_prepared(tenant_id or s.tenant_id, project_id or s.project_id)
            if prepared:
                for k, v in prepared.items():
                    setattr(s, k, v)
                s.target_bound = True

        incoming = message_data.get("messages", [])
        if incoming:
            user_text = "\n".join((m.get("content", "") if isinstance(m, dict) else str(m)) for m in incoming)
        else:
            user_text = (message_data.get("task_intent") or message_data.get("text")
                         or "Generate the documentation set for this branch.")

        first = not s.system_injected
        if first:
            ctx = _doc_context_block(s)
            content = (ctx + "\n" + user_text) if ctx else user_text
            state = {"messages": [HumanMessage(content=content)], "tenant_id": tenant_id, "model_id": message_data.get("model_id")}
            s.system_injected = True
        else:
            state = {"messages": [HumanMessage(content=user_text)], "tenant_id": tenant_id, "model_id": message_data.get("model_id")}

        audit = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
        _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, tenant_id=tenant_id, agent_type="documentation", project_id=_project_id_from_message(message_data))
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
                    await _stream(state, config, websocket, session_id)
        finally:
            clear_mcp_tools()

        await manager.broadcast({
            "type": "activity_update",
            "activity": {"id": str(uuid4()), "type": "complete", "session_id": session_id,
                         "message": "Documentation updated", "time": "Just now"},
        })
    except Exception as e:
        logger.error("Documentation WS process error: %s", e)
        await manager.send_agent_response("Error Agent", f"An error occurred: {e}", session_id)


@documentation_standalone_router.post("/chat/")
async def chat(session_id: str = Form(...), user_id: str = Form(...), text: str = Form(None),
               pipeline_context: str = Form(None), uploaded_files: List[UploadFile] = File(None)):
    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(user_id)
    set_agent_folder("orchestrator")
    s = get_session(session_id)
    first = not s.system_injected
    user_text = text or "Generate the documentation set for this branch."
    if first:
        ctx = _doc_context_block(s)
        state = {"messages": [HumanMessage(content=(ctx + "\n" + user_text) if ctx else user_text)]}
        s.system_injected = True
    else:
        state = {"messages": [HumanMessage(content=user_text)]}
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
