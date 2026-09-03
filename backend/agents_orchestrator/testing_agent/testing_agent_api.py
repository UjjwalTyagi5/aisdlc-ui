import os
import asyncio
import json
import contextvars
import sys
import logging
import base64
import shutil
import stat
from datetime import datetime
import uuid
import zipfile  
from typing import List, Dict, Any
from urllib.parse import quote
from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, UploadFile, File, APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from config.ws_helper import set_session_id, broadcast_log, set_user_id, get_session_id
from config.connection_manager import manager
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from uuid import uuid4
from config.websocket_utils import set_websocket_context
from config.agent_context import build_agent_input_text, parse_pipeline_context, set_agent_folder
from config import sdlcSettings
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE
from shared.authz.agent_access import assert_agent_access_for_chat
from shared.db import get_db_session_for_tenant
from shared.models.testing import TestingArtifact
from shared.audit import AuditCallbackHandler
from shared.observability import langfuse_langchain_extras
from shared.audit.service import audit_service
from shared.services.conversation_service import persist_turn
from shared.services.agent_session_store import patch_session_artifacts
from agents_orchestrator.testing_agent.agents.testing_agent import app as _testing_app
from dotenv import load_dotenv


def _rmtree(path: str) -> None:
    """shutil.rmtree with Windows read-only handler.

    Git object files are marked read-only on Windows; plain rmtree raises
    PermissionError when it tries to unlink them. The handler clears the
    read-only bit before retrying the delete.
    """
    def _on_error(func, fpath, _exc_info):
        os.chmod(fpath, stat.S_IWRITE)
        func(fpath)

    try:
        shutil.rmtree(path, onexc=_on_error)   # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=_on_error)  # Python ≤ 3.11


try:
    from agents_orchestrator.testing_agent.agents.testing_agent import run_super_agent
except ImportError:
    print("FATAL: Could not import 'run_super_agent' from agents.testing_agent. Make sure the file is in the correct path.")
    sys.exit(1)

esett = sdlcSettings()
load_dotenv()

testing_router_orchestrator = APIRouter()


def _browser_cors_headers(request: Request) -> dict:
    origin = request.headers.get("origin", "")
    if (
        origin.startswith(("http://localhost:", "http://127.0.0.1:"))
        or origin.endswith(".ngrok-free.app")
    ):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


def _public_testing_url(path: str) -> str:
    base_url = (
        os.getenv("AGENTIC_PUBLIC_BASE_URL")
        or os.getenv("AGENTIC_BASE_URL")
        or ""
    ).rstrip("/")
    return f"{base_url}{path}" if base_url else path



async def _emit_testing_handoff(session_id: str, final_outputs: dict, *, tenant_id: str = "") -> None:
    """Persist TestingArtifact to AgentSession.testing_artifacts via in-process Postgres store.

    Best-effort — failure here must not break the user-facing chat response.

    When no real artifact was produced, persist a minimal "failed" artifact so
    the orchestrator has unambiguous signal rather than a missing row.
    """
    artifact_json = (final_outputs or {}).get("testing_artifact_json")
    try:
        if artifact_json:
            artifact = TestingArtifact.model_validate_json(artifact_json)
            artifact_value = artifact.model_dump()
        else:
            # Fallback: synthesize a minimal "failed" artifact so the orchestrator
            # gate doesn't stay stuck on "testing".
            fallback_summary = (
                (final_outputs or {}).get("final_user_message")
                or "Testing agent ended without producing an artifact "
                "(likely a greeting / non-test intent OR an upstream failure)."
            )
            artifact_value = TestingArtifact(
                plan_test_case_count=0,
                test_cases=[],
                status="failed",
                language="unknown",
                summary_md=fallback_summary,
                artifact_files=[],
            ).model_dump()
        await patch_session_artifacts(
            session_id,
            {"testing_artifacts": artifact_value},
            tenant_id=tenant_id or None,
        )
    except Exception as exc:
        # Don't let handoff failures break the response.
        print(f"WARNING: testing-agent handoff failed: {exc}")

# --- Session State Management ---
# In-memory cache to hold the state of each session.
# Key: session_id (str), Value: a full state dictionary from the agent.
SESSION_STATES: Dict[str, Dict] = {}

SYS_MESSAGE = """
You are a highly specialised testing agent that operates as a state machine. You're preferred mode of action is to use the tools you have been provided
You can answer in text and also correct mistakes in generated content.
You can answer directly any query that is not related to documents or files.
When updating content DO NOT invent information or change formatting.
You MUST Maintain consistency with the language of the generated content.
You MUST not infinitely Loop.
YOU MUST Follow these core instructions very carefully:
-WHEN USING UPDATE MAKE SURE ALL THE REQUIRED CONTEXT IS COMPILED CAREFULLY
-DO NOT REUPLOAD OR REUSE A TOOL WHEN IT DID NOT ERROR OR THE USER DID NOT EXPLICITLY ASK FOR SOMETHING THAT NEEDS A TOOL.
-DO NOT REUSE DELETE FILE IF IT ERRORS.
-ONLY SAVE TO DOCX WHEN EXPLICITLY ASKED TO SAVE IT.
-ONLY CLEANUP WHEN THE USER ASKS FOR IT.
-WHEN THE USER ASKS FOR AN UPDATE, MAKE SURE TO INCLUDE ALL THEIR HISTORICAL NEEDS AND REFERENCE THE CORRECT CONTENT.
-FOR a general query or update YOU MUST curate the query as an LLM prompt before calling the tool
-WHEN YOU CAN USE A TOOL YOU MUST USE A TOOL
-PROVIDE A APOLOGY RESPONSE FOR QUERIES THAT ARE NOT RELATED TO DOCUMENT PROCESSING
-YOU CAN ANSWER SIMPLE GREETINGS FROM THE USER
-ANY GENERATED CONTENT MUST BE INCLUDED IN YOUR RESPONSE TO THE USER
 
Follow this strict procedure:
1. Analyze the User's request to identify any local file paths mentioned or any previous content referenced.
2. Your first action MUST be to call the `upload_file` tool for every local file required.
3. Wait for the `upload_file` tool to return a file name reference
4. Your next action must be to call the appropriate processing tool you MUST pass the file name references you received from the previous step.
5. If the user asks for cleanup, You must call `delete_file`, using the same file name reference.
6. Between each query provide the an interactive response to user, if any content was generated you MUST include that in your response.
7. If asked to save a file save it only to outputs/file.docx where file is one of (test_plan,test_code,coverage_report) accordingly.
8. For Saving a file double check that the output path is of the format outputs/file.docx where file is one of (test_plan,test_code,coverage_report)
You MUST Remember the Core instructions
"""

SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default=None)

# Per-user last-session tracker.  Keyed by user_id so cleanup of a previous
# session never touches another user's workspace.
_LAST_SESSION: Dict[str, str] = {}


