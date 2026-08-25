"""Development Agent API â€” FastAPI router.

WebSocket /ws  â€” real-time token streaming
POST /chat/    â€” REST endpoint (used by orchestrator)
GET  /sessions â€” health/status

After each stream, if the session has pr_url set the API broadcasts a pr_created event.
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import logging
import os
import pathlib
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List

import aiofiles
from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import select
from uuid import uuid4

from config.agent_context import build_agent_input_text, parse_pipeline_context, set_agent_folder
from config.connection_manager import manager
from config.context_broker import build_context
from config.orchestrator_state_client import set_state as _set_orchestrator_state, fetch_session_artifacts
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE, ADO_PAT
from config.websocket_utils import set_websocket_context
from config.ws_helper import broadcast_log, set_session_id, set_user_id, set_provider_kind
from langchain_core.messages import ToolMessage

from agents_orchestrator.development_agent.agents.dev_agent import app as planning_app
from agents_orchestrator.development_agent.prompts.dev_agent_prompt import DEV_SYS_MESSAGE
from agents_orchestrator.development_agent.config.session_state import (
    clear_session,
    get_session,
)
from shared.audit import AuditCallbackHandler
from shared.observability import langfuse_langchain_extras
from shared.audit.service import audit_service
from shared.authz.agent_access import assert_agent_access_for_chat
from shared.services.conversation_service import persist_turn
from shared.services.standalone_prompt import resolve_agent_turn, resolve_agent_skills
from shared.services.skill_runtime import skill_context_scope
from shared.services import dev_workspace_store
from shared.db import get_db_session_for_tenant
from shared.models.orm import Run

_FILES_DIR = str(pathlib.Path(__file__).resolve().parents[2] / "files")

development_router_orchestrator = APIRouter()

SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default=None)

def _project_id_from_message(message_data: dict) -> str | None:
    pc = parse_pipeline_context(message_data.get("pipeline_context") or {})
    return (
        message_data.get("project_id")
        or (message_data.get("context") or {}).get("project_id")
        or (pc.get("project_id") if isinstance(pc, dict) else None)
    )


# â”€â”€ Logging â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class _WsBroadcastHandler(logging.Handler):
    def emit(self, record):
        try:
            sid = SESSION_ID.get()
        except LookupError:
            sid = None
        activity = {
            "id": f"activity_{int(datetime.utcnow().timestamp() * 1000)}_{uuid.uuid4().hex[:6]}",
            "message": self.format(record),
            "type": "log",
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "printData": None,
            "sessionId": sid,
        }
        asyncio.create_task(manager.broadcast({"type": "activity_update", "activity": activity}))


logger = logging.getLogger("development_agent")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(levelname)s: %(message)s")
_ws_h = _WsBroadcastHandler()
_ws_h.setFormatter(_fmt)
logger.addHandler(_ws_h)
_con_h = logging.StreamHandler(sys.stdout)
_con_h.setFormatter(_fmt)
logger.addHandler(_con_h)


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _extract_text(content) -> str:
    """Normalise LangGraph message content â€” handles both str and list[block] formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


async def _build_dev_session_context(session_id: str) -> str:
    """Delegate to the shared context broker so all agents use one formatting path."""
    return await build_context(session_id, "development")


async def _load_dev_mcp_tools(tenant_id: str, project_id: str | None) -> list:
    """Resolve the project's BYO MCP servers for the development stage into tools.

    The pipeline does this via mcp_tools_for_stage; the interactive WS
    chat bypasses that, so we replicate it here. Prefers the project's per-stage
    assignment (project.mcp_servers['development']); if none is configured, falls
    back to the tenant's active MCP servers that allow the development stage — so a
    freshly-connected MCP works without a separate per-project assignment step.
    Never raises: any failure logs and yields no tools (MCP stays optional).
    """
    from config.env import MCP_ENABLED

    if not MCP_ENABLED or not tenant_id:
        return []
    try:
        from shared.services import mcp_registry, mcp_client
        from shared.models.orm import Project

        server_ids: list[str] | None = None
        if project_id:
            async with get_db_session_for_tenant(tenant_id) as session:
                proj = (
                    await session.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
                ).scalar_one_or_none()
            mapping = (proj.mcp_servers if proj else None) or {}
            server_ids = mapping.get("development") or None

        if not server_ids:
            active = await mcp_registry.list_servers(tenant_id, active_only=True)
            server_ids = [s["id"] for s in active]
        if not server_ids:
            return []

        configs = await mcp_registry.resolve_server_configs(
            tenant_id, server_ids, agent_id="development"
        )
        tools = await mcp_client.load_tools(configs)
        logger.info(
            "Dev chat: loaded %d MCP tool(s) from %d server(s)", len(tools), len(configs)
        )
        return tools
    except Exception as exc:  # noqa: BLE001 — MCP must never break the chat
        logger.warning("Dev chat MCP tool load failed: %s", exc)
        return []


