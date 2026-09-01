"""Requirements Agent — FastAPI router (WebSocket + REST).

Phase 5 hardening:
- Provider abstraction for ADO work-item endpoint (no direct env var usage)
- REQUIREMENTS_PAYLOAD:: extraction persisted as typed JSON object
- Handoff validated through HandoffPayload before persistence
- Input/file validation: size caps, accepted extensions
- User-visible errors sanitised — no stack traces or secrets
- Removed module-level shared.prev_session_id state
"""

import asyncio
import base64
import json
import logging
import os
import pathlib
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List

import aiofiles
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4
import contextvars

from agents_orchestrator.requirements_agent.agents.planning import app as planning_app, INGESTION_SYS_MESSAGE
from agents_orchestrator.requirements_agent.config import shared
from config import sdlcSettings
from config.agent_context import build_agent_input_text
from shared.tools.ingestion_summary import build_ingestion_summary
from config.connection_manager import manager
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE
from config.connectors.context import get_connector
from config.connectors.base import ConnectorNotAvailableError
from config.websocket_utils import set_websocket_context
from config.ws_helper import set_session_id, set_user_id, set_provider_kind, get_provider_kind
from shared.authz.agent_access import assert_agent_access_for_chat
from shared.db import get_db_session, get_db_session_for_tenant
from shared.services.agent_run import agent_run_scope
from shared.services.conversation_service import persist_turn
from shared.errors import classify_error
from shared.audit import AuditCallbackHandler
from shared.audit.service import audit_service
from shared.observability import langfuse_langchain_extras
from shared.services.standalone_prompt import resolve_agent_turn, resolve_agent_skills
from shared.services.skill_runtime import skill_context_scope

esett = sdlcSettings()
SYS_MESSAGE = INGESTION_SYS_MESSAGE

_initialized_sessions: set = set()
_session_provider_kinds: dict[str, str] = {}

# ── Validation limits ─────────────────────────────────────────────────────────
_MAX_MESSAGE_BYTES = 50_000          # 50 KB — reject oversized WS payloads
_MAX_FILE_BYTES    = 10 * 1024 * 1024  # 10 MB per file
_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".csv"}

# ── Router ────────────────────────────────────────────────────────────────────
requirement_router_orchestrator = APIRouter()


class WorkItemImportRequest(BaseModel):
    organization_url: str | None = None
    project: str | None = None
    team: str | None = None
    work_item_id: int
    provider_kind: str = "azure_devops"


# Backward-compat alias
AdoWorkItemImportRequest = WorkItemImportRequest


# ── Logging ───────────────────────────────────────────────────────────────────
SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default=None)


class _WebsocketBroadcastHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            session_id = SESSION_ID.get()
        except LookupError:
            session_id = None
        activity = {
            "id": f"activity_{int(datetime.utcnow().timestamp() * 1000)}_{uuid.uuid4().hex[:6]}",
            "message": self.format(record),
            "type": "log",
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "printData": None,
            "sessionId": session_id,
        }
        asyncio.create_task(
            manager.broadcast({"type": "activity_update", "activity": activity})
        )


logger = logging.getLogger("requirements_agent")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(levelname)s: %(message)s")
_ws_handler = _WebsocketBroadcastHandler()
_ws_handler.setFormatter(_fmt)
logger.addHandler(_ws_handler)
logger.addHandler(logging.StreamHandler(sys.stdout))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_extension(filename: str) -> None:
    ext = pathlib.Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' is not accepted. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )


def _validate_file_size(data: bytes, filename: str) -> None:
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File '{filename}' exceeds the 10 MB limit.",
        )


def _safe_error(exc: Exception, context: str = "processing") -> str:
    """Return a user-safe message — never raw stack traces or secrets."""
    return classify_error(exc, context)


async def _persist_session_artifacts(
    session_id: str,
    user_id: str,
    requirements_payload: Any = None,
    handoff_event: Any = None,
    current_stage: str = "design",
    tenant_id: str | None = None,
) -> None:
    """Patch AgentSession artifact fields via the in-process Postgres store."""
    from shared.services.agent_session_store import patch_session_artifacts
    patch: Dict[str, Any] = {}
    if requirements_payload is not None:
        patch["requirements_payload"] = requirements_payload
    if handoff_event is not None:
        patch["last_handoff_event"] = handoff_event
    if not patch:
        return
    try:
        await patch_session_artifacts(session_id, patch, tenant_id=tenant_id)
    except Exception as exc:
        logger.warning("AgentSession patch failed: %s", exc)