def _parse_selected_test_types(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                raw_items = parsed
            else:
                raw_items = raw.replace("+", ",").split(",")
        except Exception:
            raw_items = raw.replace("+", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        raw_items = list(raw)
    else:
        raw_items = []

    aliases = {
        "unit": "unit", "unit testing": "unit", "code": "unit",
        "functional": "functional", "functional testing": "functional",
        "functional_ui": "functional", "ui": "functional", "ui testing": "functional",
        "api": "api", "api testing": "api", "functional_api": "api", "endpoint": "api",
        "contract": "contract", "contract testing": "contract",
        "integration": "integration", "integration testing": "integration",
        "smoke": "smoke", "smoke testing": "smoke",
        "accessibility": "accessibility", "a11y": "accessibility",
        "mutation": "mutation", "mutation testing": "mutation", "mutation_testing": "mutation",
        "property": "property_based", "property based": "property_based", "property_based": "property_based",
        "negative": "negative_edge", "edge": "negative_edge", "negative_edge": "negative_edge",
        "security": "security_static", "security_static": "security_static", "sast": "security_static",
        "dependency": "dependency_scan", "dependency_scan": "dependency_scan", "dependencies": "dependency_scan",
    }
    selected: list[str] = []
    for item in raw_items:
        key = str(item or "").strip().lower()
        normalized = aliases.get(key, key if key else None)
        if normalized and normalized not in selected:
            selected.append(normalized)
    return selected


def _with_testing_selection(
    previous_state: Dict | None,
    *,
    selected_test_types=None,
    test_scope: str | None = None,
    target_url: str | None = None,
    api_timeout_s: float | None = None,
    test_config: dict | None = None,
) -> Dict | None:
    selected = _parse_selected_test_types(selected_test_types)
    if (not selected and not test_scope and not target_url
            and api_timeout_s is None and not test_config):
        return previous_state
    next_state = dict(previous_state or {})
    if selected:
        next_state["selected_test_types"] = selected
    if test_scope:
        next_state["test_scope"] = test_scope
    if target_url:
        next_state["target_url"] = target_url
    if api_timeout_s is not None:
        next_state["api_timeout_s"] = api_timeout_s
    # Per-type run configuration (target_url, base_url, credentials, env_vars,
    # auth, contract_source, wcag_level, vus, thresholds, …). Threaded into state
    # so skills/execution can consume the fields relevant to the selected type.
    # Convenience: lift a few well-known keys into the flat state fields the
    # existing nodes already read.
    if isinstance(test_config, dict) and test_config:
        next_state["test_config"] = {**(next_state.get("test_config") or {}), **test_config}
        cfg = next_state["test_config"]
        if cfg.get("target_url") and not next_state.get("target_url"):
            next_state["target_url"] = cfg["target_url"]
        if cfg.get("base_url") and not next_state.get("target_url"):
            next_state["target_url"] = cfg["base_url"]
        if cfg.get("api_scope"):
            next_state["api_scope"] = cfg["api_scope"]
        if cfg.get("test_scope") and not next_state.get("test_scope"):
            next_state["test_scope"] = cfg["test_scope"]
        try:
            if cfg.get("api_timeout_s") is not None and next_state.get("api_timeout_s") is None:
                next_state["api_timeout_s"] = float(cfg["api_timeout_s"])
        except (TypeError, ValueError):
            pass
    return next_state

async def _load_testing_mcp_tools(tenant_id: str, project_id: str | None) -> list:
    """Resolve the project's BYO MCP servers assigned to the testing stage into
    tools. Mirrors the other agents; only servers explicitly assigned to
    `testing` in the Capabilities panel are loaded (no all-active fallback)."""
    try:
        from config.env import MCP_ENABLED
    except Exception:
        return []
    if not MCP_ENABLED or not tenant_id:
        return []
    try:
        import uuid as _uuid
        from sqlalchemy import select
        from shared.services import mcp_registry, mcp_client
        from shared.models.orm import Project
        from shared.db import get_db_session_for_tenant

        server_ids = None
        if project_id:
            async with get_db_session_for_tenant(tenant_id) as session:
                proj = (
                    await session.execute(select(Project).where(Project.id == _uuid.UUID(project_id)))
                ).scalar_one_or_none()
            server_ids = ((proj.mcp_servers if proj else None) or {}).get("testing") or None
        if not server_ids:
            return []
        configs = await mcp_registry.resolve_server_configs(tenant_id, server_ids, agent_id="testing")
        tools = await mcp_client.load_tools(configs)
        logger.info("Testing chat: loaded %d MCP tool(s)", len(tools))
        return tools
    except Exception as exc:  # noqa: BLE001 — MCP must never break the chat
        logger.warning("Testing chat MCP load failed: %s", exc)
        return []


# Logging handler that broadcasts to websocket activity log
class WebsocketBroadcastHandler(logging.Handler):
    def emit(self, record):
        try:
            session_id = SESSION_ID.get()
        except LookupError:
            session_id = None
        message = self.format(record)
        activity = {
            "id": f"activity_{int(datetime.utcnow().timestamp()*1000)}_{uuid.uuid4().hex[:6]}",
            "message": message,
            "type": "log",
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "printData": None,
            "sessionId": session_id,
        }
        # Fire-and-forget broadcast so logging doesn't block.
        # Bug-fix: asyncio.create_task raises "no running event loop" when
        # the log call happens in a sync context (e.g. during request setup
        # before any await, or inside a worker thread). Guard with the
        # standard try/get_running_loop pattern so logging is never fatal.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(manager.broadcast({
                "type": "activity_update",
                "activity": activity,
            }))
        except RuntimeError:
            # No running loop — drop the broadcast silently. The print() in
            # the underlying broadcast_log helper still emits to stdout.
            pass

# Configure the logger
logger = logging.getLogger("testing_agent")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(levelname)s: %(message)s")
# Prevent duplicate handlers if this module is reloaded
if not any(isinstance(h, WebsocketBroadcastHandler) for h in logger.handlers):
    ws_handler = WebsocketBroadcastHandler()
    ws_handler.setFormatter(formatter)
    logger.addHandler(ws_handler)

# Also keep printing to console if desired
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)



async def _stash_byok_model(tenant_id: str | None, project_id: str | None,
                            previous_state: dict | None) -> str:
    """Resolve the run's BYOK model and stash it on the contextvar before the graph runs.

    BOTH CHAT ENTRYPOINTS SKIPPED THIS ENTIRELY. They call `_testing_app.invoke`
    directly rather than going through `run_super_agent_async`, which is the only
    place that resolved and stashed a model — so every node's `build_llm` found an
    empty contextvar and dropped to the local `.env` Anthropic key. The org's
    verified provider was never used, no usage was metered against its offering and
    no budget gate applied, on a run the org believes is governed.

    project_id is not optional detail either: once a tenant has any org_model_grants
    row, effective_project_offerings matches nothing without a project and reports
    "no model configured" for an org that has one.

    Returns the alias for the log. On a resolver error it returns "" and leaves the
    contextvar unset, preserving each node's existing fail-closed behaviour instead
    of failing the whole request here.
    """
    from shared.services.model_resolver import (
        ModelNotEnabledError, NoModelConfiguredError, resolve_model_for_run,
        set_resolved_model,
    )
    st = previous_state or {}
    try:
        resolved = await resolve_model_for_run(
            tenant_id or "", st.get("model_id"),
            offering_id=st.get("offering_id"), project_id=project_id or None,
        )
    except (NoModelConfiguredError, ModelNotEnabledError) as exc:
        logger.warning("testing: BYOK model resolution failed (tenant=%s project=%s): %s",
                       tenant_id, project_id, type(exc).__name__)
        return ""
    set_resolved_model(resolved)
    return resolved.alias


def process_agent_stream_for_chat_display(final_state, *, orchestrator_driven: bool = True):
    """Process agent output for chat display - adapted for super_agent.

    orchestrator_driven is kept for API compatibility but no longer controls
    sentinel injection — artifact persistence is now handled via the artifact
    service and Redis pub/sub rather than inline response text.
    """
    responses = []
    final_outputs = final_state.get('final_outputs', {})

    if 'final_summary_md' in final_outputs:
        body = final_outputs['final_summary_md']
    else:
        body = "Processing complete. Test artifacts have been generated."

    responses.append(body)
    return responses