async def _bind_pulled_workspace(s, message_data: dict, tenant_id: str) -> str:
    """Bind the session to a pre-pulled workspace if one exists for the given project.

    Returns a guidance string that the caller should append to sys_content so the
    agent knows the repo is already checked out and must NOT re-clone.  Returns ""
    when there is no workspace to bind (project_id absent, workspace not ready,
    or any lookup failure).
    """
    project_id = _project_id_from_message(message_data)
    if not project_id or not tenant_id:
        return ""

    try:
        ws = await dev_workspace_store.get_for_project(tenant_id, project_id)
    except Exception as exc:
        logger.warning("Workspace lookup failed for project %s: %s", project_id, exc)
        return ""

    if ws is None or ws.get("status") != "ready":
        return ""

    s.work_dir = ws["work_dir"]
    s.repo_url = ws.get("remote_url", "")
    s.branch_name = ws.get("branch", "")
    s.ado_project = ws.get("ado_project", "")
    s.ado_repo_name = ws.get("repo_name", "")
    s.repo_type = "ado"
    if not s.pat:
        try:
            from shared.services import ado_repos
            _, s.pat = await ado_repos.resolve_auth(tenant_id)
        except Exception:
            s.pat = ADO_PAT

    repo_name = ws.get("repo_name", "")
    branch = ws.get("branch", "")
    return (
        f"A repository is ALREADY pulled into your workspace: {repo_name} @ {branch} "
        f"(checked out at your work_dir). Work on it DIRECTLY â€” do NOT clone or scaffold. "
        f"Create a feature branch off '{branch}' and open a PR when done."
    )


# â”€â”€ Streaming helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _stream_agent_response(state: dict, config: dict, websocket: WebSocket, session_id: str) -> str:
    """Stream tokens via astream(stream_mode='messages') and return full response."""
    final_content = ""
    got_any_chunk = False

    from langgraph.errors import GraphRecursionError

    try:
        async for chunk in planning_app.astream(state, stream_mode="messages", config=config):
            msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk

            if not hasattr(msg_chunk, "content"):
                continue
            # Tool results are internal â€” never surface them in the chat UI.
            # Successes are already shown via broadcast_log; errors are handled by tool_node.
            if isinstance(msg_chunk, ToolMessage):
                continue
            content = _extract_text(msg_chunk.content)
            if not content:
                continue
            if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                continue

            final_content += content
            got_any_chunk = True
            await manager.send_personal_message(
                json.dumps({"type": "stream_chunk", "content": content, "session_id": session_id}),
                websocket,
            )
    except GraphRecursionError:
        logger.warning("Dev agent hit recursion limit (WS) for session %s", session_id)
        notice = "Step limit reached for this request. Work completed so far has been preserved. Send another message to continue."
        broadcast_log(manager, notice, level="WARNING")
        await manager.send_personal_message(
            json.dumps({"type": "stream_chunk", "content": f"\n\n> âš ï¸ {notice}", "session_id": session_id}),
            websocket,
        )
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        if not got_any_chunk:
            raise

    await manager.send_personal_message(
        json.dumps({"type": "stream_end", "session_id": session_id}),
        websocket,
    )
    return final_content




