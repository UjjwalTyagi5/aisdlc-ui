"""Design Architecture Agent — FastAPI router (WebSocket + REST).

Phase 6 hardening:
- Artifact sections parsed through shared parse_artifact_sections() → DesignArtifacts
- Persisted in-process via shared/services/agent_session_store (Postgres tables
  agent_sessions / orchestrator_state) — no Django HTTP dependency
- docx_url attached to DesignArtifacts when a document is generated
- Handoff validated through shared parse_handoff_payload() before persistence
- last_handoff_event persisted alongside design_artifacts
- Removed module-level shared.prev_session_id state
- REST /chat/ now also detects and persists HANDOFF (was WS-only)
- User-visible errors sanitised through classify_error()
- Cost/audit logging via AuditCallbackHandler → Postgres agent_call_logs/audit_events
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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, SystemMessage
from uuid import uuid4
import contextvars

from agents_orchestrator.design_architecture_agent.agents.architecture import (
    app as planning_app,
    DESIGN_SYS_MESSAGE,
)
from agents_orchestrator.design_architecture_agent.config import shared
from config.agent_context import build_agent_input_text, set_agent_folder
from config.connection_manager import manager
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE
from config.context_broker import build_context
from config.orchestrator_state_client import fetch_session_artifacts
from config.websocket_utils import set_websocket_context
from config.ws_helper import broadcast_log, set_session_id, set_user_id, set_provider_kind
from shared.authz.agent_access import assert_agent_access_for_chat
from shared.db import get_db_session_for_tenant
from shared.errors import classify_error
from shared.models.design import parse_artifact_sections
from shared.audit import AuditCallbackHandler
from shared.observability import langfuse_langchain_extras
from shared.audit.service import audit_service
from shared.services.conversation_service import persist_turn
from shared.services.standalone_prompt import resolve_agent_turn, resolve_agent_skills
from shared.services.skill_runtime import skill_context_scope
from config import sdlcSettings

esett = sdlcSettings()

# Derive files directory from this file's location (matches process_api.py static mount)
_FILES_DIR = str(pathlib.Path(__file__).resolve().parents[2] / "files")

design_router_orchestrator = APIRouter()

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
        asyncio.create_task(manager.broadcast({"type": "activity_update", "activity": activity}))


logger = logging.getLogger("design_architecture_agent")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(levelname)s: %(message)s")
_ws_handler = _WebsocketBroadcastHandler()
_ws_handler.setFormatter(_fmt)
logger.addHandler(_ws_handler)
logger.addHandler(logging.StreamHandler(sys.stdout))

# Per-session flag so DESIGN_SYS_MESSAGE is injected exactly once per thread.
_initialized_sessions: set = set()


def _extract_text(content) -> str:
    """Return plain text from an AIMessage/AIMessageChunk content field.

    langchain_anthropic>=0.3 returns content as a list of content blocks
    ([{"type":"text","text":"..."}]) when tools are bound, even for text-only
    responses.  This helper normalises both formats.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""



# ── Artifact persistence ──────────────────────────────────────────────────────