@testing_router_orchestrator.websocket("/ws")
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
            print(f"DEBUG: WebSocket context set for session: {session_id} in /ws endpoint.")

            if message_data.get("type") == "user_message_with_files":
                await process_user_message_ws(message_data, websocket, user_id, tenant_id=claims.get("tenant_id", "") if claims else "")
            elif message_data.get("type") == "clear_agents":
                await manager.clear_agents()
                print("DEBUG: Agents cleared via WebSocket request.")
            elif message_data.get("type") == "session_cleanup":
                print(f"DEBUG: Received session cleanup request for session: {session_id}")
                await handle_session_cleanup_ws(message_data, websocket)
            else:
                print(f"DEBUG: Echoing message: {data}")
                await manager.send_personal_message(json.dumps({"type": "echo", "message": data}), websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("Client disconnected from WebSocket.")
    except RuntimeError as e:
        # Starlette raises RuntimeError("WebSocket is not connected") when receive_text()
        # runs after the chat BFF closes the one-shot per-turn WS — a normal end-of-turn
        # disconnect, not a failure. Only real RuntimeErrors are worth logging.
        if "not connected" not in str(e).lower() and "disconnect" not in str(e).lower():
            print(f"WebSocket error: {e}")
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# async def process_user_message_ws(message_data: dict, websocket: WebSocket, user_id):
#     """Process user message with files and send real-time updates via WebSocket"""
#     session_id = message_data.get("session_id", str(uuid4()))
#     try:
#         user_message = message_data.get("text", "")
#         files_data = message_data.get("files", [])  # Array of {name, content} objects
#         input_directory = f"{esett.FILES}/{user_id}/testing_agent/{session_id}/input"
#         output_directory = f"{esett.FILES}/{user_id}/testing_agent/{session_id}/output"
        
#         file_names = []

#         if shared.prev_session_id != "" and shared.prev_session_id != session_id:
#             print(f"DEBUG: New session ({session_id}) detected. Initiating cleanup of previous session ({shared.prev_session_id})...")
#             await manager.send_session_update(session_id, "cleanup", "Cleaning up previous session...")
            
#             # Clean up previous session directory and state
#             prev_session_dir = f"{esett.FILES}/{user_id}/testing_agent/{shared.prev_session_id}"
#             if os.path.exists(prev_session_dir):
#                 shutil.rmtree(prev_session_dir)
#             SESSION_STATES.pop(shared.prev_session_id, None) # Remove from state cache
                
#             await manager.send_session_update(session_id, "ready", "Session cleaned up, ready for new requests")
#             print(f"DEBUG: Previous session cleaned up. Ready for {session_id}.")

#         shared.prev_session_id = session_id
#         os.makedirs(input_directory, exist_ok=True)
#         os.makedirs(output_directory, exist_ok=True)

#         # Process files if provided
#         input_file_path = None
#         if files_data:
#             await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])
#             print(f"DEBUG: Processing {len(files_data)} uploaded file(s) for session {session_id}.")
#             for file_data in files_data:
#                 file_path = os.path.join(input_directory, file_data["name"])
#                 file_names.append(file_path)

#                 try:
#                     file_content = base64.b64decode(file_data["content"])
#                     with open(file_path, "wb") as file:
#                         file.write(file_content)
#                     print(f"DEBUG: Successfully saved uploaded file: {file_path}")
#                 except Exception as e:
#                     print(f"ERROR: Error processing file {file_data['name']}: {str(e)}")
#                     await manager.send_agent_response("Error Agent", f"Error processing file {file_data['name']}: {str(e)}", session_id)
#                     continue
            
#             if file_names:
#                 input_file_path = file_names[0]

#         await manager.broadcast({
#             "type": "message_received", "session_id": session_id, "message": "Processing your request..."
#         })
#         print(f"DEBUG: Initiating agent processing for user message: '{user_message[:100]}...'")
        
#         # Retrieve previous state from cache for this session
#         previous_state = SESSION_STATES.get(session_id)
#         if previous_state:
#             print(f"DEBUG: Found existing state for session {session_id}. Continuing conversation.")
        
#         # Run the super agent
#         final_state = await asyncio.to_thread(
#             run_super_agent, 
#             user_prompt=user_message, 
#             input_file_path=input_file_path,
#             previous_state=previous_state
#         )
        
#         # IMPORTANT: Store the new state back in the cache
#         SESSION_STATES[session_id] = final_state
#         print(f"DEBUG: Updated and stored state for session {session_id}.")

#         responses = process_agent_stream_for_chat_display(final_state)
#         if responses:
#             final_response = responses[-1]
#             await manager.send_agent_response("Testing Agent", final_response, session_id)
#             print(f"DEBUG: Agent sent final chat response: '{final_response[:100]}...'")

#         # Process and save output files
#         final_outputs = final_state.get('final_outputs', {})
#         generated_files = []
#         output_map = {
#             'excel_plan_b64': 'Generated_Test_Plan.xlsx',
#             'generated_code_py': 'Generated_Test_Code.py',
#             'coverage_report_xml': 'Coverage_Report.xml'
#         }

#         for key, filename in output_map.items():
#             if key in final_outputs:
#                 content = final_outputs[key]
#                 output_path = os.path.join(output_directory, filename)
#                 try:
#                     if key.endswith('_b64'):
#                         decoded_bytes = base64.b64decode(content)
#                         with open(output_path, 'wb') as f: f.write(decoded_bytes)
#                     else:
#                         with open(output_path, 'w', encoding='utf-8') as f: f.write(content)
                    
#                     print(f"DEBUG: Saved artifact: {filename}")
#                     generated_files.append(filename)
#                 except Exception as e:
#                     print(f"ERROR: Failed to save artifact {filename}: {e}")

#         if generated_files:
#             for filename in generated_files:
#                 await manager.broadcast({
#                     "type": "file_generated", "session_id": session_id, "filename": filename,
#                     "message": f"Generated file: {filename}"
#                 })
#                 print(f"DEBUG: Notified UI about generated file: {filename}")

#         await manager.broadcast({
#             "type": "activity_update",
#             "activity": { "id": str(uuid4()), "type": "complete", "session_id": session_id,
#                           "message": f"Processed message: '{user_message[:50]}...' ", "time": "Just now" }
#         })
#         print(f"DEBUG: User message processing complete for session {session_id}.")
    
#     except Exception as e:
#         print(f"ERROR: An error occurred during message processing for session {session_id}: {str(e)}")
#         await manager.send_agent_response("Error Agent", f"An error occurred: {str(e)}", session_id)

async def process_user_message_ws(message_data: dict, websocket: WebSocket, user_id: str, tenant_id: str = ""):

    """Process user message with files and send real-time updates via WebSocket"""

    session_id = message_data.get("session_id", str(uuid4()))

    try:

        # Extract core inputs

        user_message = message_data.get("text", "")

        conversation_context = message_data.get("conversation_context")

        task_intent = message_data.get("task_intent")
        pipeline_context = message_data.get("pipeline_context")

        # Gate every message, not just the first — a session can be reused across
        # projects client-side, and the ws-ticket only proves who the caller is, not
        # which project (or whether they're even a member of it) they may act on here.
        # See shared/authz/agent_access.py::assert_agent_access_for_chat's docstring.
        _early_pc = parse_pipeline_context(pipeline_context)
        _early_project_id = _early_pc.get("project_id") if isinstance(_early_pc, dict) else None
        async with get_db_session_for_tenant(tenant_id) as _access_db:
            await assert_agent_access_for_chat(
                _access_db, tenant_id=tenant_id, project_id=_early_project_id or "",
                user_id=user_id, agent_id="testing",
            )

        messages = message_data.get("messages")

        files_data = message_data.get("files", [])

        input_directory = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input"

        output_directory = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"

        file_names = []

        # Handle session switch / cleanup

        prev_session_id = _LAST_SESSION.get(user_id, "")
        if prev_session_id and prev_session_id != session_id:
            print(f"DEBUG: New session ({session_id}) detected. Cleaning up previous session {prev_session_id}...")
            await manager.send_session_update(session_id, "cleanup", "Cleaning up previous session...")
            prev_session_dir = f"{esett.FILES}/{user_id}/orchestrator/{prev_session_id}"
            if os.path.exists(prev_session_dir):
                _rmtree(prev_session_dir)
            SESSION_STATES.pop(prev_session_id, None)
            await manager.send_session_update(session_id, "ready", "Session cleaned up, ready for new requests")

        _LAST_SESSION[user_id] = session_id

        os.makedirs(input_directory, exist_ok=True)

        os.makedirs(output_directory, exist_ok=True)

        # Handle uploaded files

        input_file_path = None

        if files_data:

            await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])

            for file_data in files_data:

                file_path = os.path.join(input_directory, file_data["name"])

                file_names.append(file_path)

                try:

                    file_content = base64.b64decode(file_data["content"])

                    with open(file_path, "wb") as f:

                        f.write(file_content)

                    print(f"DEBUG: Saved uploaded file: {file_path}")

                except Exception as e:

                    error_msg = f"Error processing file {file_data['name']}: {str(e)}"

                    print(f"ERROR: {error_msg}")

                    await manager.send_agent_response("Error Agent", error_msg, session_id)

            if file_names:

                input_file_path = file_names[0]

        # Orchestrator-aware state building

        state_messages = []

        if messages:

            try:

                state_messages = json.loads(messages)

            except:

                state_messages = []

        elif user_message:

            state_messages.append({"role": "user", "content": user_message})

        if conversation_context or task_intent or pipeline_context:

            final_msg = build_agent_input_text(
                conversation_context=conversation_context,
                task_intent=task_intent,
                pipeline_context=pipeline_context,
                pipeline_sections=("requirements", "design", "development", "testing"),
            )

            state_messages.append({"role": "user", "content": final_msg})

        # Chat attachments (uploaded via POST /conversations/{id}/attachments) arrive as
        # paths in pipeline_context.attachments — hand them to the agent's file tools.
        _attachments = pipeline_context.get("attachments") if isinstance(pipeline_context, dict) else None
        _attach_paths = [a.get("path") for a in (_attachments or []) if isinstance(a, dict) and a.get("path")]
        if _attach_paths:
            state_messages.append(
                {"role": "user", "content": f"please use the following files {', '.join(_attach_paths)}"}
            )

        actual_user_message = state_messages[-1]["content"] if state_messages else user_message

        # Acknowledge to UI

        await manager.broadcast({

            "type": "message_received",

            "session_id": session_id,

            "message": "Processing your request..."

        })

        # Persist the user turn to the conversation transcript (§11A) — best-effort, with
        # any attachment refs so a reopened session shows what was uploaded.
        await persist_turn(
            session_id, "user", task_intent or actual_user_message,
            tenant_id=tenant_id or None, author_id=str(user_id),
            artifact_refs=_attachments or None,
        )

        # Retrieve old state if any

        previous_state = SESSION_STATES.get(session_id)
        previous_state = _with_testing_selection(
            previous_state,
            selected_test_types=message_data.get("selected_test_types"),
            test_scope=message_data.get("test_scope"),
            target_url=message_data.get("target_url"),
            api_timeout_s=message_data.get("api_timeout_s"),
            test_config=message_data.get("test_config"),
        )

        if previous_state:

            print(f"DEBUG: Found previous state for session {session_id}")

        # Bug-fix: WebSocket path now also injects structured clone_target /
        # pipeline_target if the UI puts them in message_data. Mirrors the
        # REST chat() handler at the bottom of this file.
        # execute_now (UI "Run" button) = full auto flow. Force-clear any stale
        # staged-approval state from a prior run in this session so the fresh run
        # proceeds plan → generate → execute → coverage without a gate.
        if message_data.get("execute_now"):
            previous_state = dict(previous_state or {})
            previous_state["execute_now"] = True
            previous_state["pending_testing_approval"] = None
            previous_state["staged_testing_enabled"] = False

        ws_clone = message_data.get("clone_target")
        if isinstance(ws_clone, dict) and ws_clone.get("repo") and ws_clone.get("branch"):
            previous_state = dict(previous_state or {})
            previous_state["clone_target"] = {
                "project": ws_clone.get("project") or "",
                "repo": ws_clone["repo"],
                "branch": ws_clone["branch"],
            }
        ws_pipeline = message_data.get("pipeline_target")
        if isinstance(ws_pipeline, dict) and ws_pipeline.get("pipeline_id"):
            previous_state = dict(previous_state or {})
            previous_state["pipeline_target"] = {
                "project": ws_pipeline.get("project") or (ws_clone or {}).get("project") or "",
                "pipeline_id": int(ws_pipeline["pipeline_id"]),
                "branch": ws_pipeline.get("branch") or "main",
            }

        # Phase 8.9d — pass orchestrator's full prior message list through to
        # LangGraph state so pull_upstream_context can mine dev-agent chat
        # output for branch/repo/project hints when artifacts are NULL.
        if state_messages:
            previous_state = dict(previous_state or {})
            previous_state["orchestrator_chat_history"] = state_messages
        parsed_pipeline_context = parse_pipeline_context(pipeline_context)
        if parsed_pipeline_context:
            previous_state = dict(previous_state or {})
            previous_state["pipeline_context"] = parsed_pipeline_context

        # Attach AuditCallbackHandler per-invocation (D-01) via config["callbacks"].
        # run_super_agent calls app.invoke without config, so we build the initial state
        # and invoke _testing_app directly to inject the audit callback.
        # Thread tenant_id into the graph so the clone resolves the per-tenant ADO
        # connector (Integrations secret store) — without it the clone falls back
        # to env-only creds and fails when ADO is connected via Integrations.
        previous_state = dict(previous_state or {})
        if tenant_id:
            previous_state["tenant_id"] = tenant_id
        # PROJECT AND PERSON, NOT JUST TENANT. Azure DevOps credentials are stored per
        # person per project (`project_integration_credentials`); the tenant-wide shared
        # fallback was deliberately removed. Resolving with a tenant alone therefore
        # finds nothing and the clone reports "the Azure DevOps connector is not
        # configured" on an organization where it plainly is — which is what a standalone
        # unit-test run did. The dev and code-review workspaces already pass all three.
        _ws_project_id = (
            _early_project_id
            or (parsed_pipeline_context.get("project_id") if isinstance(parsed_pipeline_context, dict) else None)
        )
        if _ws_project_id:
            previous_state["project_id"] = _ws_project_id
        if user_id:
            previous_state["owner_id"] = user_id

        _lf_pid = parsed_pipeline_context.get("project_id") if isinstance(parsed_pipeline_context, dict) else None
        _audit_handler = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
        _lf_cbs, _lf_meta = langfuse_langchain_extras(session_id=session_id, tenant_id=tenant_id, agent_type="testing", project_id=_lf_pid)
        from agents_orchestrator.testing_agent.agents.testing_agent import _initial_state as _build_initial
        _initial_ws = _build_initial(actual_user_message, input_file_path, previous_state)

        # MCP: load the project's testing-stage BYO tools and place them on the
        # mcp_runtime contextvar so the chat/follow-up node can call them. asyncio
        # .to_thread copies the current context, so the contextvar propagates into
        # the graph thread.
        _project_id = (parsed_pipeline_context or {}).get("project_id")
        from shared.tools.mcp_runtime import set_mcp_tools, clear_mcp_tools
        _mcp_tools = await _load_testing_mcp_tools(tenant_id, _project_id)
        set_mcp_tools(_mcp_tools)
        try:
            _alias = await _stash_byok_model(tenant_id, _project_id, previous_state)
            if _alias:
                logger.info("testing: using BYOK model %s", _alias)
            # to_thread copies the current context, so the stashed model travels with it.
            final_state = await asyncio.to_thread(
                _testing_app.invoke,
                _initial_ws,
                {"callbacks": [_audit_handler, *_lf_cbs], "metadata": _lf_meta},
            )
        finally:
            clear_mcp_tools()

        SESSION_STATES[session_id] = final_state

        # Send response
        responses = process_agent_stream_for_chat_display(
            final_state, orchestrator_driven=bool(conversation_context)
        )

        if responses:

            await manager.send_agent_response("Testing Agent", responses[-1], session_id)

        # Save artifacts

        final_outputs = final_state.get("final_outputs", {})

        # Phase 3 — persist TestingArtifact + emit HandoffPayload(to=deployment) via the new handoff_router

        if (
            not final_state.get("awaiting_scope")
            and not final_state.get("pending_testing_approval")
            and not final_state.get("pending_functional_approval")
        ):
            await _emit_testing_handoff(session_id, final_outputs, tenant_id=tenant_id)

        output_map = {

            "excel_plan_b64": "Generated_Test_Plan.xlsx",

            "generated_code_py": "Generated_Test_Code.py",

            "coverage_report_xml": "Coverage_Report.xml"

        }

        generated_files = []

        for key, filename in output_map.items():

            if key in final_outputs:

                content = final_outputs[key]

                output_path = os.path.join(output_directory, filename)

                try:

                    if key.endswith("_b64"):

                        with open(output_path, "wb") as f:

                            f.write(base64.b64decode(content))

                    else:

                        with open(output_path, "w", encoding="utf-8") as f:

                            f.write(content)

                    generated_files.append(filename)

                    await manager.broadcast({

                        "type": "file_generated",

                        "session_id": session_id,

                        "filename": filename,

                        "message": f"Generated file: {filename}"

                    })

                except Exception as e:

                    print(f"ERROR: Failed to save artifact {filename}: {e}")
                # --- MODIFICATION START ---
        # Phase 8.10c — UI artifacts written by Nodes/finalize.py + by
        # tools/ui_testing_agent.py live directly in output_directory and
        # under output_directory/ui_test_screenshots/. Add them to the zip
        # alongside the output_map files so the user gets a complete bundle.
        ui_extra_files: list[tuple[str, str]] = []  # (full_path, arcname)
        for ui_name in ("ui_test_results.xlsx", "ui_test_results.html", "ui_test_results.pdf"):
            ui_path = os.path.join(output_directory, ui_name)
            if os.path.isfile(ui_path):
                ui_extra_files.append((ui_path, ui_name))
        ui_shots_dir = os.path.join(output_directory, "ui_test_screenshots")
        if os.path.isdir(ui_shots_dir):
            for fname in sorted(os.listdir(ui_shots_dir)):
                if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                    full = os.path.join(ui_shots_dir, fname)
                    if os.path.isfile(full):
                        ui_extra_files.append((full, os.path.join("ui_test_screenshots", fname)))

        # If any files were generated, zip them up and send a single notification
        if generated_files or ui_extra_files:
            zip_filename = "testing_agent_reports.zip"
            zip_filepath = os.path.join(output_directory, zip_filename)

            print(f"DEBUG: Creating zip archive at {zip_filepath}")
            try:
                with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file_to_zip in generated_files:
                        full_path = os.path.join(output_directory, file_to_zip)
                        # arcname ensures the file isn't stored with its full path in the zip
                        zipf.write(full_path, arcname=file_to_zip)
                    # Phase 8.10c — UI files (flat + screenshots subdirectory)
                    for full_path, arcname in ui_extra_files:
                        zipf.write(full_path, arcname=arcname)

                print(f"DEBUG: Successfully created zip archive: {zip_filename}")

                # Broadcast a single notification for the generated zip file
                await manager.broadcast({
                    "type": "file_generated", "session_id": session_id, "filename": zip_filename,
                    "message": f"Generated file: {zip_filename}"
                })
                print(f"DEBUG: Notified UI about generated file: {zip_filename}")

            except Exception as e:
                print(f"ERROR: Failed to create zip archive: {e}")
                await manager.send_agent_response("Error Agent", f"Failed to create zip file: {e}", session_id)
        # --- MODIFICATION END ---

        await manager.broadcast({

            "type": "activity_update",

            "activity": {

                "id": str(uuid4()),

                "type": "complete",

                "session_id": session_id,

                "message": f"Processed message: '{actual_user_message[:50]}...'",

                "time": "Just now"

            }

        })

    except Exception as e:

        error_msg = f"Error in WebSocket processing for session {session_id}: {str(e)}"

        print(f"ERROR: {error_msg}")

        await manager.send_agent_response("Error Agent", error_msg, session_id)
 