# â”€â”€ WebSocket endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@development_router_orchestrator.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    ticket = websocket.query_params.get("ticket", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(code=4401, reason='{"error": "invalid_or_expired_ticket", "detail": "Provide a valid single-use ticket from POST /auth/ws-ticket"}')
        return
    if AGENT_RUNTIME_MODE == "enterprise":
        expected_tenant = websocket.query_params.get("tenant_id", "")
        if expected_tenant and claims.get("tenant_id", "") != expected_tenant:
            await websocket.close(code=4403, reason='{"error": "tenant_mismatch", "detail": "Token tenant does not match requested tenant"}')
            return
    set_agent_folder("orchestrator")
    await manager.connect(websocket)
    user_id = claims.get("user_id", "")
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            session_id = message_data.get("session_id", str(uuid4()))
            manager.register_session(websocket, session_id)
            set_websocket_context(manager, session_id)
            set_session_id(session_id)
            set_user_id(user_id)

            msg_type = message_data.get("type")
            if msg_type == "user_message_with_files":
                _s = get_session(session_id)
                _pk = message_data.get("provider_kind") or _s.provider_kind
                if _pk:
                    set_provider_kind(_pk)
                    _s.provider_kind = _pk
                await _process_ws_message(message_data, websocket, user_id, tenant_id=claims.get("tenant_id", "") if claims else "")
            elif msg_type == "clear_agents":
                await manager.clear_agents()
            elif msg_type == "session_cleanup":
                await _handle_cleanup_ws(message_data, websocket)
            else:
                await manager.send_personal_message(
                    json.dumps({"type": "echo", "message": data}), websocket
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except RuntimeError as e:
        # Starlette raises RuntimeError("WebSocket is not connected") when receive_text()
        # runs after the chat BFF closes the one-shot per-turn WS — a normal end-of-turn
        # disconnect, not a failure. Only real RuntimeErrors are worth logging.
        if "not connected" not in str(e).lower() and "disconnect" not in str(e).lower():
            print(f"Dev agent WebSocket error: {e}")
        manager.disconnect(websocket)
    except Exception as e:
        print(f"Dev agent WebSocket error: {e}")
        manager.disconnect(websocket)


# HITL push gate — the user's message is an explicit go-ahead to push/PR.
# push_branch/create_pr stay blocked (and show the diff + ask) until this is true,
# so every code change is reviewed before anything leaves the machine.
_PUSH_APPROVE_PHRASES = (
    "push", "create pr", "create the pr", "open the pr", "open a pr",
    "go ahead", "approve", "ship it", "do it",
)
_PUSH_APPROVE_EXACT = {
    "yes", "y", "ok", "okay", "confirm", "proceed", "yep", "sure", "yes push",
}


def _is_push_approval(*texts) -> bool:
    """True when the user's message is an explicit approval to push / open a PR."""
    t = " ".join(x for x in texts if isinstance(x, str)).strip().lower()
    if not t:
        return False
    if t in _PUSH_APPROVE_EXACT:
        return True
    return any(p in t for p in _PUSH_APPROVE_PHRASES)


async def _process_ws_message(message_data: dict, websocket: WebSocket, user_id, tenant_id: str = ""):
    session_id = message_data.get("session_id", str(uuid4()))
    try:
        files_data = message_data.get("files", [])
        input_directory = f"{_FILES_DIR}/{user_id}/orchestrator/{session_id}/input"
        file_names: list[str] = []

        incoming_messages = message_data.get("messages", [])
        conversation_context = message_data.get("conversation_context")
        task_intent = message_data.get("task_intent")
        pipeline_context = message_data.get("pipeline_context")
        project_id = _project_id_from_message(message_data)

        # Gate every message, not just the first — a session can be reused across
        # projects on the client side, and the ticket only proves who the caller is,
        # not which project they may act on (nor that they're even a member of it —
        # see assert_agent_access_for_chat's docstring).
        async with get_db_session_for_tenant(tenant_id) as _access_db:
            project_id = await assert_agent_access_for_chat(
                _access_db, tenant_id=tenant_id, project_id=project_id,
                user_id=user_id, agent_id="development",
            )

        s = get_session(session_id)
        first_message = not s.system_injected
        _incoming_text = " ".join(
            str(m.get("content", "")) for m in incoming_messages if isinstance(m, dict)
        )
        s.push_gate_enabled = True  # standalone agent always gates push/PR on approval
        s.push_approved = _is_push_approval(task_intent, _incoming_text)

        _audit_handler = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
        _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, tenant_id=tenant_id, agent_type="development", project_id=_project_id_from_message(message_data))
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 160, "callbacks": [_audit_handler, *_lf_cbs], "metadata": _lf_meta}
        os.makedirs(input_directory, exist_ok=True)

        if files_data:
            await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])
            for file_data in files_data:
                try:
                    base_name, ext = os.path.splitext(file_data.get("name", "file"))
                    file_path = os.path.join(input_directory, f"{base_name}{ext}")
                    file_names.append(file_path)
                    with open(file_path, "wb") as f:
                        f.write(base64.b64decode(file_data["content"]))
                    logger.info(f"Saved file: {file_path}")
                except Exception as e:
                    logger.error(f"Error saving {file_data.get('name')}: {e}")

        if incoming_messages:
            state_messages = [
                HumanMessage(**m) if isinstance(m, dict) else m for m in incoming_messages
            ]
        else:
            text = build_agent_input_text(
                conversation_context=conversation_context,
                task_intent=task_intent,
                pipeline_context=pipeline_context,
                pipeline_sections=("requirements", "design", "development", "testing"),
            )
            state_messages = [HumanMessage(content=text)]

        if first_message:
            workspace_guidance = await _bind_pulled_workspace(s, message_data, tenant_id)
            session_context = await _build_dev_session_context(session_id)
            sys_content = DEV_SYS_MESSAGE
            if session_context:
                sys_content = DEV_SYS_MESSAGE + "\n\n" + session_context
            if workspace_guidance:
                sys_content = sys_content + "\n\n" + workspace_guidance
            # Agent-profile prompt layer (design §3.4): wrap the composed base with the
            # org/workspace/project profile. Fail-soft to base on any miss/error.
            sys_content, _ = await resolve_agent_turn(
                "development", sys_content, tenant_id or None, project_id
            )
            state = {
                "messages": [SystemMessage(content=sys_content)] + state_messages,
                "tenant_id": tenant_id,
                "model_id": message_data.get("model_id"),
            }
            s.system_injected = True
        else:
            state = {
                "messages": state_messages,
                "tenant_id": tenant_id,
                "model_id": message_data.get("model_id"),
            }

        # Chat attachments (uploaded via POST /conversations/{id}/attachments) arrive as
        # paths in pipeline_context.attachments — pass them to the agent's file tools.
        _attachments = pipeline_context.get("attachments") if isinstance(pipeline_context, dict) else None
        _attach_paths = [a.get("path") for a in (_attachments or []) if isinstance(a, dict) and a.get("path")]
        _all_files = file_names + _attach_paths
        if _all_files:
            state["messages"].append(
                HumanMessage(content=f"Uploaded files available at: {', '.join(_all_files)}")
            )

        await manager.broadcast(
            {"type": "message_received", "session_id": session_id, "message": "Processing your request..."}
        )

        # Persist the user turn to the conversation transcript (§11A) — best-effort, with
        # any attachment refs so a reopened session shows what was uploaded.
        await persist_turn(
            session_id, "user", task_intent, tenant_id=tenant_id or None, author_id=str(user_id),
            artifact_refs=_attachments or None,
        )

        # MCP: bind this project's development-stage servers as tools for the graph
        # run (interactive-chat surface). project_id comes from pipeline_context;
        # absent project / disabled MCP -> no tools (no-op). Mirrors the pipeline path.
        from shared.services.mcp_injection import mcp_tools_scope, project_stage_server_ids
        _project_id = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None
        _mcp_ids = await project_stage_server_ids(tenant_id or None, _project_id, "development")
        _dev_skills = await resolve_agent_skills("development", tenant_id or None, _project_id)

        _final_response = ""
        async with mcp_tools_scope(
            tenant_id or None, _mcp_ids, "development",
            project_id=_project_id, owner_id=str(user_id) or None,
        ), skill_context_scope("development", _dev_skills):
            _final_response = await _stream_agent_response(state, config, websocket, session_id)

        # Persist the agent turn to the conversation transcript (§11A) — best-effort.
        await persist_turn(
            session_id, "agent", _final_response, tenant_id=tenant_id or None,
            author_id="development", model=message_data.get("model_id"),
        )
        await _broadcast_files_updated(session_id, user_id)
        await _persist_pr_to_run(session_id, project_id, tenant_id)
        await _broadcast_pr_created(session_id)

        await manager.broadcast({
            "type": "activity_update",
            "activity": {
                "id": str(uuid4()), "type": "complete",
                "session_id": session_id, "message": "Processing complete", "time": "Just now",
            },
        })
    except Exception as e:
        logger.error(f"Dev WS error: {e}")
        await manager.send_agent_response("Error Agent", f"An error occurred: {str(e)}", session_id)
        # Terminal stream_end so the client unlocks even when streaming raised before
        # emitting one (the no-chunk error path re-raises). Without it the composer
        # stays disabled and the user's next message silently can't be sent.
        try:
            await manager.send_personal_message(
                json.dumps({"type": "stream_end", "session_id": session_id}), websocket
            )
        except Exception:
            pass
    finally:
        from shared.tools.mcp_runtime import clear_mcp_tools
        clear_mcp_tools()