async def _persist_design_artifacts(
    session_id: str,
    full_content: str,
    docx_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> None:
    """Parse artifact sections into DesignArtifacts and persist via in-process Postgres store."""
    from shared.services.agent_session_store import patch_session_artifacts
    artifacts = parse_artifact_sections(full_content, docx_url=docx_url)
    artifacts_dict = artifacts.model_dump()
    await patch_session_artifacts(session_id, {"design_artifacts": artifacts_dict}, tenant_id=tenant_id or None)


async def _build_session_context(session_id: str) -> str:
    """Upstream Requirements context for this turn — BY SESSION ONLY, deliberately.

    A PROJECT-KEYED FALLBACK WAS ADDED HERE AND REMOVED AGAIN. `build_context_for_project`
    reads the project's most recent Run, which makes the standalone Design page inherit
    whatever Requirements last produced. That is what the Development agent does, and it
    is NOT what this product wants for Design: opening Project -> Design on its own is a
    blank-slate design conversation, not a continuation of a pipeline the user did not
    start. Preloading the last run's payload also silently spends tokens on context the
    user never asked for and may be stale.

    So the rule is: context arrives through the ORCHESTRATOR, where the pipeline uses
    the run id as the session id and this lookup finds the artifacts for THAT run.
    Standalone starts empty, and the user pastes or points at what they want.

    Do not "fix" this by adding the project fallback back. If Design should be able to
    reach upstream artifacts on demand, that is a TOOL the model chooses to call, not an
    injection it cannot decline.
    """
    return await build_context(session_id, "design")


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@design_router_orchestrator.websocket("/ws")
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

            if message_data.get("type") == "user_message_with_files":
                if "provider_kind" in message_data:
                    set_provider_kind(message_data["provider_kind"])
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
        # WebSocketDisconnect — when receive_text() runs after the chat BFF closes the
        # one-shot per-turn WS (the agent's work already completed). Normal end-of-turn
        # disconnect, not a failure — don't surface it as an error.
        if "not connected" in str(exc).lower() or "disconnect" in str(exc).lower():
            logger.debug("WS closed by peer after turn: %s", exc)
        else:
            logger.error("Design agent WebSocket error: %s", classify_error(exc))
        manager.disconnect(websocket)
    except Exception as exc:
        logger.error("Design agent WebSocket error: %s", classify_error(exc))
        manager.disconnect(websocket)


async def _process_user_message_ws(message_data: dict, websocket: WebSocket, user_id: Any, tenant_id: str = "") -> None:
    session_id = message_data.get("session_id", str(uuid4()))
    files_data = message_data.get("files", [])
    input_directory = f"{_FILES_DIR}/{user_id}/orchestrator/{session_id}/input"
    file_names: List[str] = []

    incoming_messages = message_data.get("messages", [])
    conversation_context = message_data.get("conversation_context")
    task_intent = message_data.get("task_intent")
    pipeline_context = message_data.get("pipeline_context")

    # Consent for the Consequential epic write (update_ado_epic_design_complete),
    # recomputed FROM THIS TURN'S MESSAGE and set unconditionally — a turn that is not
    # an approval must clear the previous turn's yes. Only `task_intent` is read:
    # conversation_context replays earlier messages, and an approval in the transcript
    # is not an approval of what is being asked now. Same per-turn shape as the
    # Development agent's push_approved.
    from config.ws_helper import set_consequential_approved  # noqa: PLC0415
    from shared.authz.consequential import is_approval_message  # noqa: PLC0415
    set_consequential_approved(is_approval_message(task_intent))

    _lf_pid = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None

    # Gate every message, not just the first — a session can be reused across
    # projects on the client side. assert_agent_access_for_chat additionally
    # requires the caller be a MEMBER of the resolved project (not just any
    # same-tenant user with the right role name — see its docstring), so this
    # also closes the leak a plain resolve_project + assert_agent_access pair
    # would miss.
    if not _lf_pid:
        raise HTTPException(status_code=400, detail="project_id is required")
    async with get_db_session_for_tenant(tenant_id) as _access_db:
        _lf_pid = await assert_agent_access_for_chat(
            _access_db, tenant_id=tenant_id, project_id=_lf_pid,
            user_id=user_id, agent_id="design",
        )
        if isinstance(pipeline_context, dict):
            pipeline_context["project_id"] = _lf_pid

    from shared.services.budget_store import workspace_id_for_project  # noqa: PLC0415
    _lf_ws = await workspace_id_for_project(tenant_id or "", _lf_pid)
    _audit_handler = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
    _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, tenant_id=tenant_id, user_id=user_id, agent_type="design", project_id=_lf_pid, workspace_id=_lf_ws)
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 100, "callbacks": [_audit_handler, *_lf_cbs], "metadata": _lf_meta}
    os.makedirs(input_directory, exist_ok=True)

    if files_data:
        await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])
        for file_data in files_data:
            try:
                name = file_data.get("name", "upload")
                file_path = os.path.join(input_directory, pathlib.Path(name).name)
                file_names.append(file_path)
                with open(file_path, "wb") as fh:
                    fh.write(base64.b64decode(file_data["content"]))
                logger.info("Saved file: %s", file_path)
            except Exception as exc:
                logger.error("Error saving file %s: %s", file_data.get("name"), classify_error(exc))

    if incoming_messages:
        state_messages = [HumanMessage(**m) if isinstance(m, dict) else m for m in incoming_messages]
    else:
        text = build_agent_input_text(
            conversation_context=conversation_context,
            task_intent=task_intent,
            pipeline_context=pipeline_context,
            pipeline_sections=("requirements",),
        )
        state_messages = [HumanMessage(content=text)]

    first_call = session_id not in _initialized_sessions
    if first_call:
        # SESSION-KEYED ONLY. See _build_session_context: standalone Design starts
        # blank on purpose, so there is no project fallback to feed here.
        session_context = await _build_session_context(session_id)
        sys_content = DESIGN_SYS_MESSAGE
        if session_context:
            sys_content = DESIGN_SYS_MESSAGE + "\n\n" + session_context
        # Agent-profile prompt layer (design §3.4): wrap the composed base with the
        # org/workspace/project profile. Fail-soft to base on any miss/error.
        _pc_pid = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None
        sys_content, _ = await resolve_agent_turn(
            "design", sys_content, tenant_id or None, _pc_pid
        )
        state_messages = [SystemMessage(content=sys_content)] + state_messages
        _initialized_sessions.add(session_id)

    state: Dict[str, Any] = {
        "messages": state_messages,
        "tenant_id": tenant_id,
        "model_id": message_data.get("model_id"),
    }
    # Chat attachments (uploaded via POST /conversations/{id}/attachments) arrive as paths
    # in pipeline_context.attachments — pass them to the agent's file tools.
    _attachments = pipeline_context.get("attachments") if isinstance(pipeline_context, dict) else None
    _attach_paths = [a.get("path") for a in (_attachments or []) if isinstance(a, dict) and a.get("path")]
    _all_files = file_names + _attach_paths
    if _all_files:
        # Read attachment content SERVER-SIDE and inject it directly, rather than only
        # passing paths and hoping the agent calls read_document (which can silently skip,
        # or fail on a Windows path mangled through the LLM tool-call). Falls back to the
        # path hint when a file can't be read.
        from shared.tools.document_tools import (  # noqa: PLC0415
            extract_file_text as _extract,
            extraction_succeeded as _extracted_ok,
        )
        _parts, _unread = [], []
        for _p in _all_files:
            try:
                _txt = _extract(_p)
            except Exception:  # noqa: BLE001 — best-effort; degrade to the path hint
                _txt = ""
            # NOT `if _txt` — extraction returns a non-empty PLACEHOLDER on failure.
            # See the same block in requirements_agent_api for the failure it caused.
            if _extracted_ok(_txt):
                _parts.append(f"--- Attached file: {os.path.basename(_p)} ---\n{_txt.strip()[:20000]}")
            else:
                _unread.append(_p)
        if _parts:
            state["messages"].append(HumanMessage(
                content="The user attached the following file(s); use their content directly:\n\n"
                        + "\n\n".join(_parts)))
        if _unread:
            # Name the limit instead of handing over a path the file tools cannot read
            # either. Design is more exposed to this than Requirements — people attach
            # screenshots of diagrams and wireframes to it constantly.
            _names = ", ".join(os.path.basename(_u) for _u in _unread)
            state["messages"].append(HumanMessage(
                content=(
                    f"The user attached {_names}, which could not be read as text — it "
                    "is an image or an unsupported format. You CANNOT open it: do not "
                    "call a file tool on it, and do not claim to have looked at it. "
                    "Tell the user you cannot read that file type and ask them to paste "
                    "the relevant text, or re-upload as .pdf, .docx, .txt, .md, .csv "
                    "or .xlsx."
                )))

    await manager.broadcast({"type": "message_received", "session_id": session_id, "message": "Processing your request..."})

    start_ms = int(asyncio.get_event_loop().time() * 1000)
    final_content = ""
    streaming_started = False
    total_input_tokens = 0
    total_output_tokens = 0

    # Persist the user turn to the conversation transcript (§11A) — best-effort, with
    # any attachment refs so a reopened session shows what was uploaded.
    await persist_turn(
        session_id, "user", task_intent, tenant_id=tenant_id or None, author_id=str(user_id),
        artifact_refs=_attachments or None,
    )

    # MCP: bind this project's design-stage servers as tools for the duration of the
    # graph run (interactive-chat surface). project_id comes from pipeline_context;
    # absent project / disabled MCP -> no tools (no-op). Mirrors the pipeline path.
    from shared.services.mcp_injection import mcp_tools_scope, project_stage_server_ids
    _project_id = pipeline_context.get("project_id") if isinstance(pipeline_context, dict) else None
    # Expose tenant/project/run to tool context so chat-generated design files (docx/ppt/
    # diagram) persist as project Artifact rows (shared.services.chat_artifacts).
    from config.ws_helper import set_tenant_id, set_project_id, set_run_id  # noqa: PLC0415
    set_tenant_id(tenant_id or None)
    set_project_id(_project_id)
    set_run_id(pipeline_context.get("run_id") if isinstance(pipeline_context, dict) else None)
    _mcp_ids = await project_stage_server_ids(tenant_id or None, _project_id, "design")
    _design_skills = await resolve_agent_skills("design", tenant_id or None, _project_id)

    try:
        async with mcp_tools_scope(
            tenant_id or None, _mcp_ids, "design",
            project_id=_project_id, owner_id=str(user_id) or None,
        ), skill_context_scope("design", _design_skills):
            async for chunk in planning_app.astream(state, stream_mode="messages", config=config):
                msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk
                if hasattr(msg_chunk, "content") and msg_chunk.content:
                    content = _extract_text(msg_chunk.content)
                    if content and not (hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls):
                        streaming_started = True
                        final_content += content
                        await manager.send_personal_message(
                            json.dumps({"type": "stream_chunk", "content": content, "session_id": session_id}),
                            websocket,
                        )
                if hasattr(msg_chunk, "usage_metadata") and msg_chunk.usage_metadata:
                    total_input_tokens += msg_chunk.usage_metadata.get("input_tokens", 0)
                    total_output_tokens += msg_chunk.usage_metadata.get("output_tokens", 0)
    except Exception as exc:
        err = classify_error(exc, "design generation")
        logger.error(err)
        await manager.send_agent_response("Error Agent", f"An error occurred: {err}", session_id)

    if not streaming_started and final_content:
        await manager.send_agent_response("Design Agent", final_content, session_id)
    # Always emit a terminal stream_end so the client's chat stream closes and the
    # composer unlocks — on every path (streamed, fallback, or error). Without it an
    # empty/failed turn leaves the UI stuck "busy" (Textarea disabled).
    try:
        await manager.send_personal_message(
            json.dumps({"type": "stream_end", "session_id": session_id}), websocket
        )
    except Exception:
        pass

    # Persist the agent turn to the conversation transcript (§11A) — best-effort.
    await persist_turn(
        session_id, "agent", final_content, tenant_id=tenant_id or None,
        author_id="design", model=message_data.get("model_id"),
        tokens_in=total_input_tokens or None, tokens_out=total_output_tokens or None,
    )

    # Persist typed design artifacts when the session has generated content
    if final_content:
        docx_url = shared.output_file_url if shared.output_file and shared.output_file_url else None
        await _persist_design_artifacts(session_id, final_content, docx_url, tenant_id=tenant_id or None)

    # Broadcast file_generated event for any docx produced this turn
    if shared.output_file and shared.output_file_url:
        try:
            fp = os.path.join(f"{_FILES_DIR}/{user_id}/orchestrator/{session_id}/output", shared.output_file)
            file_size = os.path.getsize(fp) if os.path.exists(fp) else 0
            await manager.broadcast({
                "type": "file_generated",
                "session_id": session_id,
                "filename": shared.output_file,
                "url": shared.output_file_url,
                "file_size": file_size,
                "agent_name": "Design Agent",
            })
        except Exception as exc:
            logger.warning("Failed to broadcast file_generated: %s", exc)
        finally:
            shared.output_file = ""
            shared.output_file_url = ""

    await manager.broadcast({
        "type": "activity_update",
        "activity": {"id": str(uuid4()), "type": "complete", "session_id": session_id, "message": "Processing complete", "time": "Just now"},
    })