async def handle_session_cleanup_ws(message_data: dict, websocket: WebSocket):
    """Handle session cleanup via WebSocket"""
    session_id_to_clean = message_data.get("session_id")
    user_id = message_data.get("user_id")
    try:
        if session_id_to_clean and user_id:
            print(f"DEBUG: Executing explicit session cleanup for session: {session_id_to_clean}")
            session_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id_to_clean}"
            
            # Remove files
            if os.path.exists(session_dir):
                _rmtree(session_dir)

            # Remove state from cache
            if session_id_to_clean in SESSION_STATES:
                del SESSION_STATES[session_id_to_clean]
                print(f"DEBUG: Removed state cache for session: {session_id_to_clean}")

            await manager.send_session_update(session_id_to_clean, "cleaned", "Session cleanup completed")
            print(f"DEBUG: Explicit cleanup completed for session: {session_id_to_clean}.")
        else:
            print("WARNING: No session_id or user_id provided for explicit cleanup request.")
            await manager.send_personal_message("Error: No session_id or user_id provided for cleanup", websocket)
    except Exception as e:
        print(f"ERROR: Explicit cleanup error for session {session_id_to_clean}: {str(e)}")
        await manager.send_personal_message(f"Cleanup error: {str(e)}", websocket)

# @testing_router_orchestrator.post("/chat/")
# async def chat(
#     session_id: str = Form(...),
#     user_message: str = Form(...),
#     user_id: str = Form(...),
#     uploaded_files: List[UploadFile] = File(None)
# ):
#     """REST endpoint for backward compatibility"""
#     set_websocket_context(manager, session_id)
#     set_session_id(session_id)
#     set_user_id(user_id)
#     print(f"DEBUG: REST chat request received for session {session_id}, message: {user_message[:100]}...")