async def _handle_cleanup_ws(message_data: dict, websocket: WebSocket):
    try:
        sid = message_data.get("session_id")
        if sid:
            clear_session(sid)
            await manager.send_session_update(sid, "cleaned", "Session cleanup completed")
        else:
            await manager.send_personal_message("Error: no session_id for cleanup", websocket)
    except Exception as e:
        await manager.send_personal_message(f"Cleanup error: {str(e)}", websocket)


# â”€â”€ REST endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@development_router_orchestrator.post("/chat/")
async def chat(
    request: Request,
    conversation_context: str = Form(None),
    task_intent: str = Form(None),
    pipeline_context: str = Form(None),
    provider_kind: str = Form(None),
    session_id: str = Form(...),
    user_id: str = Form(...),  # kept for wire compatibility; NOT trusted for identity
    uploaded_files: List[UploadFile] = File(None),
):
    # Identity comes from the verified session, never from the form body — see
    # assert_agent_access_for_chat's docstring / Security's chat() for why.
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    set_provider_kind(provider_kind or "azure_devops")
    set_agent_folder("orchestrator")

    input_directory = f"{_FILES_DIR}/{user_id}/orchestrator/{session_id}/input"
    file_names: list[str] = []

    s = get_session(session_id)
    first_message = not s.system_injected
    s.push_gate_enabled = True  # standalone agent always gates push/PR on approval
    s.push_approved = _is_push_approval(task_intent)

    _lf_pc = parse_pipeline_context(pipeline_context or {}) or {}
    _lf_pid = _lf_pc.get("project_id") if isinstance(_lf_pc, dict) else None

    # project_id here comes from pipeline_context (client-supplied) — resolve it to a
    # real project the caller is actually a member of, and check their role's reach to
    # THIS agent on THIS project, before doing any work. See
    # assert_agent_access_for_chat's docstring / Security's chat() for why a plain
    # resolve_project + assert_agent_access pair isn't enough.
    async with get_db_session_for_tenant(str(real_tenant_id)) as _access_db:
        _lf_pid = await assert_agent_access_for_chat(
            _access_db, tenant_id=str(real_tenant_id), project_id=_lf_pid,
            user_id=str(real_user_id), agent_id="development",
        )
    _audit_handler_rest = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id="")
    _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, agent_type="development", project_id=_lf_pid)
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 160, "callbacks": [_audit_handler_rest, *_lf_cbs], "metadata": _lf_meta}
    os.makedirs(input_directory, exist_ok=True)

    text = build_agent_input_text(
        conversation_context=conversation_context,
        task_intent=task_intent,
        pipeline_context=pipeline_context,
        pipeline_sections=("requirements", "design", "development", "testing"),
    )
    incoming = [HumanMessage(content=text)]

    if first_message:
        session_context = await _build_dev_session_context(session_id)
        sys_content = DEV_SYS_MESSAGE
        if session_context:
            sys_content = DEV_SYS_MESSAGE + "\n\n" + session_context
        # Agent-profile prompt layer (design §3.4). This REST endpoint carries no tenant_id
        # (unlike the WS path), so injection is a no-op here — kept for parity and to pick up
        # a profile automatically if this endpoint ever gains a tenant. Fail-soft to base.
        sys_content, _ = await resolve_agent_turn("development", sys_content, None, _lf_pid)
        state = {"messages": [SystemMessage(content=sys_content)] + incoming}
        s.system_injected = True
    else:
        state = {"messages": incoming}

    if uploaded_files:
        for uf in uploaded_files:
            base_name, ext = os.path.splitext(uf.filename)
            file_path = os.path.join(input_directory, f"{base_name}{ext}")
            file_names.append(file_path)
            with open(file_path, "wb") as f:
                f.write(await uf.read())
        state["messages"].append(
            HumanMessage(content=f"Uploaded files available at: {', '.join(file_names)}")
        )

    from langgraph.errors import GraphRecursionError

    final_content = ""
    recursion_hit = False
    _dev_skills_rest = await resolve_agent_skills("development", None, _lf_pid)
    try:
        async with skill_context_scope("development", _dev_skills_rest):
            async for chunk in planning_app.astream(state, stream_mode="messages", config=config):
                msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk
                if isinstance(msg_chunk, ToolMessage):
                    continue
                if hasattr(msg_chunk, "content") and msg_chunk.content:
                    chunk_text = _extract_text(msg_chunk.content)
                    if chunk_text and not (hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls):
                        final_content += chunk_text
    except GraphRecursionError:
        recursion_hit = True
        logger.warning("Dev agent hit recursion limit for session %s â€” returning partial work", session_id)
        broadcast_log(manager, "Step limit reached â€” work completed so far has been saved. Continue the conversation to finish remaining tasks.", level="WARNING")
        if not final_content:
            final_content = "The agent reached its step limit during this request. Work completed so far has been preserved in the session. Send another message to continue from where it left off."

    # Broadcast files written during this request (REST path â€” WS path calls this via _broadcast_files_updated)
    await _broadcast_files_updated(session_id, user_id)

    pr_info = None
    if s.pr_url:
        try:
            await manager.broadcast({
                "type": "pr_created",
                "session_id": session_id,
                "pr_url": s.pr_url,
                "pr_title": s.pr_title,
                "agent_name": "Development Agent",
            })
            pr_info = {"pr_url": s.pr_url, "pr_title": s.pr_title}
        except Exception as e:
            logger.warning(f"Failed to broadcast pr_created: {e}")
        finally:
            s.pr_url = ""
            s.pr_title = ""

    return {
        "conversation_id": session_id,
        "responses": final_content or "No response generated.",
        "generated_file": pr_info,
    }


