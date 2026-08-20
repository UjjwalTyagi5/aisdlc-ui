"""Security Agent API — FastAPI router (interactive standalone path).

WebSocket /ws  — real-time streaming chat ("scan this branch/PR")
POST /chat/    — REST (orchestrator / non-streaming)

The scan TARGET (cloned branch) is prepared out-of-band by
POST /security/{project_id}/scan/prepare using the same project; this handler binds
that prepared state, streams the scan, then persists the produced artifact to a Run.
The WS path bypasses the pipeline capability spine, so BYO MCP tools are injected here.
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

from agents_orchestrator.security_agent.agents.scanner import app as scan_app
from agents_orchestrator.security_agent.prompts.security_prompt import SECURITY_SYSTEM_PROMPT
from agents_orchestrator.security_agent.config.session_state import (
    clear_session,
    get_prepared,
    get_session,
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
from shared.db import get_db_session, get_db_session_for_tenant
from shared.services.prompt_runtime import prompt_override_scope
from shared.services.skill_runtime import skill_context_scope
from shared.services.standalone_prompt import resolve_agent_turn

logger = logging.getLogger("security_agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(_h)

security_router = APIRouter()


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
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _scan_context_block(s) -> str:
    if not s.work_dir:
        return ""
    if s.mode == "pr":
        target = f"PR #{s.pr_id} \"{s.pr_title}\" (source branch {s.branch})"
    else:
        target = f"branch '{s.branch}'"
    return (
        f"You are running a security scan of {target} in repo '{s.repo_name}'. "
        f"The repository is checked out and ready to scan. Run the scan layers, "
        f"establish reachability, then submit your structured security review.\n"
    )


async def _load_mcp_tools(tenant_id: str, project_id: str | None) -> list:
    from config.env import MCP_ENABLED

    if not MCP_ENABLED or not tenant_id:
        return []
    try:
        from sqlalchemy import select
        from shared.services import mcp_registry, mcp_client
        from shared.models.orm import Project

        server_ids: list[str] | None = None
        if project_id:
            async with get_db_session_for_tenant(tenant_id) as session:
                proj = (
                    await session.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
                ).scalar_one_or_none()
            server_ids = ((proj.mcp_servers if proj else None) or {}).get("security") or None
        # No fallback to all-active servers: only BYO servers explicitly assigned to
        # this agent (via the Agents & Capabilities panel) are loaded — otherwise the
        # agent picks up unrelated tools (e.g. a public-repo Q&A MCP) and wastes turns.
        if not server_ids:
            return []
        configs = await mcp_registry.resolve_server_configs(tenant_id, server_ids, agent_id="security")
        tools = await mcp_client.load_tools(configs)
        logger.info("Security chat: loaded %d MCP tool(s)", len(tools))
        return tools
    except Exception as exc:  # noqa: BLE001
        logger.warning("Security chat MCP load failed: %s", exc)
        return []


async def _stream(state: dict, config: dict, websocket: WebSocket, session_id: str) -> str:
    from langgraph.errors import GraphRecursionError

    final, got = "", False
    try:
        async for chunk in scan_app.astream(state, stream_mode="messages", config=config):
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
                json.dumps({"type": "stream_chunk", "content": text, "session_id": session_id}),
                websocket,
            )
    except GraphRecursionError:
        notice = "Step limit reached for this scan. Send another message to continue."
        await manager.send_personal_message(
            json.dumps({"type": "stream_chunk", "content": f"\n\n> ⚠️ {notice}", "session_id": session_id}),
            websocket,
        )
    except Exception as e:
        logger.error("Security stream error: %s", e)
        if not got:
            raise
    await manager.send_personal_message(
        json.dumps({"type": "stream_end", "session_id": session_id}), websocket
    )
    return final


async def _persist_scan_to_run(session_id: str, project_id: str | None, tenant_id: str) -> None:
    s = get_session(session_id)
    if not s.last_artifact or not project_id or not tenant_id:
        return
    from sqlalchemy import select
    from shared.models.orm import Run

    artifact = dict(s.last_artifact)
    head = (artifact.get("context") or {}).get("head_sha", "")
    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            if head:
                existing = (
                    await db.execute(
                        select(Run).where(
                            Run.project_id == uuid.UUID(project_id),
                            Run.security_artifacts["context"]["head_sha"].astext == head,
                        )
                    )
                ).scalars().first()
                if existing:
                    return
            db.add(
                Run(
                    project_id=uuid.UUID(project_id),
                    tenant_id=uuid.UUID(tenant_id),
                    stage="security",
                    status="completed",
                    trigger="manual",
                    current_stage="security",
                    security_artifacts=artifact,
                )
            )
        await manager.broadcast({
            "type": "scan_created",
            "session_id": session_id,
            "risk_score": artifact.get("risk_score"),
            "signoff": (artifact.get("signoff") or {}).get("decision"),
        })
        s.last_artifact = None
    except Exception as exc:
        logger.warning("Failed to persist security scan for session %s: %s", session_id, exc)


@security_router.websocket("/ws")
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
                await manager.send_personal_message(
                    json.dumps({"type": "echo", "message": data}), websocket
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("Security WS error: %s", e)
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
                user_id=user_id, agent_id="security",
            )

        if not s.target_bound:
            prepared = get_prepared(tenant_id or s.tenant_id, project_id or s.project_id)
            if prepared:
                for k, v in prepared.items():
                    setattr(s, k, v)
                s.target_bound = True

        incoming = message_data.get("messages", [])
        if incoming:
            user_text = "\n".join(
                (m.get("content", "") if isinstance(m, dict) else str(m)) for m in incoming
            )
        else:
            user_text = (
                message_data.get("task_intent")
                or message_data.get("text")
                or "Please run the security scan and submit your review."
            )

        first = not s.system_injected
        if first:
            ctx = _scan_context_block(s)
            content = (ctx + "\n" + user_text) if ctx else user_text
            state = {"messages": [HumanMessage(content=content)], "tenant_id": tenant_id,
                     "model_id": message_data.get("model_id")}
            s.system_injected = True
        else:
            state = {"messages": [HumanMessage(content=user_text)], "tenant_id": tenant_id,
                     "model_id": message_data.get("model_id")}

        audit = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
        _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, tenant_id=tenant_id, agent_type="security", project_id=_project_id_from_message(message_data))
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 140, "callbacks": [audit, *_lf_cbs], "metadata": _lf_meta}

        await manager.broadcast(
            {"type": "message_received", "session_id": session_id, "message": "Scanning…"}
        )

        from shared.tools.mcp_runtime import set_mcp_tools, clear_mcp_tools
        if not s.mcp_loaded:
            s.mcp_tools = await _load_mcp_tools(tenant_id, project_id)
            s.mcp_loaded = True
        set_mcp_tools(s.mcp_tools)
        # Agent-profile prompt layer (design §3.4): the scanner graph node self-injects via
        # get_prompt_override("security") or SECURITY_SYSTEM_PROMPT, so resolve the
        # org/workspace/project profile over the BARE constant (the node re-appends its own
        # MCP note) and set it into the prompt_runtime contextvar for this turn. Fail-soft.
        _injected, _skills = await resolve_agent_turn(
            "security", SECURITY_SYSTEM_PROMPT,
            tenant_id or s.tenant_id, project_id or s.project_id,
        )
        try:
            async with prompt_override_scope("security", _injected):
                async with skill_context_scope("security", _skills):
                    await _stream(state, config, websocket, session_id)
        finally:
            clear_mcp_tools()

        await _persist_scan_to_run(session_id, project_id or s.project_id, tenant_id or s.tenant_id)
        await manager.broadcast({
            "type": "activity_update",
            "activity": {
                "id": str(uuid4()), "type": "complete",
                "session_id": session_id, "message": "Scan complete", "time": "Just now",
            },
        })
    except Exception as e:
        logger.error("Security WS process error: %s", e)
        await manager.send_agent_response("Error Agent", f"An error occurred: {e}", session_id)


@security_router.post("/chat/")
async def chat(
    request: Request,
    project_id: str = Form(...),
    session_id: str = Form(...),
    user_id: str = Form(...),  # kept for wire compatibility; NOT trusted for identity
    text: str = Form(None),
    pipeline_context: str = Form(None),
    uploaded_files: List[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db_session),
):
    # Identity comes from the verified session, never from the form body — the field
    # above used to be trusted directly, which let any authenticated caller claim to
    # be anyone (see multi-track-agent-access-design.md's "assume broken" framing).
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""

    # `project_id` is client-supplied (a Form field) — resolve it to a real project the
    # caller is actually a member of, and check their role's reach to this agent on
    # THIS project specifically, before trusting it for anything. See
    # assert_agent_access_for_chat's docstring for why a plain `resolve_project` +
    # `assert_agent_access` isn't enough (a role held on a different project would
    # otherwise be accepted here too). The WS route (`_process_ws_message`, above)
    # calls the same helper, so both routes agree on who may do what.
    project_id = await assert_agent_access_for_chat(
        db, tenant_id=str(real_tenant_id), project_id=project_id,
        user_id=str(real_user_id), agent_id="security",
    )

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    set_agent_folder("orchestrator")

    s = get_session(session_id)
    s.project_id = s.project_id or project_id
    s.tenant_id = s.tenant_id or real_tenant_id
    first = not s.system_injected
    user_text = text or "Please run the security scan and submit your review."
    if first:
        ctx = _scan_context_block(s)
        content = (ctx + "\n" + user_text) if ctx else user_text
        state = {"messages": [HumanMessage(content=content)]}
        s.system_injected = True
    else:
        state = {"messages": [HumanMessage(content=user_text)]}

    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 140}
    _injected, _skills = await resolve_agent_turn(
        "security", SECURITY_SYSTEM_PROMPT, s.tenant_id, s.project_id
    )
    final = ""
    async with prompt_override_scope("security", _injected), \
            skill_context_scope("security", _skills):
        async for chunk in scan_app.astream(state, stream_mode="messages", config=config):
            msg = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(msg, ToolMessage):
                continue
            if hasattr(msg, "content") and msg.content and not (hasattr(msg, "tool_calls") and msg.tool_calls):
                final += _extract_text(msg.content)
    await _persist_scan_to_run(session_id, s.project_id, s.tenant_id)
    return {"conversation_id": session_id, "responses": final or "No response generated."}


@security_router.get("/sessions")
async def get_sessions():
    return {"current_session": "security"}