#     input_directory = f"{esett.FILES}/{user_id}/testing_agent/{session_id}/input"
#     output_directory = f"{esett.FILES}/{user_id}/testing_agent/{session_id}/output"
    
#     if shared.prev_session_id != "" and shared.prev_session_id != session_id:
#         print(f"DEBUG: New session ({session_id}) detected in REST. Cleaning up previous session ({shared.prev_session_id})...")
#         prev_session_dir = f"{esett.FILES}/{user_id}/testing_agent/{shared.prev_session_id}"
#         if os.path.exists(prev_session_dir): shutil.rmtree(prev_session_dir)
#         SESSION_STATES.pop(shared.prev_session_id, None)

#     shared.prev_session_id = session_id
#     os.makedirs(input_directory, exist_ok=True)
#     os.makedirs(output_directory, exist_ok=True)

#     input_file_path = None
#     if uploaded_files:
#         uploaded_file = uploaded_files[0] # Using first file
#         file_path = os.path.join(input_directory, uploaded_file.filename)
#         input_file_path = file_path
#         try:
#             with open(file_path, "wb") as file: file.write(await uploaded_file.read())
#             print(f"DEBUG: Saved uploaded file (REST): {file_path}")
#         except Exception as e:
#             print(f"ERROR: Error saving uploaded file (REST) {uploaded_file.filename}: {str(e)}")
#             return {"error": f"Failed to save file {uploaded_file.filename}: {str(e)}"}
    
#     print("DEBUG: Invoking agent for REST request.")
#     try:
#         previous_state = SESSION_STATES.get(session_id)
#         if previous_state: print(f"DEBUG: Found existing state for session {session_id} in REST.")

#         final_state = run_super_agent(
#             user_prompt=user_message, 
#             input_file_path=input_file_path,
#             previous_state=previous_state
#         )
        
#         SESSION_STATES[session_id] = final_state # Store updated state
#         print(f"DEBUG: Updated and stored state for session {session_id} in REST.")
        
#         responses = process_agent_stream_for_chat_display(final_state)
#         final_outputs = final_state.get('final_outputs', {})
#         generated_files = []
#         output_map = {
#             'excel_plan_b64': 'Generated_Test_Plan.xlsx',
#             'generated_code_py': 'Generated_Test_Code.py',
#             'coverage_report_xml': 'Coverage_Report.xml'
#         }

#         for key, filename in output_map.items():
#             if key in final_outputs:
#                 content = final_outputs[key]
#                 output_path = os.path.join(output_directory, filename)
#                 try:
#                     if key.endswith('_b64'):
#                         with open(output_path, 'wb') as f: f.write(base64.b64decode(content))
#                     else:
#                         with open(output_path, 'w', encoding='utf-8') as f: f.write(content)
                    
#                     generated_files.append({
#                         "filename": filename,
#                         "download_url": f"/api/testing/download/{user_id}/{session_id}/{filename}"
#                     })
#                 except Exception as e:
#                     print(f"ERROR: Failed to save artifact {filename}: {e}")

#         response_data = {
#             "conversation_id": session_id,
#             "responses": responses[-1] if responses else "No response generated.",
#             "generated_files": generated_files
#         }
        
#         print(f"DEBUG: REST request processed. Response data: {response_data}")
#         return response_data
         
#     except Exception as e:
#         print(f"ERROR: Error in REST chat endpoint: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