@development_router_orchestrator.get("/sessions")
async def get_sessions():
    return {"current_session": "development"}


# â”€â”€ Code Viewer endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", ".vs", "dist", "build"}

_LANG_MAP = {
    "py": "python", "js": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript", "cs": "csharp",
    "java": "java", "json": "json", "yaml": "yaml", "yml": "yaml",
    "sql": "sql", "md": "markdown", "html": "html", "css": "css",
    "go": "go", "rs": "rust", "sh": "bash", "tf": "hcl",
    "toml": "toml", "xml": "xml", "txt": "text",
}


def _browser_cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "")
    if origin.startswith(("http://localhost:", "http://127.0.0.1:")) or origin.endswith(".ngrok-free.app"):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


def _workspace_for(user_id: str, session_id: str) -> pathlib.Path:
    return pathlib.Path(_FILES_DIR) / str(user_id) / "orchestrator" / str(session_id) / "project"


@development_router_orchestrator.get("/filetree/")
async def get_filetree(
    request: Request,
    response: Response,
    session_id: str = Query(...),
    user_id: str = Query(...),
):
    """Return a flat list of all files/dirs in the session workspace."""
    response.headers.update(_browser_cors_headers(request))
    workspace = _workspace_for(user_id, session_id)
    if not workspace.exists():
        return {"status": "ok", "tree": []}

    tree = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        rel_root = pathlib.Path(root).relative_to(workspace)
        for d in sorted(dirs):
            rel = (rel_root / d) if str(rel_root) != "." else pathlib.Path(d)
            tree.append({"path": str(rel).replace("\\", "/"), "type": "dir"})
        for f in sorted(files):
            rel = (rel_root / f) if str(rel_root) != "." else pathlib.Path(f)
            tree.append({"path": str(rel).replace("\\", "/"), "type": "file"})

    return {"status": "ok", "tree": tree}