def _extract_requirements_payload(final_state: dict) -> Any:
    """Scan ToolMessages for REQUIREMENTS_PAYLOAD:: and return the parsed dict."""
    if not final_state:
        return None
    prefix = "REQUIREMENTS_PAYLOAD::"
    for msg in reversed(final_state.get("messages", [])):
        if not isinstance(msg, ToolMessage) or not msg.content:
            continue
        content = str(msg.content)
        idx = content.find(prefix)
        if idx == -1:
            continue
        raw = content[idx + len(prefix):].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None



def _process_agent_stream_for_chat_display(stream) -> list:
    responses = []
    for s in stream:
        for message in s["messages"]:
            if isinstance(message, (HumanMessage, ToolMessage, SystemMessage)):
                continue
            if not message.tool_calls:
                responses.append(message.content)
    return responses


def _extract_text(content) -> str:
    """Handle both str and list[block] formats from langchain_anthropic."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


async def _stream_agent_response(state: dict, config: dict, websocket: WebSocket, session_id: str) -> str:
    """Stream agent tokens to the WebSocket. Returns final assembled content."""
    final_content = ""
    streaming_started = False
    try:
        async for chunk in planning_app.astream(state, stream_mode="messages", config=config):
            msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk
            if not hasattr(msg_chunk, "content") or not msg_chunk.content:
                continue
            content = _extract_text(msg_chunk.content)
            if content and not getattr(msg_chunk, "tool_calls", None):
                streaming_started = True
                final_content += content
                await manager.send_personal_message(
                    json.dumps({"type": "stream_chunk", "content": content, "session_id": session_id}),
                    websocket,
                )
        # NB: the terminal `stream_end` is emitted by the caller (_process_user_message_ws)
        # for ALL paths — streaming, non-streaming fallback, and error — so the client's
        # chat stream always closes and the composer never gets stuck disabled.
    except Exception as exc:
        # Sanitized message goes to the user; the real exception + traceback stays
        # in the server log (exc_info) so failures are actually diagnosable.
        logger.error("Streaming error: %s", _safe_error(exc, "streaming"), exc_info=True)
    return final_content


# ── Endpoints ─────────────────────────────────────────────────────────────────

@requirement_router_orchestrator.post("/ado/work-item")
async def import_ado_work_item(payload: WorkItemImportRequest):
    """Import a single work item using the configured connector (provider-agnostic)."""
    try:
        connector = get_connector()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    org_url = (payload.organization_url or connector._org_url).strip()
    project = (payload.project or "").strip()
    team = (payload.team or "").strip()

    if not project:
        raise HTTPException(status_code=400, detail="Azure DevOps project is required.")

    try:
        # fetch_item_detail returns a normalized dict (fetch + normalize in the connector)
        normalized = await connector.read_adapter(
            "fetch_item_detail", project=project, item_id=payload.work_item_id
        )
        if team:
            normalized.setdefault("team", team)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=_safe_error(exc, "work item import"),
        ) from exc
    except ConnectorNotAvailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=_safe_error(exc, "work item import")) from exc

    summary = build_ingestion_summary(normalized)
    return {"status": "ok", "normalized": normalized, "summary": summary}


@requirement_router_orchestrator.websocket("/test-ws")
async def test_websocket(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text("WebSocket connection successful!")
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        pass


@requirement_router_orchestrator.websocket("/ws")
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
    await manager.connect(websocket)
    user_id = claims.get("user_id", "")
    try:
        while True:
            data = await websocket.receive_text()

            if len(data.encode()) > _MAX_MESSAGE_BYTES:
                await manager.send_personal_message(
                    json.dumps({"type": "error", "message": "Message too large (max 50 KB)."}),
                    websocket,
                )
                continue

            message_data = json.loads(data)
            session_id = message_data.get("session_id", str(uuid4()))
            manager.register_session(websocket, session_id)
            set_websocket_context(manager, session_id)
            set_session_id(session_id)
            set_user_id(user_id)

            if message_data.get("type") == "user_message_with_files":
                provider_kind = message_data.get("provider_kind") or _session_provider_kinds.get(session_id)
                if provider_kind:
                    _session_provider_kinds[session_id] = provider_kind
                    set_provider_kind(provider_kind)
                await _process_user_message_ws(message_data, websocket, user_id, tenant_id=claims.get("tenant_id", "") if claims else "")
            elif message_data.get("type") == "clear_agents":
                await manager.clear_agents()
            elif message_data.get("type") == "session_cleanup":
                await _handle_session_cleanup_ws(message_data, websocket)
            else:
                await manager.send_personal_message(
                    json.dumps({"type": "echo", "message": data}), websocket
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except RuntimeError as exc:
        # Starlette raises RuntimeError("WebSocket is not connected") — not
        # WebSocketDisconnect — when receive_text() runs after the peer closed the
        # socket. The chat BFF opens a one-shot WS per turn and closes it once it has
        # the terminal stream_end, so this is the NORMAL end-of-turn disconnect (the
        # agent's work already completed), not a processing failure. Treat it as a
        # clean disconnect instead of surfacing a misleading error.
        if "not connected" in str(exc).lower() or "disconnect" in str(exc).lower():
            logger.debug("WS closed by peer after turn: %s", exc)
        else:
            logger.error("WebSocket error: %s", _safe_error(exc), exc_info=True)
        manager.disconnect(websocket)
    except Exception as exc:
        logger.error("WebSocket error: %s", _safe_error(exc), exc_info=True)
        manager.disconnect(websocket)


async def _process_user_message_ws(message_data: dict, websocket: WebSocket, user_id: str, tenant_id: str = "") -> None:
    session_id = message_data.get("session_id", str(uuid4()))
    files_data = message_data.get("files", [])
    input_directory = f"{esett.FILES}/{user_id}/requirements_agent/{session_id}/input"
    file_names: List[str] = []

    conversation_context = message_data.get("conversation_context", "")
    task_intent = message_data.get("task_intent", "")
    pipeline_context = message_data.get("pipeline_context")

    # Consent for a Consequential board write, recomputed FROM THIS TURN'S MESSAGE and
    # set unconditionally — a turn that is not an approval must clear the previous
    # turn's yes, or "yes, create those" would authorise every write that followed it.
    # Only `task_intent` is read: conversation_context replays earlier messages, and an
    # approval in the transcript is not an approval of what is being asked now.
    # Same per-turn shape as the Development agent's push_approved.
    from config.ws_helper import set_consequential_approved  # noqa: PLC0415
    from shared.authz.consequential import is_approval_message  # noqa: PLC0415
    set_consequential_approved(is_approval_message(task_intent))

    # Gate every message, not just the first — a session can be reused across
    # projects on the client side, and the ticket only proves who the caller is,
    # not which project they may act on (nor, on its own, that they're even a
    # member of it — see assert_agent_access_for_chat's docstring).
    _access_project_id = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None
    async with get_db_session_for_tenant(tenant_id) as _access_db:
        try:
            await assert_agent_access_for_chat(
                _access_db, tenant_id=tenant_id, project_id=_access_project_id,
                user_id=user_id, agent_id="requirements",
            )
        except HTTPException as exc:
            await manager.send_agent_response("Error Agent", str(exc.detail), session_id)
            return

    final_message = build_agent_input_text(
        conversation_context=conversation_context,
        task_intent=task_intent,
        pipeline_context=pipeline_context,
        pipeline_sections=("requirements",),
    )

    _lf_project_id = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None
    from shared.services.budget_store import workspace_id_for_project  # noqa: PLC0415
    _lf_ws = await workspace_id_for_project(tenant_id or "", _lf_project_id)
    _audit_handler = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
    _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, tenant_id=tenant_id, user_id=user_id, agent_type="requirements", project_id=_lf_project_id, workspace_id=_lf_ws)
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 100, "callbacks": [_audit_handler, *_lf_cbs], "metadata": _lf_meta}
    os.makedirs(input_directory, exist_ok=True)

    if files_data:
        await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])
        for file_data in files_data:
            try:
                filename = file_data.get("name", "upload")
                _validate_extension(filename)
                if "content" not in file_data:
                    continue
                raw_bytes = base64.b64decode(file_data["content"])
                _validate_file_size(raw_bytes, filename)
                file_path = os.path.join(input_directory, pathlib.Path(filename).name)
                with open(file_path, "wb") as fh:
                    fh.write(raw_bytes)
                file_names.append(file_path)
                logger.info("Saved uploaded file: %s", file_path)
            except HTTPException:
                raise
            except Exception as exc:
                error_msg = _safe_error(exc, f"file processing for {file_data.get('name', '')}")
                logger.error(error_msg)
                await manager.send_agent_response("Error Agent", error_msg, session_id)

    incoming_messages = message_data.get("messages") or [HumanMessage(content=final_message)]
    first_turn = session_id not in _initialized_sessions
    if first_turn:
        pm_name = "Azure DevOps"
        try:
            _conn = get_connector()
            pm_name = _conn.display_name
        except Exception:
            pass
        sys_content = SYS_MESSAGE.replace("{PM_PROVIDER}", pm_name)
        # Agent-profile prompt layer (design §3.4): wrap the SAME {PM_PROVIDER}-substituted
        # base with the org/workspace/project profile. Fail-soft to base on any miss/error.
        _pc_pid = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None
        sys_content, _ = await resolve_agent_turn(
            "requirements", sys_content, tenant_id or None, _pc_pid
        )
        state: Dict[str, Any] = {
            "messages": [SystemMessage(content=sys_content)] + incoming_messages,
            "tenant_id": tenant_id,
            "model_id": message_data.get("model_id"),
        }
        _initialized_sessions.add(session_id)
    else:
        state = {
            "messages": incoming_messages,
            "tenant_id": tenant_id,
            "model_id": message_data.get("model_id"),
        }
    # Chat attachments uploaded via POST /conversations/{id}/attachments arrive here as
    # paths in pipeline_context.attachments — pass them to the agent's file tools.
    _attachments = pipeline_context.get("attachments") if isinstance(pipeline_context, dict) else None
    _attach_paths = [a.get("path") for a in (_attachments or []) if isinstance(a, dict) and a.get("path")]
    _all_files = file_names + _attach_paths
    if _all_files:
        # Read attachment content SERVER-SIDE and inject it directly rather than only
        # passing paths and relying on the agent to read them (which can silently skip or
        # fail on a mangled Windows path). Falls back to the path hint when unreadable.
        from shared.tools.document_tools import extract_file_text as _extract  # noqa: PLC0415
        _parts, _unread = [], []
        for _p in _all_files:
            try:
                _txt = _extract(_p)
            except Exception:  # noqa: BLE001 — best-effort; degrade to the path hint
                _txt = ""
            if _txt and _txt.strip():
                _parts.append(f"--- Attached file: {os.path.basename(_p)} ---\n{_txt.strip()[:20000]}")
            else:
                _unread.append(_p)
        if _parts:
            state["messages"].append(HumanMessage(
                content="The user attached the following file(s); use their content directly:\n\n"
                        + "\n\n".join(_parts)))
        if _unread:
            state["messages"].append(HumanMessage(
                content=f"please use the following files {', '.join(_unread)}"))

    await manager.broadcast({"type": "message_received", "session_id": session_id, "message": "Processing your request..."})

    # Persist the user turn to the conversation transcript (§11A) — best-effort, with any
    # attachment refs so a reopened session shows what was uploaded.
    await persist_turn(
        session_id, "user", task_intent, tenant_id=tenant_id or None, author_id=user_id,
        artifact_refs=_attachments or None,
    )

    # Inject the tenant's board connector for the duration of this turn so the
    # requirements board tools can reach ADO (live-pass F1). The scope also assembles
    # upstream context (no-op for requirements — it has no input_artifacts) and clears
    # the connector in finally (REQ-M3-10).
    # project_id (from the chat's pipeline_context dict) lets agent_run_scope resolve
    # the project's per-stage MCP selection and bind those tools for this turn.
    _project_id = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None
    # Expose tenant/project/run to the tool context so chat-generated files (docx/ppt/png)
    # can be persisted as project Artifact rows (see shared.services.chat_artifacts).
    from config.ws_helper import set_tenant_id, set_project_id, set_run_id  # noqa: PLC0415
    set_tenant_id(tenant_id or None)
    set_project_id(_project_id)
    set_run_id(pipeline_context.get("run_id") if isinstance(pipeline_context, dict) else None)
    _turn_skills = await resolve_agent_skills("requirements", tenant_id or None, _project_id)
    async with agent_run_scope(
        agent_id="requirements", tenant_id=tenant_id or None, session_id=session_id,
        project_id=_project_id, owner_id=user_id or None,
    ) as scope, skill_context_scope("requirements", _turn_skills):
        if scope.context_block:
            state["messages"].append(HumanMessage(content=scope.context_block))
        final_response = ""
        try:
            final_response = await _stream_agent_response(state, config, websocket, session_id)
            if not final_response:
                responses = _process_agent_stream_for_chat_display(
                    planning_app.stream(state, stream_mode="values", config=config)
                )
                if responses:
                    final_response = responses[-1]
                    await manager.send_agent_response("Requirements Agent", final_response, session_id)
        except Exception as exc:
            err = _safe_error(exc, "agent processing")
            logger.error(err, exc_info=True)
            await manager.send_agent_response("Error Agent", f"An error occurred: {err}", session_id)

        # Always send a terminal stream_end so the client's chat stream closes and the
        # composer unlocks — on every path (streamed, fallback, or error). Without this
        # an empty/failed turn leaves the UI stuck "busy" (Textarea disabled), so the
        # user's next message silently can't be sent.
        try:
            await manager.send_personal_message(
                json.dumps({"type": "stream_end", "session_id": session_id}), websocket
            )
        except Exception:
            pass

        # Persist the agent turn to the conversation transcript (§11A) — best-effort.
        await persist_turn(
            session_id, "agent", final_response, tenant_id=tenant_id or None,
            author_id="requirements", model=message_data.get("model_id"),
        )

        # Persist requirements artifact if present in final graph state
        try:
            final_state = await planning_app.aget_state(config)
            if final_state and final_state.values:
                req_payload = _extract_requirements_payload(final_state.values)
                if req_payload is not None:
                    await _persist_session_artifacts(
                        session_id=session_id,
                        user_id=str(user_id),
                        requirements_payload=req_payload,
                        handoff_event=None,
                        tenant_id=tenant_id or None,
                    )
        except Exception as exc:
            logger.warning("WS artifact persistence failed: %s", _safe_error(exc, "artifact persistence"))

    await manager.broadcast({
        "type": "activity_update",
        "activity": {
            "id": str(uuid4()),
            "type": "complete",
            "session_id": session_id,
            "message": "Message processed",
            "time": "Just now",
        },
    })


async def _handle_session_cleanup_ws(message_data: dict, websocket: WebSocket) -> None:
    session_id_to_clean = message_data.get("session_id")
    if session_id_to_clean:
        try:
            config = {"configurable": {"thread_id": session_id_to_clean}}
            state = {"messages": [HumanMessage(content="cleanup if needed")]}
            planning_app.invoke(state, config=config)
            await manager.send_session_update(session_id_to_clean, "cleaned", "Session cleanup completed")
        except Exception as exc:
            await manager.send_personal_message(
                f"Cleanup error: {_safe_error(exc, 'session cleanup')}", websocket
            )
    else:
        await manager.send_personal_message("Error: No session_id provided for cleanup", websocket)


@requirement_router_orchestrator.post("/chat/")
async def chat(
    request: Request,
    conversation_context: str = Form(None),
    task_intent: str = Form(None),
    pipeline_context: str = Form(None),
    provider_kind: str = Form(None),
    session_id: str = Form(...),
    user_id: str = Form(...),  # kept for wire compatibility; NOT trusted for identity
    tenant_id: str = Form(None),
    model_id: str = Form(None),
    uploaded_files: List[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db_session),
):
    """REST endpoint — invokes Requirements Agent and persists typed artifacts."""
    # Identity comes from the verified session, never from the form body — the field
    # above used to be trusted directly, which let any authenticated caller claim to
    # be anyone (see multi-track-agent-access-design.md's "assume broken" framing).
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""

    _lf_pid = None
    if pipeline_context:
        try:
            import json as _json_pc
            _pc = _json_pc.loads(pipeline_context) if isinstance(pipeline_context, str) else pipeline_context
            _lf_pid = _pc.get("project_id") if isinstance(_pc, dict) else None
        except Exception:
            _lf_pid = None

    # `_lf_pid` is client-supplied (parsed out of the pipeline_context Form field) --
    # resolve it to a real project the caller is actually a member of, and check their
    # role's reach to this agent on THIS project specifically, before trusting it for
    # anything. See assert_agent_access_for_chat's docstring for why a plain
    # resolve_project + assert_agent_access pair isn't enough (a role held on a
    # different project would otherwise be accepted here too). The WS route
    # (`_process_user_message_ws`, above) calls the same helper.
    _lf_pid = await assert_agent_access_for_chat(
        db, tenant_id=str(real_tenant_id), project_id=_lf_pid,
        user_id=str(real_user_id), agent_id="requirements",
    )

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    # Per-turn consent for a Consequential board write — see the WS path above for why
    # this reads task_intent only, and why it is set on every turn rather than only on
    # an approval.
    from config.ws_helper import set_consequential_approved  # noqa: PLC0415
    from shared.authz.consequential import is_approval_message  # noqa: PLC0415
    set_consequential_approved(is_approval_message(task_intent))
    resolved_provider_kind = provider_kind or _session_provider_kinds.get(session_id) or "azure_devops"
    _session_provider_kinds[session_id] = resolved_provider_kind
    set_provider_kind(resolved_provider_kind)

    input_directory = f"{esett.FILES}/{real_user_id}/requirements_agent/{session_id}/input"
    file_names: List[str] = []

    _audit_handler_rest = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=real_tenant_id or "")
    from shared.services.budget_store import workspace_id_for_project  # noqa: PLC0415
    _lf_ws_rest = await workspace_id_for_project(real_tenant_id or "", _lf_pid)
    _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, tenant_id=real_tenant_id or "", user_id=real_user_id, model=model_id, agent_type="requirements", project_id=_lf_pid, workspace_id=_lf_ws_rest)
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 100, "callbacks": [_audit_handler_rest, *_lf_cbs], "metadata": _lf_meta}
    os.makedirs(input_directory, exist_ok=True)

    if uploaded_files:
        for uploaded_file in uploaded_files:
            filename = uploaded_file.filename or "upload"
            _validate_extension(filename)
            raw_bytes = await uploaded_file.read()
            _validate_file_size(raw_bytes, filename)
            file_path = os.path.join(input_directory, pathlib.Path(filename).name)
            try:
                with open(file_path, "wb") as fh:
                    fh.write(raw_bytes)
                file_names.append(file_path)
                logger.info("Saved uploaded file (REST): %s", file_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=_safe_error(exc, f"saving file {filename}"),
                ) from exc

    final_message = build_agent_input_text(
        conversation_context=conversation_context,
        task_intent=task_intent,
        pipeline_context=pipeline_context,
        pipeline_sections=("requirements",),
    )

    incoming_messages = [HumanMessage(content=final_message)]
    first_turn = session_id not in _initialized_sessions
    if first_turn:
        pm_name = "Azure DevOps"
        try:
            _conn = get_connector()
            pm_name = _conn.display_name
        except Exception:
            pass
        sys_content = SYS_MESSAGE.replace("{PM_PROVIDER}", pm_name)
        # Agent-profile prompt layer (design §3.4): wrap the SAME {PM_PROVIDER}-substituted
        # base with the org/workspace/project profile. Fail-soft to base on any miss/error.
        _pc_pid = None
        if pipeline_context:
            try:
                import json as _json_pid  # noqa: PLC0415
                _pc = _json_pid.loads(pipeline_context) if isinstance(pipeline_context, str) else pipeline_context
                _pc_pid = _pc.get("project_id") if isinstance(_pc, dict) else None
            except Exception:
                _pc_pid = None
        sys_content, _ = await resolve_agent_turn(
            "requirements", sys_content, real_tenant_id or None, _pc_pid
        )
        state: Dict[str, Any] = {"messages": [SystemMessage(content=sys_content)] + incoming_messages}
        _initialized_sessions.add(session_id)
    else:
        state = {"messages": incoming_messages}
    # The agent node resolves the org's model from state["tenant_id"]; without it
    # resolution fails with NoModelConfiguredError even when a model is connected.
    state["tenant_id"] = real_tenant_id or ""
    if model_id:
        state["model_id"] = model_id
    if file_names:
        state["messages"].append(
            HumanMessage(content=f"please use the following files {', '.join(file_names)}")
        )

    # Run graph and capture final state. The `agent` node is async, so the graph
    # must be driven with the async API — calling the sync `.stream()` from inside
    # this running event loop raises before any node executes.
    #
    # Inject the tenant's board connector for the duration of this turn so the
    # requirements board tools can reach ADO (live-pass F1). The scope also assembles
    # upstream context (no-op for requirements — no input_artifacts) and clears the
    # connector in finally (REQ-M3-10).
    final_state = None
    # `_lf_pid` was already resolved (UUID string, not the raw client-supplied value)
    # and access-checked above by assert_agent_access_for_chat — reuse it rather than
    # re-parsing pipeline_context a third time.
    _turn_skills = await resolve_agent_skills("requirements", real_tenant_id or None, _lf_pid)
    async with agent_run_scope(
        agent_id="requirements", tenant_id=real_tenant_id or None, session_id=session_id,
        project_id=_lf_pid, owner_id=real_user_id or None,
    ) as scope, skill_context_scope("requirements", _turn_skills):
        if scope.context_block:
            state["messages"].append(HumanMessage(content=scope.context_block))
        try:
            async for event in planning_app.astream(state, stream_mode="values", config=config):
                final_state = event
        except Exception as exc:
            logger.exception("Requirements chat agent processing failed (session=%s)", session_id)
            raise HTTPException(status_code=500, detail=_safe_error(exc, "agent processing")) from exc

        responses = _process_agent_stream_for_chat_display([final_state]) if final_state else []

        # ── Extract and persist typed artifacts ───────────────────────────────
        requirements_payload = _extract_requirements_payload(final_state)

        if requirements_payload is not None:
            await _persist_session_artifacts(
                session_id=session_id,
                user_id=real_user_id,
                requirements_payload=requirements_payload,
                handoff_event=None,
                current_stage="design",
                tenant_id=real_tenant_id or None,
            )

    # ── Build response text ───────────────────────────────────────────────────
    final_text = responses[-1] if responses else "No response generated."

    output_file = shared.output_file
    shared.output_file = ""

    return {
        "conversation_id": session_id,
        "responses": final_text,
        "output_filename": output_file,
    }


# REMOVED: GET /download/{filename}, which served a flat process-wide `outputs/`
# directory.
#
# It was not an arbitrary-file read — traversal was guarded with realpath + a
# startswith check, and the router sits behind `artifact:view`. The problem was that
# NOTHING ABOUT IT WAS TENANT-SCOPED. Its signature was `(filename: str)` with no
# `Request`, so it could not have checked a tenant even in principle, and the documents
# it served are written under fixed names — `outputs/brd.docx`, `outputs/pdd.docx`,
# `outputs/risk_register.docx` (see the prompts in deployment_agent/api.py). One
# tenant's BRD overwrote another's, and whichever was on disk went to any caller
# holding `artifact:view`.
#
# Replaced by `GET /artifacts/{artifact_id}/download` (shared/routers/artifacts.py),
# which resolves the id through a join on `Run.tenant_id` — a cross-tenant id is a 404
# — and then applies the same project-visibility check as the rest of that router.
# Documents reach it via `shared/services/artifact_store.store_artifact`, which writes
# them under `{tenant_id}/{run_id}/{artifact_type}/{filename}` in blob storage.
#
# Safe to remove outright rather than deprecate: nothing in the frontend called it
# (grep-verified — the only agent download route the UI uses is the testing agent's).


@requirement_router_orchestrator.get("/sessions")
async def get_sessions():
    return {"current_session": "req"}