@testing_router_orchestrator.post("/chat/")
async def chat(
    request: Request,
    session_id: str = Form(...),
    user_message: str = Form(None),
    conversation_context: str = Form(None),
    task_intent: str = Form(None),
    pipeline_context: str = Form(None),
    messages: str = Form(None),
    user_id: str = Form(...),  # kept for wire compatibility (session/file paths); NOT trusted for identity
    uploaded_files: List[UploadFile] = File(None),
    # Phase B.1 — structured clone_target form fields. UI dropdowns post these
    # instead of the user typing "test branch X of repo Y in project Z".
    clone_project: str = Form(None),
    clone_repo: str = Form(None),
    clone_branch: str = Form(None),
    # Phase B.2 — structured pipeline_target form fields.
    pipeline_project: str = Form(None),
    pipeline_id: int = Form(None),
    pipeline_branch: str = Form(None),
    selected_test_types: str = Form(None),
    test_scope: str = Form(None),
    target_url: str = Form(None),
    api_timeout_s: float = Form(None),
):
    """REST endpoint for orchestrator or direct user calls"""
    # Identity for the access check comes from the verified session, never from the
    # Form body — see shared/authz/agent_access.py::assert_agent_access_for_chat's
    # docstring and the WS handler's identical gate above.
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""
    _rest_pc = parse_pipeline_context(pipeline_context)
    _rest_project_id = _rest_pc.get("project_id") if isinstance(_rest_pc, dict) else None
    async with get_db_session_for_tenant(real_tenant_id) as _access_db:
        await assert_agent_access_for_chat(
            _access_db, tenant_id=real_tenant_id, project_id=_rest_project_id or "",
            user_id=real_user_id, agent_id="testing",
        )

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(user_id)
    set_agent_folder("orchestrator")  # match WS handler at line 138 — without this, files end up under the wrong folder
    input_directory = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input"
    output_directory = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"
    os.makedirs(input_directory, exist_ok=True)
    os.makedirs(output_directory, exist_ok=True)
    # Cleanup if session changed
    prev_session_id = _LAST_SESSION.get(user_id, "")
    if prev_session_id and prev_session_id != session_id:
        prev_dir = f"{esett.FILES}/{user_id}/orchestrator/{prev_session_id}"
        if os.path.exists(prev_dir):
            _rmtree(prev_dir)
        SESSION_STATES.pop(prev_session_id, None)
    _LAST_SESSION[user_id] = session_id
    # Save uploaded file (first only)
    input_file_path = None
    if uploaded_files:
        uploaded_file = uploaded_files[0]
        file_path = os.path.join(input_directory, uploaded_file.filename)
        try:
            with open(file_path, "wb") as f:
                f.write(await uploaded_file.read())
            input_file_path = file_path
        except Exception as e:
            return {"error": f"Failed to save {uploaded_file.filename}: {str(e)}"}
    # Orchestrator-aware state building
    state_messages = []
    raw_user_msg = user_message or ""  # fallback for direct API calls
    if messages:
        try:
            state_messages = json.loads(messages)
        except Exception:
            state_messages = []
        # Capture the last user-role message BEFORE context enrichment so that
        # short approval replies ("yes", "approve") survive the orchestrator's
        # context-wrapping step below.
        for m in reversed(state_messages):
            if isinstance(m, dict) and m.get("role") == "user":
                raw_user_msg = (m.get("content") or "").strip()
                break
    elif user_message:
        state_messages.append({"role": "user", "content": user_message})
    if conversation_context or task_intent or pipeline_context:
        final_msg = build_agent_input_text(
            conversation_context=conversation_context,
            task_intent=task_intent,
            pipeline_context=pipeline_context,
            pipeline_sections=("requirements", "design", "development", "testing"),
        )
        state_messages.append({"role": "user", "content": final_msg})
    # When an approval gate is active, use the raw short reply (e.g. "yes") rather
    # than the enriched context blob — _is_approval_response uses re.match at string
    # start and will never fire on a blob beginning with conversation history.
    _prev_state_peek = SESSION_STATES.get(session_id) or {}
    _gate_active = (
        _prev_state_peek.get("pending_testing_approval")
        or _prev_state_peek.get("pending_functional_approval")
    )
    if _gate_active and raw_user_msg and len(raw_user_msg) <= 200:
        actual_user_message = raw_user_msg
    else:
        actual_user_message = state_messages[-1]["content"] if state_messages else user_message
    # Run super agent (wrapped in timing for agent_call_log observability)
    try:
        previous_state = SESSION_STATES.get(session_id) or {}
        previous_state = _with_testing_selection(
            previous_state,
            selected_test_types=selected_test_types,
            test_scope=test_scope,
            target_url=target_url,
            api_timeout_s=api_timeout_s,
        ) or {}
        # Phase B.1 — inject structured clone_target if the form params were
        # supplied (e.g. by the UI's repo dropdown). Natural-language
        # "test branch X of repo Y" still works via classify_intent regex.
        if clone_repo and clone_branch:
            previous_state = dict(previous_state)
            previous_state["clone_target"] = {
                "project": clone_project or "",
                "repo": clone_repo,
                "branch": clone_branch,
            }
        if pipeline_id:
            previous_state = dict(previous_state)
            previous_state["pipeline_target"] = {
                "project": pipeline_project or (clone_project or ""),
                "pipeline_id": pipeline_id,
                "branch": pipeline_branch or "main",
            }
        # Phase 8.9d — see WS handler for rationale.
        if state_messages:
            previous_state = dict(previous_state)
            previous_state["orchestrator_chat_history"] = state_messages
        parsed_pipeline_context = parse_pipeline_context(pipeline_context)
        if parsed_pipeline_context:
            previous_state = dict(previous_state)
            previous_state["pipeline_context"] = parsed_pipeline_context
        # Attach AuditCallbackHandler per-invocation (D-01). Build initial state
        # and invoke _testing_app directly so we can pass config["callbacks"].
        # Same thread-isolation pattern as the WS handler (avoids event-loop poisoning).
        _lf_pid_rest = parsed_pipeline_context.get("project_id") if isinstance(parsed_pipeline_context, dict) else None
        # The REST path threaded NONE of these — not even the tenant, which the WS path
        # has always set. A run started here could not resolve the Azure DevOps
        # connector at all, so every standalone clone failed with "not configured"
        # regardless of how the organization was set up. Same three values, same reason
        # as the WS path above: credentials are per person, per project.
        previous_state = dict(previous_state or {})
        if real_tenant_id:
            previous_state["tenant_id"] = real_tenant_id
        if _lf_pid_rest:
            previous_state["project_id"] = _lf_pid_rest
        if real_user_id:
            previous_state["owner_id"] = real_user_id
        _audit_handler_rest = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=real_tenant_id or "")
        _lf_cbs_rest, _lf_meta_rest = langfuse_langchain_extras(session_id=session_id, agent_type="testing", project_id=_lf_pid_rest)
        from agents_orchestrator.testing_agent.agents.testing_agent import _initial_state as _build_initial_rest
        _initial_rest = _build_initial_rest(
            actual_user_message,
            input_file_path,
            previous_state if previous_state else None,
        )
        _alias_rest = await _stash_byok_model(real_tenant_id, _lf_pid_rest, previous_state)
        if _alias_rest:
            logger.info("testing: using BYOK model %s", _alias_rest)
        final_state = await asyncio.to_thread(
            _testing_app.invoke,
            _initial_rest,
            {"callbacks": [_audit_handler_rest, *_lf_cbs_rest], "metadata": _lf_meta_rest},
        )
        SESSION_STATES[session_id] = final_state
        # Phase 8.9a — see WS handler for rationale.
        responses = process_agent_stream_for_chat_display(
            final_state, orchestrator_driven=bool(conversation_context)
        )
        final_outputs = final_state.get("final_outputs", {})
        # Phase 3 — persist TestingArtifact + emit HandoffPayload(to=deployment) via the new handoff_router
        if (
            not final_state.get("awaiting_scope")
            and not final_state.get("pending_testing_approval")
            and not final_state.get("pending_functional_approval")
        ):
            await _emit_testing_handoff(session_id, final_outputs)
        # --- MODIFICATION START ---
        generated_files_list = []
        output_map = {
            'excel_plan_b64': 'Generated_Test_Plan.xlsx',
            'generated_code_py': 'Generated_Test_Code.py',
            'coverage_report_xml': 'Coverage_Report.xml'
        }

        for key, filename in output_map.items():
            if key in final_outputs:
                content = final_outputs[key]
                output_path = os.path.join(output_directory, filename)
                try:
                    if key.endswith('_b64'):
                        with open(output_path, 'wb') as f: f.write(base64.b64decode(content))
                    else:
                        with open(output_path, 'w', encoding='utf-8') as f: f.write(content)
                    # Store filename for zipping
                    generated_files_list.append(filename)
                except Exception as e:
                    print(f"ERROR: Failed to save artifact {filename}: {e}")

        # Phase 8.10c — UI artifacts written by Nodes/finalize.py + by
        # tools/ui_testing_agent.py. Same enumeration as the WS handler so
        # both transport paths produce identical zips.
        ui_extra_files_rest: list[tuple[str, str]] = []
        for ui_name in ("ui_test_results.xlsx", "ui_test_results.html", "ui_test_results.pdf"):
            ui_path = os.path.join(output_directory, ui_name)
            if os.path.isfile(ui_path):
                ui_extra_files_rest.append((ui_path, ui_name))
        ui_shots_dir_rest = os.path.join(output_directory, "ui_test_screenshots")
        if os.path.isdir(ui_shots_dir_rest):
            for fname in sorted(os.listdir(ui_shots_dir_rest)):
                if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                    full = os.path.join(ui_shots_dir_rest, fname)
                    if os.path.isfile(full):
                        ui_extra_files_rest.append((full, os.path.join("ui_test_screenshots", fname)))

        # If any files were generated, zip them up and create the response link
        final_generated_files_response = []
        if generated_files_list or ui_extra_files_rest:
            zip_filename = "testing_agent_reports.zip"
            zip_filepath = os.path.join(output_directory, zip_filename)

            print(f"DEBUG: Creating zip archive at {zip_filepath} for REST response")
            try:
                with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file_to_zip in generated_files_list:
                        full_path = os.path.join(output_directory, file_to_zip)
                        zipf.write(full_path, arcname=file_to_zip)
                    # Phase 8.10c — UI files (flat + screenshots subdirectory)
                    for full_path, arcname in ui_extra_files_rest:
                        zipf.write(full_path, arcname=arcname)

                print(f"DEBUG: Successfully created zip archive: {zip_filename}")

                # Replace the list of files with a single entry for the zip file
                final_generated_files_response.append({
                    "filename": zip_filename,
                    "download_url": f"/api/testing/download/{user_id}/{session_id}/{zip_filename}"
                })
            except Exception as e:
                print(f"ERROR: Failed to create zip archive for REST response: {e}")

        response_data = {
            "conversation_id": session_id,
            "responses": responses[-1] if responses else "No response generated.",
            "generated_files": final_generated_files_response
        }
        return response_data
        # --- MODIFICATION END ---
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@testing_router_orchestrator.get("/download/{user_id}/{session_id}/{filename}")
async def download_generated_file(user_id: str, session_id: str, filename: str):
    set_agent_folder("orchestrator")
    """Provides a secure way to download generated artifact files."""
    try:
        output_directory = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"
        file_path = os.path.join(output_directory, filename)
        
        if not os.path.abspath(file_path).startswith(os.path.abspath(output_directory)):
            raise HTTPException(status_code=403, detail="Access denied: Invalid file path.")

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found.")
        
        print(f"DEBUG: Serving file for download: {file_path}")
        return FileResponse(path=file_path, filename=filename, media_type='application/octet-stream')
    except Exception as e:
        print(f"ERROR: Error downloading file {filename}: {e}")
        if not isinstance(e, HTTPException):
            raise HTTPException(status_code=500, detail="An internal error occurred while trying to download the file.")
        raise e

