"""Standalone Deployment Agent API — interactive readiness + deploy-package chat.

WebSocket /ws — streaming chat ("assess and generate deployment for this branch")
POST /chat/   — REST (non-streaming)
GET  /release/{session_id} — the session's current assessment artifact (for the page)

Mirrors the Code Review / Security standalone agents. The prepared target (cloned
repo + detected connector) is bound by project_id; BYO MCP tools are injected here
(the WS path bypasses the pipeline spine).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from uuid import uuid4
from typing import List

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.agent_access import assert_agent_access_for_chat

from agents_orchestrator.deployment_agent.agents.deployer import app as deploy_app
from agents_orchestrator.deployment_agent.prompts.deploy_prompt import DEPLOY_SYSTEM_PROMPT
from agents_orchestrator.deployment_agent.config.session_state import (
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

logger = logging.getLogger("deployment_agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(_h)

deployment_standalone_router = APIRouter()


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


def _deploy_context_block(s) -> str:
    if not s.work_dir:
        return ""
    tgt = f"PR #{s.pr_id} (source {s.source_branch})" if s.mode == "pr" else f"branch '{s.source_branch}'"
    return (
        f"You are assessing deployment readiness for {tgt} in repo '{s.repo_name}'.\n"
        f"Target environment: {s.environment}. Connected deploy connector "
        f"(deploy_via): {s.deploy_via}. Image: {s.image_registry or '(registry?)'}/"
        f"{s.image_name or s.repo_name}. Namespace: {s.namespace or '(default?)'}.\n"
        f"The repo is checked out. Inspect it, generate the connector-appropriate "
        f"deployment package (stage each file), assess readiness/risk, write the "
        f"runbooks, and submit your release assessment. Do NOT open the PR unless "
        f"the user explicitly asks.\n"
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
            server_ids = ((proj.mcp_servers if proj else None) or {}).get("deployment") or None
        if not server_ids:
            return []
        configs = await mcp_registry.resolve_server_configs(tenant_id, server_ids, agent_id="deployment")
        return await mcp_client.load_tools(configs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deployment chat MCP load failed: %s", exc)
        return []


async def _stream(state: dict, config: dict, websocket: WebSocket, session_id: str,
                  before_end=None) -> str:
    """Stream the model's ANSWERS to the client; returns the text sent.

    Narration from a message that goes on to call a tool is not an answer — a streamed
    chunk carries no tool call until its message completes, so every "Let me inspect the
    repo…" reached the reader. `AnswerStream` releases text once it is known to be one.
    A failure is raised even after text has streamed: it used to be swallowed, and the
    turn announced "Deployment assessment complete". `before_end` runs before
    `stream_end` — the report must be filed by the time the page refetches.
    """
    from langgraph.errors import GraphRecursionError

    from shared.services.answer_stream import AnswerStream  # noqa: PLC0415

    answers = AnswerStream(_extract_text)
    final = ""
    failure = None

    async def _send(text: str) -> None:
        nonlocal final
        if not text:
            return
        final += text
        await manager.send_personal_message(
            json.dumps({"type": "stream_chunk", "content": text, "session_id": session_id}), websocket
        )

    try:
        async for chunk in deploy_app.astream(state, stream_mode="messages", config=config):
            await _send(answers.feed(chunk[0] if isinstance(chunk, tuple) else chunk))
        await _send(answers.close())
    except GraphRecursionError:
        await _send(answers.close())
        await _send("\n\n> ⚠️ Step limit reached. Send another message to continue.")
    except Exception as e:  # noqa: BLE001 — re-raised below, after the report is filed
        logger.error("Deployment stream error: %s", e)
        failure = e
    if before_end is not None:
        try:
            await before_end()
        except Exception:  # noqa: BLE001 — a failed filing must not strand the client
            logger.exception("Deployment: before_end hook failed for session %s", session_id)
    if failure is not None:
        raise failure
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
    from shared.services.filed_documents import filed_documents_note  # noqa: PLC0415

    return await filed_documents_note(tenant_id, project_id, "deployment")


async def _file_readiness_report(session_id: str, user_id: str) -> None:
    """File the turn's release assessment as the Deployment Readiness Report — a DRAFT in
    the project's Documents — and record {filename, url, artifact_id} (or {error}) on it.

    Once per assessment: a turn that only talks about the report (or opens the PR, which
    edits the same assessment) files nothing new."""
    from config import sdlcSettings  # noqa: PLC0415
    from config.env import AGENTIC_BASE_URL  # noqa: PLC0415

    from agents_orchestrator.deployment_agent.readiness_report import write_readiness_report  # noqa: PLC0415
    from shared.services.chat_artifacts import register_generated_file  # noqa: PLC0415

    s = get_session(session_id)
    artifact = s.last_artifact
    if not artifact or artifact.get("document"):
        return
    owner = user_id or "deployment"
    out_dir = f"{sdlcSettings().FILES}/{owner}/deployment/{session_id}/output"
    try:
        docx_path, _md = await asyncio.to_thread(write_readiness_report, artifact, out_dir)
    except Exception as exc:  # noqa: BLE001 — recorded on the assessment, not swallowed
        logger.exception("Deployment readiness report not written for session %s", session_id)
        artifact["document"] = {"error": f"The report document could not be written ({type(exc).__name__}: {exc})"}
        return
    filename = os.path.basename(docx_path)
    url = f"{AGENTIC_BASE_URL}/generated/{owner}/deployment/{session_id}/output/{filename}"
    artifact_id = await register_generated_file(
        filename, docx_path, url, stage="deployment",
        note="Deployment readiness report generated by the Deployment agent.",
    )
    artifact["document"] = {"filename": filename, "url": url, "artifact_id": artifact_id} if artifact_id else {
        "filename": filename, "url": url,
        "error": "The report was written but could not be recorded in the project's Documents.",
    }
    await manager.broadcast({
        "type": "file_generated", "session_id": session_id, "filename": filename, "url": url,
        "artifact_id": artifact_id, "file_size": os.path.getsize(docx_path),
        "message": f"Generated file: {filename}",
    })


@deployment_standalone_router.websocket("/ws")
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
        logger.error("Deployment WS error: %s", e)
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
                user_id=user_id, agent_id="deployment",
            )

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
                         or "Assess deployment readiness and generate the deployment package.")

        # THE TRANSCRIPT, like every other agent's — the drawer lists this agent's past
        # conversations, and each opened empty because nothing was ever written.
        await persist_turn(
            session_id, "user", user_text, tenant_id=tenant_id or s.tenant_id or None,
            author_id=str(user_id) if user_id else None,
        )

        # THE PAGE'S MODEL PICKER. The graph reads `state["offering_id"]`; this handler —
        # the one the Deployment chat actually uses — never put it there, so the picker
        # changed nothing and every turn ran on whichever connection resolved first.
        _model_state = {
            "tenant_id": tenant_id,
            "project_id": _effective_project,
            "model_id": message_data.get("model_id"),
            "offering_id": message_data.get("offering_id"),
        }
        first = not s.system_injected
        if first:
            ctx = _deploy_context_block(s) + await _filed_documents_note(
                tenant_id or s.tenant_id, _effective_project or s.project_id,
            )
            content = (ctx + "\n" + user_text) if ctx else user_text
            state = {"messages": [HumanMessage(content=content)], **_model_state}
            s.system_injected = True
        else:
            state = {"messages": [HumanMessage(content=user_text)], **_model_state}

        # THE FILE THE PERSON ATTACHED, read here rather than left as a path.
        #
        # THIS ROUTE IS THE ONE THE DEPLOYMENT CHAT USES — `/sdlc/agent/deployment/ws`
        # is mounted on this module, not on deployment_agent_api, which had the block
        # already. So the page showed an attach button, the file uploaded, the chip
        # rendered, and the agent never saw a byte of it.
        for _content in attachment_message_contents(
            attachment_paths_from_context(message_data.get("pipeline_context"))
        ):
            state["messages"].append(HumanMessage(content=_content))

        audit = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
        _lf_cbs, _lf_meta = await agent_trace(session_id=session_id, tenant_id=tenant_id, user_id=user_id, agent_type="deployment", project_id=_project_id_from_message(message_data))
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 140, "callbacks": [audit, *_lf_cbs], "metadata": _lf_meta}
        await manager.broadcast({"type": "message_received", "session_id": session_id, "message": "Assessing…"})

        from shared.tools.mcp_runtime import set_mcp_tools, clear_mcp_tools
        if not s.mcp_loaded:
            s.mcp_tools = await _load_mcp_tools(tenant_id, project_id)
            s.mcp_loaded = True
        set_mcp_tools(s.mcp_tools)
        # Agent-profile prompt layer (design §3.4): the deployer graph node self-injects via
        # get_prompt_override("deployment") or DEPLOY_SYSTEM_PROMPT (verbatim, no MCP note),
        # so resolve the org/workspace/project profile over the BARE constant and set it into
        # the prompt_runtime contextvar for this turn. Fail-soft to base on any miss/error.
        _injected, _skills = await resolve_agent_turn(
            "deployment", DEPLOY_SYSTEM_PROMPT,
            tenant_id or s.tenant_id, project_id or s.project_id,
        )
        async def _file_report() -> None:
            await _file_readiness_report(session_id, str(user_id or ""))

        try:
            async with prompt_override_scope("deployment", _injected):
                async with skill_context_scope("deployment", _skills):
                    reply = await _stream(state, config, websocket, session_id, before_end=_file_report)
        finally:
            clear_mcp_tools()
        await persist_turn(
            session_id, "agent", reply, tenant_id=tenant_id or s.tenant_id or None,
            author_id="deployment", model=message_data.get("model_id"),
        )

        await manager.broadcast({
            "type": "activity_update",
            "activity": {"id": str(uuid4()), "type": "complete", "session_id": session_id,
                         "message": "Deployment assessment complete", "time": "Just now"},
        })
    except Exception as e:
        logger.error("Deployment WS process error: %s", e)
        reason = _failure_reason(e)
        await manager.send_agent_response("Error Agent", f"An error occurred: {reason}", session_id)
        await persist_turn(
            session_id, "agent", f"An error occurred: {reason}", tenant_id=tenant_id or None,
            author_id="deployment",
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
                         "message": "Deployment failed", "time": "Just now"},
        })


@deployment_standalone_router.post("/chat/")
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
    # Identity comes from the verified session, never from the form body — see
    # Security's chat() (security_agent_api.py) for why the field above used to be
    # trusted directly and what that let an authenticated caller do.
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""

    # `project_id` is client-supplied (a Form field) — resolve it to a real project the
    # caller is actually a member of, and check their role's reach to this agent on
    # THIS project specifically, before trusting it for anything (see
    # assert_agent_access_for_chat's docstring). The WS route (`_process_ws_message`,
    # above) calls the same helper, so both routes agree on who may do what.
    project_id = await assert_agent_access_for_chat(
        db, tenant_id=str(real_tenant_id), project_id=project_id,
        user_id=str(real_user_id), agent_id="deployment",
    )

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    set_agent_folder("orchestrator")
    s = get_session(session_id)
    s.project_id = s.project_id or project_id
    s.tenant_id = s.tenant_id or real_tenant_id
    first = not s.system_injected
    user_text = text or "Assess deployment readiness and generate the deployment package."
    if first:
        ctx = _deploy_context_block(s)
        state = {"messages": [HumanMessage(content=(ctx + "\n" + user_text) if ctx else user_text)]}
        s.system_injected = True
    else:
        state = {"messages": [HumanMessage(content=user_text)]}

    # Same as the socket path: refs uploaded to this session, extracted server-side.
    for _content in attachment_message_contents(
        attachment_paths_from_context(parse_pipeline_context(pipeline_context or {}))
    ):
        state["messages"].append(HumanMessage(content=_content))
    # CALLBACKS ARE NOT OPTIONAL PLUMBING, and this route had none while the WS route
    # above did. `langfuse_langchain_extras` is what threads the run's PROJECT into the
    # async context via set_run_project, and resolve_model_for_run reads it: once a
    # tenant has any org_model_grants row, effective_project_offerings fails CLOSED
    # without a project and every turn answers "no model configured", pointing an
    # administrator at a model that was never the problem. Latent while that table is
    # empty, which is exactly how it survives review. Same fix as the Design REST route.
    # The audit handler is the other half — without it a REST turn left no trail.
    _audit = AuditCallbackHandler(
        audit_service, run_id=session_id, tenant_id=str(real_tenant_id)
    )
    _lf_cbs, _lf_meta = await agent_trace(
        request=request, session_id=session_id, agent_type="deployment",
        project_id=project_id,
    )
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 140,
        "callbacks": [_audit, *_lf_cbs],
        "metadata": _lf_meta,
    }
    # Agent-profile prompt layer (design §3.4): resolve over the BARE constant (the deployer
    # node uses it verbatim) using the session's bound tenant/project. Fail-soft.
    _injected, _skills = await resolve_agent_turn(
        "deployment", DEPLOY_SYSTEM_PROMPT, s.tenant_id, s.project_id
    )
    final = ""
    async with prompt_override_scope("deployment", _injected), \
            skill_context_scope("deployment", _skills):
        async for chunk in deploy_app.astream(state, stream_mode="messages", config=config):
            msg = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(msg, ToolMessage):
                continue
            if hasattr(msg, "content") and msg.content and not (hasattr(msg, "tool_calls") and msg.tool_calls):
                final += _extract_text(msg.content)
    return {"conversation_id": session_id, "responses": final or "No response generated."}


@deployment_standalone_router.get("/release/{session_id}")
async def get_release(session_id: str):
    """Return the current session's deployment assessment artifact (or null)."""
    s = get_session(session_id)
    return {"release": s.last_artifact, "staged_files": [
        {"path": f["path"], "language": f.get("language", "yaml")} for f in s.staged_files
    ]}


@deployment_standalone_router.get("/sessions")
async def get_sessions():
    return {"current_session": "deployment"}