async def _handle_session_cleanup_ws(message_data: dict, websocket: WebSocket) -> None:
    sid = message_data.get("session_id")
    if sid:
        try:
            await planning_app.ainvoke(
                {"messages": [HumanMessage(content="cleanup")]},
                config={"configurable": {"thread_id": sid}},
            )
            await manager.send_session_update(sid, "cleaned", "Session cleanup completed")
        except Exception as exc:
            await manager.send_personal_message(
                f"Cleanup error: {classify_error(exc, 'session cleanup')}", websocket
            )
    else:
        await manager.send_personal_message("Error: No session_id provided for cleanup", websocket)


# ── REST endpoint ─────────────────────────────────────────────────────────────

@design_router_orchestrator.post("/chat/")
async def chat(
    request: Request,
    project_id: str = Form(...),
    conversation_context: str = Form(None),
    task_intent: str = Form(None),
    pipeline_context: str = Form(None),
    provider_kind: str = Form(None),
    session_id: str = Form(...),
    user_id: str = Form(...),  # kept for wire compatibility; NOT trusted for identity
    # Optional BYOK model override, same field the Requirements REST route takes. None
    # means "use the tenant's default", which resolve_model_for_run picks.
    model_id: str = Form(None),
    uploaded_files: List[UploadFile] = File(None),
):
    """REST endpoint — invokes Design Agent and persists typed design artifacts."""
    # Identity comes from the verified session, never from the form body (see
    # multi-track-agent-access-design.md's "assume broken" framing — the field
    # above used to be trusted directly, which let any authenticated caller claim
    # to be anyone). project_id is likewise resolved and access-checked before any
    # work happens, via the same helper the WS handler above uses.
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""
    async with get_db_session_for_tenant(real_tenant_id) as _access_db:
        project_id = await assert_agent_access_for_chat(
            _access_db, tenant_id=real_tenant_id, project_id=project_id,
            user_id=real_user_id, agent_id="design",
        )

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    # Tenant/project/run in the tool context, so a document this turn generates is
    # PERSISTED. `chat_artifacts.register_generated_file` returns early without both a
    # tenant and a project ("no tenant/project in context — skip persist"), so the .docx
    # and diagrams produced through this route were written to local disk and never
    # became Artifact rows — no Blob upload, nothing in the project's artifact panel,
    # and a debug-level log line as the only trace. The WS path set these; this one
    # never did.
    #
    # `project_id` here is the value assert_agent_access_for_chat RETURNED, not the raw
    # form field — it is resolved and access-checked, which is what makes it safe to use
    # as the isolation key for stored artifacts.
    from config.ws_helper import set_project_id, set_run_id, set_tenant_id  # noqa: PLC0415
    set_tenant_id(real_tenant_id or None)
    set_project_id(project_id or None)
    set_run_id(None)  # a standalone chat turn belongs to no pipeline run
    # Per-turn consent for the Consequential epic write — see the WS path above for why
    # this reads task_intent only, and why it is set on every turn.
    from config.ws_helper import set_consequential_approved  # noqa: PLC0415
    from shared.authz.consequential import is_approval_message  # noqa: PLC0415
    set_consequential_approved(is_approval_message(task_intent))
    set_provider_kind(provider_kind or "azure_devops")
    set_agent_folder("orchestrator")

    input_directory = f"{_FILES_DIR}/{user_id}/orchestrator/{session_id}/input"
    file_names: List[str] = []
    os.makedirs(input_directory, exist_ok=True)

    # THE PROJECT IS THE FORM FIELD, not something to dig out of pipeline_context.
    # `project_id` is required on this route and was REASSIGNED above by
    # assert_agent_access_for_chat to the resolved, access-checked id — so it is both
    # present and trustworthy, while pipeline_context is optional and often absent.
    #
    # Reading only pipeline_context meant a request without one had no project, and
    # `_set_run_project(None)` inside langfuse_langchain_extras then left model
    # resolution with no project context. Once a tenant has ANY org_model_grants row,
    # effective_project_offerings fails CLOSED without a project — so every call to
    # this endpoint answered "No usable model is configured for your organization",
    # pointing an administrator at a model that was never the problem.
    _lf_pid = project_id
    if not _lf_pid and pipeline_context:
        try:
            import json as _json_pc
            _pc = _json_pc.loads(pipeline_context) if isinstance(pipeline_context, str) else pipeline_context
            _lf_pid = _pc.get("project_id") if isinstance(_pc, dict) else None
        except Exception:
            _lf_pid = None
    # tenant_id passed too: it keys the usage meter and the budget checks, and the WS
    # path has always supplied it.
    _audit_handler_rest = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=real_tenant_id or "")
    _lf_cbs, _lf_meta = langfuse_langchain_extras(
        session_id=session_id, tenant_id=real_tenant_id or "", user_id=user_id,
        model=model_id, agent_type="design", project_id=_lf_pid,
    )
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 100, "callbacks": [_audit_handler_rest, *_lf_cbs], "metadata": _lf_meta}

    if uploaded_files:
        for uploaded_file in uploaded_files:
            filename = uploaded_file.filename or "upload"
            file_path = os.path.join(input_directory, pathlib.Path(filename).name)
            file_names.append(file_path)
            with open(file_path, "wb") as fh:
                fh.write(await uploaded_file.read())

    text = build_agent_input_text(
        conversation_context=conversation_context,
        task_intent=task_intent,
        pipeline_context=pipeline_context,
        pipeline_sections=("requirements",),
    )
    new_messages = [HumanMessage(content=text)]
    if file_names:
        new_messages.append(HumanMessage(content=f"please use the following files {', '.join(file_names)}"))

    first_call = session_id not in _initialized_sessions
    if first_call:
        session_context = await _build_session_context(session_id)
        sys_content = DESIGN_SYS_MESSAGE
        if session_context:
            sys_content = DESIGN_SYS_MESSAGE + "\n\n" + session_context
        # Agent-profile prompt layer (design §3.4). This REST endpoint carries no tenant_id
        # (unlike the WS path), so injection is a no-op here — kept for parity and to pick up
        # a profile automatically if this endpoint ever gains a tenant. Fail-soft to base.
        sys_content, _ = await resolve_agent_turn("design", sys_content, None, _lf_pid)
        new_messages = [SystemMessage(content=sys_content)] + new_messages
        _initialized_sessions.add(session_id)

    state: Dict[str, Any] = {
        "messages": new_messages,
        # WITHOUT THESE THIS ENDPOINT COULD NEVER RUN. The agent node calls
        # resolve_model_for_run(state["tenant_id"], state["model_id"]), so an absent
        # tenant resolved no provider and every request answered "No usable model is
        # configured for your organization" — a message that sends an administrator to
        # Org Settings to fix a model that was never the problem.
        #
        # The WS path has passed both since it was written; this one was built with
        # just the messages and nobody noticed, because the UI drives the WebSocket.
        "tenant_id": real_tenant_id,
        "model_id": model_id,
    }

    final_content = ""
    total_input_tokens = 0
    total_output_tokens = 0
    start_ms = int(asyncio.get_event_loop().time() * 1000)

    _design_skills_rest = await resolve_agent_skills("design", None, _lf_pid)
    try:
        async with skill_context_scope("design", _design_skills_rest):
            async for chunk in planning_app.astream(state, stream_mode="messages", config=config):
                msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk
                if hasattr(msg_chunk, "content") and msg_chunk.content:
                    chunk_text = _extract_text(msg_chunk.content)
                    if chunk_text and not (hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls):
                        final_content += chunk_text
                if hasattr(msg_chunk, "usage_metadata") and msg_chunk.usage_metadata:
                    total_input_tokens += msg_chunk.usage_metadata.get("input_tokens", 0)
                    total_output_tokens += msg_chunk.usage_metadata.get("output_tokens", 0)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=classify_error(exc, "design generation")) from exc

    # Persist typed design artifacts when content was generated
    if final_content:
        docx_url = shared.output_file_url if shared.output_file and shared.output_file_url else None
        await _persist_design_artifacts(session_id, final_content, docx_url)

    generated_file_info = None
    if shared.output_file and shared.output_file_url:
        try:
            fp = os.path.join(f"{_FILES_DIR}/{user_id}/orchestrator/{session_id}/output", shared.output_file)
            file_size = os.path.getsize(fp) if os.path.exists(fp) else 0
            await manager.broadcast({
                "type": "file_generated",
                "session_id": session_id,
                "filename": shared.output_file,
                "url": shared.output_file_url,
                "file_size": file_size,
                "agent_name": "Design Agent",
            })
            generated_file_info = {"filename": shared.output_file, "url": shared.output_file_url, "file_size": file_size}
        except Exception as exc:
            logger.warning("Failed to broadcast file_generated: %s", exc)

    shared.output_file = ""
    shared.output_file_url = ""

    return {
        "conversation_id": session_id,
        "responses": final_content or "No response generated.",
        "generated_file": generated_file_info,
    }


@design_router_orchestrator.get("/sessions")
async def get_sessions():
    return {"current_session": "design"}