@development_router_orchestrator.get("/filecontent/")
async def get_filecontent(
    request: Request,
    session_id: str = Query(...),
    user_id: str = Query(...),
    path: str = Query(...),
):
    """Return the text content and detected language of a single workspace file."""
    cors_headers = _browser_cors_headers(request)
    workspace = _workspace_for(user_id, session_id)
    try:
        target = (workspace / path).resolve()
        target.relative_to(workspace.resolve())  # raises ValueError if outside
    except ValueError:
        return JSONResponse(status_code=400, content={"status": "error", "msg": "path traversal denied"}, headers=cors_headers)

    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"status": "error", "msg": "file not found"}, headers=cors_headers)

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "msg": str(e)}, headers=cors_headers)

    ext = target.suffix.lstrip(".").lower()
    language = _LANG_MAP.get(ext, "text")
    return JSONResponse(content={"status": "ok", "path": path, "content": content, "language": language}, headers=cors_headers)


@development_router_orchestrator.get("/filedownload/")
async def get_filedownload(
    request: Request,
    session_id: str = Query(...),
    user_id: str = Query(...),
    path: str = Query(...),
):
    """Download a file from the session workspace for the Code Viewer file list."""
    cors_headers = _browser_cors_headers(request)
    workspace = _workspace_for(user_id, session_id)
    try:
        target = (workspace / path).resolve()
        target.relative_to(workspace.resolve())
    except ValueError:
        return JSONResponse(status_code=400, content={"status": "error", "msg": "path traversal denied"}, headers=cors_headers)

    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"status": "error", "msg": "file not found"}, headers=cors_headers)

    return FileResponse(path=target, filename=target.name, headers=cors_headers)