@testing_router_orchestrator.get("/qa_report/{session_id}")
async def get_qa_report_html(request: Request, session_id: str, user_id: str = "default"):
    """Return the QA report HTML for inline browser rendering."""
    base = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output/qa_report.html"
    if not os.path.isfile(base):
        raise HTTPException(status_code=404, detail="QA report not yet generated")
    return FileResponse(base, media_type="text/html", headers=_browser_cors_headers(request))


@testing_router_orchestrator.get("/qa_report/{session_id}/pdf")
async def get_qa_report_pdf(request: Request, session_id: str, user_id: str = "default"):
    """Trigger PDF download. 404 if PDF wasn't produced (weasyprint failure)."""
    base = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output/qa_report.pdf"
    if not os.path.isfile(base):
        raise HTTPException(status_code=404, detail="QA report PDF unavailable (only HTML produced)")
    return FileResponse(
        base, media_type="application/pdf",
        filename=f"qa_report_{session_id}.pdf",
        headers=_browser_cors_headers(request),
    )


@testing_router_orchestrator.get("/files/{session_id}")
async def list_session_files(request: Request, session_id: str, user_id: str = "default"):
    """List every artifact in the session's output_dir for the View Test Files panel."""
    output_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"
    if not os.path.isdir(output_dir):
        return JSONResponse({"files": []}, headers=_browser_cors_headers(request))
    items = []
    for fname in sorted(os.listdir(output_dir)):
        fpath = os.path.join(output_dir, fname)
        if not os.path.isfile(fpath):
            continue
        st = os.stat(fpath)
        ext = os.path.splitext(fname)[1].lower()
        mime = {
            ".html": "text/html", ".xml": "application/xml", ".py": "text/plain",
            ".json": "application/json", ".pdf": "application/pdf",
            ".png": "image/png", ".jpg": "image/jpeg",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(ext, "application/octet-stream")
        items.append({
            "filename": fname,
            "size": st.st_size,
            "mime_type": mime,
            "last_modified": int(st.st_mtime),
            "download_url": _public_testing_url(
                f"/sdlc/agent/testing_orchestrator/files/"
                f"{quote(session_id)}/{quote(fname)}?user_id={quote(str(user_id))}"
            ),
        })
    return JSONResponse({"files": items}, headers=_browser_cors_headers(request))


@testing_router_orchestrator.get("/files/{session_id}/{filename}")
async def stream_session_file(request: Request, session_id: str, filename: str, user_id: str = "default"):
    """Stream a single artifact. Path-traversal-safe via os.path.basename."""
    safe = os.path.basename(filename)  # strip path separators
    fpath = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output/{safe}"
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(fpath, filename=safe, headers=_browser_cors_headers(request))


@testing_router_orchestrator.get("/sessions")
async def get_sessions():
    set_agent_folder("orchestrator")
    return {
        "current_session": "testing_agent"
    }


# ── Unit-test result + gated ADO PR ──────────────────────────────────────────
@testing_router_orchestrator.get("/unit-result/{session_id}")
async def get_unit_result(session_id: str):
    """Return the last unit run's coverage + generated files + PR url for the page."""
    from agents_orchestrator.testing_agent.config.unit_pr_store import get_unit_run
    data = get_unit_run(session_id)
    if not data:
        return {"available": False}
    return {
        "available": True,
        "coverage": data.get("coverage"),
        "results": data.get("results"),
        "generated_files": [
            {"path": f["path"], "bytes": len(f["contents"].encode("utf-8"))}
            for f in data.get("generated_files", [])
        ],
        "clone_target": data.get("clone_target"),
        "pr_url": data.get("pr_url"),
    }


@testing_router_orchestrator.post("/tests-pr/{session_id}")
async def open_tests_pr(session_id: str, request: Request):
    """Push the captured unit-test folder + COVERAGE.md to ADO and open a PR (GATED).
    Re-clones the base branch fresh (the run's temp clone is gone) and commits the
    stored file contents."""
    import asyncio as _asyncio
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    import uuid as _uuid

    from agents_orchestrator.testing_agent.config.unit_pr_store import get_unit_run, update_unit_run
    from shared.services import ado_repos

    data = get_unit_run(session_id)
    if not data or not data.get("generated_files"):
        raise HTTPException(status_code=400, detail="No generated unit tests for this session. Run unit tests first.")
    if data.get("pr_url"):
        return {"pr_url": data["pr_url"], "already": True}

    tenant_id = getattr(request.state, "tenant_id", "") or data.get("tenant_id") or ""
    ct = data.get("clone_target") or {}
    project, repo, branch = ct.get("project"), ct.get("repo"), ct.get("branch")
    if not (project and repo and branch):
        raise HTTPException(status_code=400, detail="Missing repo target for this run.")

    org_url, pat = await ado_repos.resolve_auth(tenant_id)
    remote_url = await ado_repos.resolve_clone_url(project, repo, pat=pat, org_url=org_url)
    if not remote_url:
        raise HTTPException(status_code=404, detail=f"Repo '{repo}' not found")

    work_dir = _os.path.join(_tempfile.gettempdir(), f"testing_pr_{_uuid.uuid4().hex[:8]}")
    try:
        await _asyncio.to_thread(ado_repos.clone_into, work_dir, remote_url, branch, pat)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone the branch: {exc}")

    files = [(f["path"], f["contents"]) for f in data["generated_files"]]
    if data.get("coverage_md"):
        files.append(("tests/COVERAGE.md", data["coverage_md"]))
    new_branch = f"tests/unit-{_uuid.uuid4().hex[:8]}"
    pr_title = f"Unit tests for {repo} ({branch})"
    cov = data.get("coverage") or {}
    pct = cov.get("coverage_pct")
    pr_desc = (
        "Generated unit tests from the SDLC Testing agent.\n\n"
        + (f"**Line coverage:** {pct:.1f}%\n\n" if isinstance(pct, (int, float)) else "")
        + (data.get("coverage_md") or "")
    )
    try:
        await _asyncio.to_thread(
            ado_repos.commit_and_push_files, work_dir, branch, new_branch, files, pat, pr_title
        )
        pr_url = await ado_repos.create_pull_request(
            project, repo, new_branch, branch, pr_title, pr_desc, pat=pat, tenant_id=tenant_id
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not open tests PR: {str(exc)[:400]}")
    finally:
        _shutil.rmtree(work_dir, ignore_errors=True)

    if not pr_url:
        raise HTTPException(status_code=502, detail="Pushed the branch but the PR could not be created.")
    update_unit_run(session_id, pr_url=pr_url)
    return {"pr_url": pr_url, "files": len(files), "branch": new_branch}


# ── Phase B.1 — Azure Repos endpoints (ported from legacy testing_agent_api) ──
import httpx as _httpx


async def _get_ado_creds() -> dict:
    """This tenant's ADO credentials, or a 400 telling them to connect it.

    Async because the credentials live in the tenant secret store, not in process
    configuration — there is no platform-wide PAT to read synchronously.
    """
    from shared.services.ado_repos import resolve_auth

    org_url, pat = await resolve_auth()
    if not (org_url and pat):
        raise HTTPException(
            status_code=400,
            detail="Azure DevOps is not connected for your organization. "
                   "Connect it on the Integrations page.",
        )
    return {"org_url": org_url, "pat": pat}


@testing_router_orchestrator.get("/repos/list")
async def list_repos(project: str):
    """List all git repos in an ADO project."""
    if not project.strip():
        raise HTTPException(status_code=400, detail="project is required.")
    creds = await _get_ado_creds()
    url = f"{creds['org_url'].rstrip('/')}/{project}/_apis/git/repositories?api-version=7.1"
    try:
        async with _httpx.AsyncClient(timeout=30, auth=("", creds["pat"])) as client:
            r = await client.get(url)
            r.raise_for_status()
            repos = [
                {"id": rp["id"], "name": rp["name"], "default_branch": rp.get("defaultBranch", "").replace("refs/heads/", "")}
                for rp in r.json().get("value", [])
            ]
        return {"repos": repos}
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


@testing_router_orchestrator.get("/repos/branches")
async def list_branches(project: str, repo: str):
    """List branches for a repository."""
    if not project.strip() or not repo.strip():
        raise HTTPException(status_code=400, detail="project and repo are required.")
    creds = await _get_ado_creds()
    url = f"{creds['org_url'].rstrip('/')}/{project}/_apis/git/repositories/{repo}/refs?filter=heads/&api-version=7.1"
    try:
        async with _httpx.AsyncClient(timeout=30, auth=("", creds["pat"])) as client:
            r = await client.get(url)
            r.raise_for_status()
            branches = [
                {"name": b["name"].replace("refs/heads/", ""), "commit": b.get("objectId", "")}
                for b in r.json().get("value", [])
            ]
        return {"branches": branches}
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


@testing_router_orchestrator.get("/repos/pull-requests")
async def list_pull_requests(project: str, repo: str, status: str = "active"):
    """List pull requests for a repository."""
    if not project.strip() or not repo.strip():
        raise HTTPException(status_code=400, detail="project and repo are required.")
    creds = await _get_ado_creds()
    url = (
        f"{creds['org_url'].rstrip('/')}/{project}/_apis/git/repositories/{repo}"
        f"/pullrequests?searchCriteria.status={status}&api-version=7.1"
    )
    try:
        async with _httpx.AsyncClient(timeout=30, auth=("", creds["pat"])) as client:
            r = await client.get(url)
            r.raise_for_status()
            prs = [
                {
                    "id": pr["pullRequestId"],
                    "title": pr["title"],
                    "source_branch": pr["sourceRefName"].replace("refs/heads/", ""),
                    "target_branch": pr["targetRefName"].replace("refs/heads/", ""),
                    "created_by": pr.get("createdBy", {}).get("displayName", ""),
                    "status": pr["status"],
                }
                for pr in r.json().get("value", [])
            ]
        return {"pull_requests": prs}
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


# ── Phase B.2 — Azure Pipelines endpoints ─────────────────────────────────────

@testing_router_orchestrator.get("/pipelines/list")
async def list_pipelines(project: str):
    """List all pipelines in the given ADO project."""
    if not project.strip():
        raise HTTPException(status_code=400, detail="project is required.")
    creds = await _get_ado_creds()
    url = f"{creds['org_url'].rstrip('/')}/{project}/_apis/pipelines?api-version=7.1"
    try:
        async with _httpx.AsyncClient(timeout=30, auth=("", creds["pat"])) as client:
            r = await client.get(url)
            r.raise_for_status()
            pipelines = [
                {"id": p["id"], "name": p["name"], "folder": p.get("folder", "")}
                for p in r.json().get("value", [])
            ]
        return {"pipelines": pipelines}
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


@testing_router_orchestrator.post("/pipelines/trigger")
async def trigger_pipeline(project: str, pipeline_id: int, branch: str = "main"):
    """Trigger a pipeline run on the given branch."""
    if not project.strip():
        raise HTTPException(status_code=400, detail="project is required.")
    creds = await _get_ado_creds()
    url = f"{creds['org_url'].rstrip('/')}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1"
    body = {"resources": {"repositories": {"self": {"refName": f"refs/heads/{branch}"}}}}
    try:
        async with _httpx.AsyncClient(timeout=30, auth=("", creds["pat"])) as client:
            r = await client.post(url, json=body)
            r.raise_for_status()
            data = r.json()
        return {
            "run_id": data.get("id"),
            "state": data.get("state"),
            "url": data.get("_links", {}).get("web", {}).get("href", ""),
        }
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


@testing_router_orchestrator.get("/pipelines/status")
async def pipeline_run_status(project: str, pipeline_id: int, run_id: int):
    """Get the current status of a pipeline run."""
    if not project.strip():
        raise HTTPException(status_code=400, detail="project is required.")
    creds = await _get_ado_creds()
    url = f"{creds['org_url'].rstrip('/')}/{project}/_apis/pipelines/{pipeline_id}/runs/{run_id}?api-version=7.1"
    try:
        async with _httpx.AsyncClient(timeout=30, auth=("", creds["pat"])) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
        return {
            "run_id": data.get("id"),
            "state": data.get("state"),
            "result": data.get("result"),
            "url": data.get("_links", {}).get("web", {}).get("href", ""),
        }
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


@testing_router_orchestrator.post("/repos/generate-scripts")
async def generate_test_scripts(project: str, repo: str, branch: str = "main", work_item_id: int = None):
    """Fetch the latest file tree from a branch and generate test scripts via the testing agent.
    Optionally link to a work item for context."""
    if not project.strip() or not repo.strip():
        raise HTTPException(status_code=400, detail="project and repo are required.")
    creds = await _get_ado_creds()
    org_url = creds["org_url"].rstrip("/")
    pat = creds["pat"]

    try:
        async with _httpx.AsyncClient(timeout=30, auth=("", pat)) as client:
            repo_resp = await client.get(
                f"{org_url}/{project}/_apis/git/repositories/{repo}?api-version=7.1"
            )
            repo_resp.raise_for_status()
            repo_id = repo_resp.json()["id"]

            items_resp = await client.get(
                f"{org_url}/{project}/_apis/git/repositories/{repo_id}/items"
                f"?scopePath=/&recursionLevel=OneLevel&versionDescriptor.version={branch}"
                f"&versionDescriptor.versionType=branch&api-version=7.1"
            )
            items_resp.raise_for_status()
            items = items_resp.json().get("value", [])

        file_tree = [
            {"path": item["path"], "type": "folder" if item.get("isFolder") else "file"}
            for item in items
        ]
        context = (
            f"Repository: {repo} | Branch: {branch} | Project: {project}\n"
            f"Work item: {work_item_id or 'not specified'}\n\n"
            f"Top-level file tree:\n" +
            "\n".join(f"  {'📁' if i['type'] == 'folder' else '📄'} {i['path']}" for i in file_tree)
        )
        return {
            "status": "ok",
            "repo": repo,
            "branch": branch,
            "file_tree": file_tree,
            "agent_context": context,
            "message": "File tree retrieved. Pass agent_context to the testing agent WebSocket to generate scripts.",
        }
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