# â”€â”€ Internal helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_SKIP_DIRS_BROADCAST = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", "dist", "build"}

async def _broadcast_files_updated(session_id: str, user_id) -> None:
    s = get_session(session_id)

    # Scan the actual workspace so the count reflects ALL files, not just the ones
    # modified in the current session (e.g. brownfield sessions that only edit files).
    workspace = pathlib.Path(_FILES_DIR) / str(user_id) / "orchestrator" / str(session_id) / "project"
    all_files: list[str] = []
    if workspace.exists():
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS_BROADCAST]
            rel_root = pathlib.Path(root).relative_to(workspace)
            for f in sorted(files):
                rel = (rel_root / f) if str(rel_root) != "." else pathlib.Path(f)
                all_files.append(str(rel).replace("\\", "/"))

    # Fall back to tracked lists if filesystem scan found nothing
    if not all_files:
        all_files = list(dict.fromkeys(
            s.dev_artifacts.generated_files + s.dev_artifacts.changed_files
        ))

    if not all_files:
        return
    try:
        await manager.broadcast({
            "type": "files_updated",
            "session_id": session_id,
            "user_id": str(user_id),
            "files": all_files,
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast files_updated: {e}")


async def _persist_pr_to_run(session_id: str, project_id: str | None, tenant_id: str) -> None:
    s = get_session(session_id)
    if not s.pr_url or not project_id or not tenant_id:
        return
    dev_artifacts = s.dev_artifacts.model_dump()
    dev_artifacts["pr_url"] = s.pr_url
    dev_artifacts["pr_title"] = s.pr_title
    if dev_artifacts.get("status") != "pr_created":
        dev_artifacts["status"] = "pr_created"
    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            existing = (
                await db.execute(
                    select(Run).where(
                        Run.project_id == uuid.UUID(project_id),
                        Run.development_artifacts["pr_url"].astext == s.pr_url,
                    )
                )
            ).scalars().first()
            if existing:
                return
            try:
                async with db.begin_nested():
                    db.add(
                        Run(
                            project_id=uuid.UUID(project_id),
                            tenant_id=uuid.UUID(tenant_id),
                            stage="development",
                            status="completed",
                            trigger="manual",
                            current_stage="development",
                            development_artifacts=dev_artifacts,
                        )
                    )
            except Exception as dup_exc:
                import sqlalchemy.exc
                if isinstance(dup_exc, sqlalchemy.exc.IntegrityError):
                    logger.debug(
                        "PR Run insert raced — already persisted for session %s: %s",
                        session_id, dup_exc,
                    )
                else:
                    raise
    except Exception as exc:
        logger.warning("Failed to persist PR to Run for session %s: %s", session_id, exc)


async def _broadcast_pr_created(session_id: str) -> None:
    s = get_session(session_id)
    if s.pr_url:
        try:
            await manager.broadcast({
                "type": "pr_created",
                "session_id": session_id,
                "pr_url": s.pr_url,
                "pr_title": s.pr_title,
                "agent_name": "Development Agent",
            })
        except Exception as e:
            logger.warning(f"Failed to broadcast pr_created: {e}")
        finally:
            s.pr_url = ""
            s.pr_title = ""



