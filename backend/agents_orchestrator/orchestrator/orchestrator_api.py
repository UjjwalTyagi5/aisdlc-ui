import uuid
import os
import re
import json
import httpx
from datetime import datetime
from typing import Dict, Any, List, Annotated
from langchain_litellm import ChatLiteLLM
from langgraph.graph import StateGraph, END
from config.checkpoint import build_checkpointer as _build_checkpointer
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from dotenv import load_dotenv
from fastapi import APIRouter, Form, Request, Response, Cookie, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi import FastAPI
from config import sdlcSettings
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE, AGENTIC_BASE_URL, AGENTIC_INTERNAL_BASE_URL, AGENTIC_WS_URL, ANTHROPIC_MODEL
from config.pipeline_context import (
    build_pipeline_context,
    empty_pipeline_context,
    pipeline_context_json,
)
from config.orchestrator_state_client import (
    get_state as get_orch_state,
    set_state as set_orch_state,
    clear_state as clear_orch_state,
    fetch_session_artifacts,
    clear_session_artifacts,
    parse_handoff,
    strip_handoff,
)
import re as _re
import difflib as _difflib

# Affirmative phrases that confirm a pending stage gate ("Continue to Development?")
_AFFIRMATIVE_RE = _re.compile(
    r"^(yes|y|go|continue|proceed|ok|okay|sure|do it|yep|yeah|please do)\b",
    _re.IGNORECASE,
)

# Pure greetings â€” always bypass any active-agent lock and route via LLM (which sends them to ingestion)
_GREETING_RE = _re.compile(
    r"^(hi|hello|hey|good\s+morning|good\s+afternoon|good\s+evening|howdy|greetings|sup|what'?s\s+up)[!?,.\s]*$",
    _re.IGNORECASE,
)

# Phrases that mean "reset everything and start from the beginning".
_START_OVER_RE = _re.compile(
    r"\b(start\s+over|reset|begin\s+again|restart|new\s+session|clear\s+all|"
    r"start\s+fresh|start\s+new|wipe|forget\s+everything)\b",
    _re.IGNORECASE,
)

# Keywords that signal the user wants to change stage â€” bypass the active-agent lock
# so the orchestrator re-routes instead of staying in the current agent.
_STAGE_CHANGE_RE = _re.compile(
    r"\b(design|architect|architecture|hld|lld|c4|api design|api contract|database design|"
    r"develop|coding|implementation|start coding|write code|build|implement|"
    r"test|testing|qa|quality assurance|unit test|integration test|"
    r"deploy|deployment|release|pipeline|"
    r"requirement|user stor|epic|feature|backlog|"
    r"new project|different project|switch to|go to)\b",
    _re.IGNORECASE,
)

_EXPLICIT_DEVELOPMENT_RE = _re.compile(
    r"\b("
    r"start\s+(?:the\s+)?development|start\s+dev|proceed\s+(?:with|to)\s+development|"
    r"go\s+to\s+dev|go\s+to\s+development|skip\s+design|start\s+coding|"
    r"write\s+code|implement(?:ation)?|build\s+(?:it|the\s+code)|"
    r"create\s+(?:a\s+)?pr|open\s+(?:a\s+)?pr|raise\s+(?:a\s+)?pr|"
    r"create\s+(?:a\s+)?pull\s+request|open\s+(?:a\s+)?pull\s+request|"
    r"raise\s+(?:a\s+)?pull\s+request|draft\s+(?:a\s+)?pr|draft\s+(?:a\s+)?pull\s+request"
    r")\b",
    _re.IGNORECASE,
)

_EXPLICIT_DEPLOYMENT_RE = _re.compile(
    r"\b("
    r"deploy(?:ment)?|release|"
    r"start\s+(?:the\s+)?deploy(?:ment)?|"
    r"proceed\s+(?:with|to)\s+deploy(?:ment)?|"
    r"go\s+to\s+deploy(?:ment)?|"
    r"do\s+(?:the\s+)?deploy(?:ment)?|"
    r"generate\s+deployment\s+assets|run\s+(?:the\s+)?pipeline"
    r")\b",
    _re.IGNORECASE,
)


def _has_deployment_intent(text: str) -> bool:
    """Detect deployment requests without binding routing to one exact typo."""
    if _EXPLICIT_DEPLOYMENT_RE.search(text or ""):
        return True
    tokens = _re.findall(r"[a-zA-Z]{5,}", (text or "").lower())
    deployment_terms = ("deploy", "deployment")
    return any(
        _difflib.get_close_matches(token, deployment_terms, n=1, cutoff=0.84)
        for token in tokens
    )


# Friendly gate question per next-stage value emitted in HANDOFF.to
_GATE_QUESTIONS = {
    "design": "Ready to generate the system architecture and design documents?",
    "development": "Ready to generate code for these stories?",
    "testing": "Ready to run tests against the generated code?",
    "deployment": "Ready to generate deployment assets?",
    "monitoring_feedback": "Ready to set up monitoring and feedback?",
}
# from google import genai
# from google.genai import types
import shutil
import os
from pathlib import Path
import asyncio
import json
import websockets
import logging
from uuid import uuid4
from config.connection_manager import manager
from contextvars import ContextVar
from config.auth.jwt import create_access_token

# Per-chat-turn internal auth headers for orchestrator → sibling-agent HTTP calls.
# Those endpoints are mounted with require_permission("artifact:view"); the
# orchestrator runs server-side and must present a bearer token. We mint a
# short-lived token scoped to the SAME user + tenant (no privilege escalation —
# the caller already holds artifact:view to reach the orchestrator). Set once per
# turn in process_orchestrator_message_ws; ContextVar propagates into graph nodes.
_internal_auth_headers_var: ContextVar[dict] = ContextVar("internal_auth_headers", default={})


def _internal_agent_headers() -> dict:
    """Authorization headers for internal agent-to-agent HTTP calls (or {} if unset)."""
    return _internal_auth_headers_var.get()
import os
import json
from typing import List
from docx import Document
import PyPDF2
import logging
# Set up logging. INFO (not DEBUG) so the root logger doesn't flood the console with
# third-party debug spam (LiteLLM emits ~6 lines per streamed token; httpx/mcp/passlib
# are chatty too). App loggers can still opt into DEBUG individually.
logging.basicConfig(level=logging.INFO)
# Quiet the noisiest third-party loggers regardless of root level — these have no
# diagnostic value in normal operation and bury the app's own logs.
for _noisy in ("LiteLLM", "litellm", "httpx", "httpcore", "passlib", "mcp", "mcp.client.streamable_http"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from shared.services.conversation_service import persist_turn  # noqa: E402
from shared.observability import langfuse_langchain_extras  # noqa: E402

esett = sdlcSettings()
orchestrator_router = APIRouter()

# WebSocket connection manager for orchestrator
class OrchestratorConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.session_connections: Dict[str, WebSocket] = {}
       
    async def connect(self, websocket: WebSocket, session_id: str = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if session_id:
            self.session_connections[session_id] = websocket
        logger.info(f"WebSocket connected. Session: {session_id}")
       
    def disconnect(self, websocket: WebSocket, session_id: str = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if session_id and session_id in self.session_connections:
            del self.session_connections[session_id]
        logger.info(f"WebSocket disconnected. Session: {session_id}")
       
    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
            logger.debug(f"Sent personal message: {message[:100]}...")
        except Exception as e:
            logger.error(f"Failed to send personal message: {e}")
           
    async def send_to_session(self, message: dict, session_id: str):
        if session_id in self.session_connections:
            websocket = self.session_connections[session_id]
            try:
                await websocket.send_text(json.dumps(message))
                logger.debug(f"Sent to session {session_id}: {message.get('type', 'unknown')}")
            except Exception as e:
                logger.error(f"Failed to send to session {session_id}: {e}")
               
    async def broadcast_to_session(self, message: dict, session_id: str):
        await self.send_to_session(message, session_id)

# Initialize connection manager
orchestrator_manager = OrchestratorConnectionManager()

# Map agent names to their WebSocket URLs
AGENT_WS_MAP = {
    "ingestion": f"{AGENTIC_WS_URL}/sdlc/agent/ingestion/ws",
    "design": f"{AGENTIC_WS_URL}/sdlc/agent/design/ws",
    "development": f"{AGENTIC_WS_URL}/sdlc/agent/development/ws",
    "testing": f"{AGENTIC_WS_URL}/sdlc/agent/testing/ws",
    "deployment": f"{AGENTIC_WS_URL}/sdlc/agent/deployment/ws",
    "monitoring": f"{AGENTIC_WS_URL}/sdlc/agent/monitoring/ws",
}

# Color mapping for agents (optional, used by frontend to style pipeline)
agent_colors = {
   "ingestion": "bg-blue-500",
   "design": "bg-purple-500",
   "development": "bg-green-500",
   "testing": "bg-orange-500",
   "deployment": "bg-red-500",
   "monitoring": "bg-indigo-500",
   "orchestrator": "bg-gray-500",
}

# Agent display info
AGENT_DISPLAY_NAMES = {
    "ingestion": "Ingestion Agent",
    "design": "Design & Architecture Agent",
    "development": "Development Agent",
    "testing": "Testing Agent",
    "deployment": "Deployment Agent",
    "monitoring": "Monitoring Agent",
}

AGENT_COLORS = {
    "ingestion": "bg-blue-500",
    "design": "bg-purple-500",
    "development": "bg-green-500",
    "testing": "bg-orange-500",
    "deployment": "bg-red-500",
    "monitoring": "bg-indigo-500",
}

# In-memory per-session, per-agent counters for unique agent invocation IDs
agent_invocation_counters: Dict[str, Dict[str, int]] = {}

def get_agent_invocation_id(session_id: str, agent_name: str) -> str:
    """
    Generates a unique agent invocation ID per session and agent,
    incrementing the per-agent count on each invocation.
    """
    if session_id not in agent_invocation_counters:
        agent_invocation_counters[session_id] = {}
    if agent_name not in agent_invocation_counters[session_id]:
        agent_invocation_counters[session_id][agent_name] = 0
    agent_invocation_counters[session_id][agent_name] += 1
    count = agent_invocation_counters[session_id][agent_name]
    return f"{session_id}_{agent_name}_{count}"

async def notify_agent_routing(session_id: str, agent_name: str, stage: str):
    """
    Notify frontend when an agent is routed.
    """
    agent_id = create_agent_invocation_id(session_id, agent_name)
    routing_message = {
        "type": "agent_joining",
        "agent": {
            "id": agent_id,
            "name": agent_name,
            "status": "joining",
            "stage": stage,
            "color": agent_colors.get(agent_name.lower(), "bg-blue-500"),
            "progress": 0,
            "startTime": datetime.utcnow().isoformat()
        },
        "routing_info": {
            "from": "orchestrator",
            "to": agent_name,
            "stage": stage,
            "decision_time": datetime.utcnow().isoformat()
        }
    }
    logger.debug(f"[notify_agent_routing] session={session_id}, agent={agent_name}, id={agent_id}")
    await manager.broadcast(routing_message)
    await orchestrator_manager.broadcast_to_session(routing_message, session_id)

async def notify_agent_progress(session_id: str, agent_name: str, progress: int, status: str = "joining"):
   """
   Notify frontend of agent progress updates.
   """
   agent_id = get_current_agent_invocation_id(session_id, agent_name)
   if not agent_id:
       agent_id = f"{session_id}_{agent_name}_1"  # fallback
   progress_message = {
       "type": "agent_progress",
       "agent_id": agent_id,
       "progress": progress,
       "status": status,

   }
   await manager.broadcast(progress_message)
   logger.debug(f"[notify_agent_progress] session={session_id}, agent={agent_name}, id={agent_id}, progress={progress}")
   await orchestrator_manager.broadcast_to_session(progress_message, session_id)

async def notify_agent_completion(session_id: str, agent_name: str, stage: str, success: bool = True):
    """
    Notify frontend when an agent has completed its task.
    """
    agent_id = get_current_agent_invocation_id(session_id, agent_name)
    if not agent_id:
        agent_id = f"{session_id}_{agent_name}_1"  # fallback
    completion_message = {
        "type": "agent_completed",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "stage": stage,
        "success": success,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await manager.broadcast(completion_message)
    logger.debug(f"[notify_agent_completion] session={session_id}, agent={agent_name}, id={agent_id}, success={success}")
    await orchestrator_manager.broadcast_to_session(completion_message, session_id)
# Note: Remove notify_agent_routing call from AgentWebSocketClient.connect() to avoid duplicate join notifications!

# WebSocket client for connecting to individual agents (no routing notification here)
class AgentWebSocketClient:
    def __init__(self, agent_name: str, session_id: str):
        self.agent_name = agent_name
        self.session_id = session_id
        self.websocket = None
        self.is_connected = False
        self.response_received = False
        self.agent_response = None
        self.start_time = None
       
    async def connect(self):
        try:
            if self.agent_name in AGENT_WS_MAP:
                logger.info(f"Attempting to connect to {self.agent_name} at {AGENT_WS_MAP[self.agent_name]}")
                self.start_time = datetime.utcnow()
                # Set connection timeout
                self.websocket = await asyncio.wait_for(
                    websockets.connect(AGENT_WS_MAP[self.agent_name]), 
                    timeout=10.0
                )
                self.is_connected = True
                logger.info(f"Successfully connected to {self.agent_name} WebSocket")
                await notify_agent_progress(self.session_id, self.agent_name, 25, "active")
                return True
        except asyncio.TimeoutError:
            logger.error(f"Timeout connecting to {self.agent_name} WebSocket")
            await notify_agent_completion(self.session_id, self.agent_name, False)
            return False
        except Exception as e:
            logger.error(f"Failed to connect to {self.agent_name} WebSocket: {e}")
            self.is_connected = False
            await notify_agent_completion(self.session_id, self.agent_name, False)
            return False
   
    async def send_message(self, message_data: dict):
        if not self.websocket or not self.is_connected:
            logger.error(f"Cannot send message to {self.agent_name}: Not connected")
            return False
            
        try:
            await notify_agent_progress(self.session_id, self.agent_name, 50, "active")
            
            message_json = json.dumps(message_data)
            await self.websocket.send(message_json)
            logger.info(f"Successfully sent message to {self.agent_name}")
            logger.debug(f"Message content: {message_json[:200]}...")
            
            await notify_agent_progress(self.session_id, self.agent_name, 75, "active")
            
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {self.agent_name}: {e}")
            self.is_connected = False
            await notify_agent_completion(self.session_id, self.agent_name, False)
            return False

    async def listen_for_updates(self, stop_on_response: bool = True, recv_timeout: float = 30.0):
        if not self.websocket or not self.is_connected:
            logger.error(f"Cannot listen to {self.agent_name}: Not connected")
            return
        logger.info(f"Starting to listen for updates from {self.agent_name}")
        await notify_agent_progress(self.session_id, self.agent_name, 90, "active")
        try:
            while self.is_connected and not self.response_received:  # ADD: Check response_received
                try:
                    message = await asyncio.wait_for(self.websocket.recv(), timeout=recv_timeout)
                except asyncio.TimeoutError:
                    logger.debug(f"No message received from {self.agent_name} within {recv_timeout}s")
                    if stop_on_response and self.response_received:  # Break on timeout if response already received
                        break
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"{self.agent_name} WebSocket connection closed while listening: {e}")
                    self.is_connected = False
                    break
                except Exception as e:
                    logger.error(f"Error receiving from {self.agent_name}: {e}")
                    self.is_connected = False
                    break
                logger.info(f"Received message from {self.agent_name}: {str(message)[:200]}...")
                # Process message...
                try:
                    agent_data = json.loads(message)
                except json.JSONDecodeError:
                    # Handle raw message...
                    continue
                # Check if this is a final response
                is_final = False
                if (agent_data.get("type") == "response" or 
                    "response" in agent_data or 
                    agent_data.get("status") in ("done", "completed")):
                    is_final = True
                if is_final:
                    self.agent_response = agent_data
                    self.response_received = True
                    logger.info(f"Received final response from {self.agent_name}")
                    await notify_agent_completion(self.session_id, self.agent_name, True)
                    # Send response immediately
                    final_response = agent_data.get("message", 
                                    agent_data.get("response", 
                                    agent_data.get("text", "Agent completed")))
                    try:
                        await manager.send_agent_response(f"{self.agent_name.title()} Agent", final_response, self.session_id)
                    except Exception as e:
                        logger.warning(f"Failed to send final response to manager: {e}")
                    if stop_on_response:
                        logger.info(f"Stopping listener for {self.agent_name} after receiving response")
                        break  # IMPORTANT: Break immediately after response
        finally:
            logger.info(f"Stopped listening to {self.agent_name}")
            if not self.response_received:
                await notify_agent_completion(self.session_id, self.agent_name, False)
 
                   
   
    async def disconnect(self):
        if self.websocket:
            try:
                await self.websocket.close()
                logger.info(f"Disconnected from {self.agent_name} WebSocket")
            except Exception as e:
                logger.error(f"Error disconnecting from {self.agent_name}: {e}")
        self.is_connected = False


# ---- Define Orchestrator State ----

class OrchestratorState(Dict[str, Any]):
    user_input: str
    stage: str
    messages: List[BaseMessage]
    session_id: str
    user_id: str
    provider_kind: str
    history: List[str]
    files_meta: List[Dict[str, Any]]
    artifact_map: Dict[str, List[str]]
    pipeline_context: Dict[str, Any]
    # Declared so it survives the decide -> orchestrate node hop (undeclared keys are
    # dropped by LangGraph). Carries a user-facing reason when routing fails (e.g. no model).
    routing_error: str
    user_id: str
    session_id: str
    tenant_id: str
    model_id: str | None

example_output = {
    "ingestion": [
        "The user wants to ingest approved Azure Boards work items and normalize them for downstream agents"
    ],
    "development": [],
    "testing": [],
    "deployment": [],
    "monitoring_feedback": []
}

def get_agent_path(agent_name: str, session_id: str, user_id: str) -> str:
    base_path = f"{esett.FILES}/{user_id}/{agent_name}_agent/{session_id}"
    os.makedirs(base_path, exist_ok=True)
    return base_path


def save_agent_output(
    agent_name: str,
    session_id: str,
    user_id: str,
    output: str,
    files_meta: List[Dict]
) -> List[Dict]:
    """
    Save agent output to file (status/text) and update files_meta.
    Also writes/updates a metadata.json file in the agent's output folder.
    """

    output_dir = f"{esett.FILES}/{user_id}/{agent_name}_agent/{session_id}/output"
    os.makedirs(output_dir, exist_ok=True)

    if not output or output.strip() == "":
        return files_meta

    meta_entry = None

    # --- 1. Detect if agent message mentions a saved file (docx/pdf/txt) ---
    match = re.search(r'"([^"]+\.(docx|pdf|txt))"', output)
    if match:
        filename = match.group(1)
        file_path = os.path.join(output_dir, filename)

        if os.path.exists(file_path):
            file_type = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if filename.endswith(".docx") else
                "application/pdf"
                if filename.endswith(".pdf") else
                "text/plain"
            )

            meta_entry = {
                "name": filename,
                "path": file_path,
                "type": file_type,
                "created_at": datetime.now().isoformat()
            }
            files_meta.append(meta_entry)

    # --- 2. Otherwise, save raw text/status as .txt ---
    if not meta_entry:
        if "successfully" in output.lower():
            filename = f"{agent_name}_status.txt"
        else:
            filename = f"{agent_name}_output.txt"

        file_path = os.path.join(output_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(output)

        meta_entry = {
            "name": filename,
            "path": file_path,
            "type": "text/plain",
            "created_at": datetime.now().isoformat()
        }
        files_meta.append(meta_entry)

    # --- 3. Always update metadata.json ---
    _update_metadata_file(output_dir, files_meta)

    return files_meta

def create_agent_invocation_id(session_id: str, agent_name: str) -> str:
    """
    Increment and return a new invocation id for (session, agent).
    Called once when routing to the agent.
    """
    
    if session_id not in agent_invocation_counters:
        agent_invocation_counters[session_id] = {}
    if agent_name not in agent_invocation_counters[session_id]:
        agent_invocation_counters[session_id][agent_name] = 0
    agent_invocation_counters[session_id][agent_name] += 1
    count = agent_invocation_counters[session_id][agent_name]
    return f"{session_id}_{agent_name}_{count}"

def get_current_agent_invocation_id(session_id: str, agent_name: str) -> str:
    """
    Return the current invocation id without incrementing.
    Use this for progress/completion events.
    """

    if session_id in agent_invocation_counters and agent_name in agent_invocation_counters[session_id]:
        count = agent_invocation_counters[session_id][agent_name]
        return f"{session_id}_{agent_name}_{count}"
    return None




def _update_metadata_file(output_dir: str, files_meta: List[Dict]):
    """
    Write the current files_meta into metadata.json in output_dir.
    """
    metadata_path = os.path.join(output_dir, "metadata.json")

    try:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(files_meta, f, indent=2)
        logger.info(f"Updated metadata file at {metadata_path}")
    except Exception as e:
        logger.error(f"Failed to update metadata.json in {output_dir}: {e}")


# â”€â”€ Artifact context formatters â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Each formatter converts a raw artifact value (dict or legacy string) into a
# concise text block suitable for inclusion in an agent's conversation_context.
# A hard char cap prevents runaway context sizes.

_ARTIFACT_CHAR_CAP = 4_000   # per-artifact cap before truncation
_STORY_CAP = 20               # max stories to include
_AC_PER_STORY_CAP = 3         # max acceptance criteria lines per story
_LIST_CAP = 10                # max items for gaps/constraints lists
_SECTION_CHAR_CAP = 1_500     # max chars per design section


def _format_requirements_context(req_payload) -> str:
    if not req_payload:
        return (
            "[No requirements artifact found for this session. "
            "The Requirements agent has not completed or persisted its output yet. "
            "Ask the user to run the Requirements agent first, or provide requirements manually.]"
        )
    if isinstance(req_payload, str):
        return req_payload[:_ARTIFACT_CHAR_CAP]
    try:
        lines: list[str] = []
        stories = req_payload.get("stories", [])
        if stories:
            lines.append("## User Stories")
            for s in stories[:_STORY_CAP]:
                lines.append(f"- [{s.get('id', '')}] {s.get('title', '')}: {s.get('description', '')}")
                for ac in (s.get("acceptance_criteria") or [])[:_AC_PER_STORY_CAP]:
                    lines.append(f"  AC: {ac}")
        gap_report = req_payload.get("gap_report", "")
        if gap_report:
            lines.append(f"\n## Open Questions / Gaps\n{str(gap_report)[:500]}")
        out_of_scope = req_payload.get("out_of_scope", [])
        if out_of_scope:
            lines.append("\n## Out of Scope")
            lines.extend(f"- {c}" for c in out_of_scope[:_LIST_CAP])
        assumptions = req_payload.get("assumptions", [])
        if assumptions:
            lines.append("\n## Assumptions")
            lines.extend(f"- {a}" for a in assumptions[:_LIST_CAP])
        result = "\n".join(lines)
        return result[:_ARTIFACT_CHAR_CAP] if result else "[Requirements payload exists but contains no stories, gaps, or constraints]"
    except Exception:
        return str(req_payload)[:_ARTIFACT_CHAR_CAP]


# Sections of design_artifacts that the Development agent needs.
_DESIGN_DEV_SECTIONS = [
    ("lld", "## Low-Level Design (LLD)"),
    ("api_contract", "## API Contracts"),
    ("database_schema", "## Database Schema"),
    ("tech_stack", "## Technology Stack & Infrastructure"),
    ("adrs", "## Architecture Decision Records (ADRs)"),
]


def _format_design_context(design_artifacts) -> str:
    if not design_artifacts:
        return (
            "[No design artifact found for this session. "
            "The Design agent has not completed or persisted its output yet. "
            "Ask the user to run the Design agent first.]"
        )
    if isinstance(design_artifacts, str):
        return design_artifacts[:_ARTIFACT_CHAR_CAP]
    try:
        blocks: list[str] = []
        for key, label in _DESIGN_DEV_SECTIONS:
            content = design_artifacts.get(key, "")
            if content:
                blocks.append(f"{label}\n{str(content)[:_SECTION_CHAR_CAP]}")
        if not blocks:
            # Sections not yet parsed (pre-Phase-6) â€” fall back to raw dump
            return str(design_artifacts)[:_ARTIFACT_CHAR_CAP]
        return ("\n\n".join(blocks))[:_ARTIFACT_CHAR_CAP]
    except Exception:
        return str(design_artifacts)[:_ARTIFACT_CHAR_CAP]


def _format_development_context(dev_artifacts) -> str:
    if not dev_artifacts:
        return (
            "[No development artifact found for this session. "
            "The Development agent has not completed or persisted its output yet.]"
        )
    if isinstance(dev_artifacts, str):
        return dev_artifacts[:_ARTIFACT_CHAR_CAP]
    try:
        lines: list[str] = []
        for key, label in (
            ("status", "## Status"),
            ("branch_name", "## Branch"),
            ("repo_url", "## Repository"),
            ("pr_url", "## Pull Request"),
            ("changed_files", "## Changed Files"),
            ("generated_files", "## Generated Files"),
        ):
            val = dev_artifacts.get(key, "")
            if val:
                lines.append(f"{label}\n{str(val)[:_SECTION_CHAR_CAP]}")
        result = "\n\n".join(lines)
        return result[:_ARTIFACT_CHAR_CAP] if result else "[Development artifact exists but contains no structured fields]"
    except Exception:
        return str(dev_artifacts)[:_ARTIFACT_CHAR_CAP]


def _format_testing_context(testing_artifacts) -> str:
    if not testing_artifacts:
        return "[No testing artifact found. The Testing agent has not completed or persisted its output yet.]"
    if isinstance(testing_artifacts, str):
        return testing_artifacts[:_ARTIFACT_CHAR_CAP]
    try:
        lines: list[str] = []
        status = testing_artifacts.get("status", "")
        if status:
            lines.append(f"## Status\n{status}")
        te = testing_artifacts.get("test_execution") or {}
        if te:
            lines.append(
                f"## Test Execution\n"
                f"Total: {te.get('total', 0)} | "
                f"Passed: {te.get('passed', 0)} | "
                f"Failed: {te.get('failed', 0)} | "
                f"Errors: {te.get('errors', 0)} | "
                f"Skipped: {te.get('skipped', 0)}"
            )
        cov = testing_artifacts.get("coverage") or {}
        if cov:
            cov_pct = cov.get("coverage_pct", "N/A")
            branch_pct = cov.get("branch_coverage_pct")
            lines.append(
                f"## Coverage\n{cov_pct}% line"
                + (f" / {branch_pct}% branch" if branch_pct is not None else "")
            )
        summary = testing_artifacts.get("summary_md", "")
        if summary:
            lines.append(f"## Summary\n{str(summary)[:_SECTION_CHAR_CAP]}")
        result = "\n\n".join(lines)
        return result[:_ARTIFACT_CHAR_CAP] if result else "[Testing artifact exists but contains no structured fields]"
    except Exception:
        return str(testing_artifacts)[:_ARTIFACT_CHAR_CAP]


# â”€â”€ File content helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ---- Decide Stage (ENHANCED with routing notifications) ----
def extract_text_from_docx(docx_path: str) -> str:
    try:
        doc = Document(docx_path)
        fullText = []
        for para in doc.paragraphs:
            fullText.append(para.text)
        return "\n".join(fullText).strip()
    except Exception as e:
        logger.error(f"Error reading DOCX file {docx_path}: {e}")
        return ""

def extract_text_from_pdf(pdf_path: str) -> str:
    try:
        text = []
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
        return "\n".join(text).strip()
    except Exception as e:
        logger.error(f"Error reading PDF file {pdf_path}: {e}")
        return ""

def get_file_content(folder_path: str) -> str:
    """
    Looks for the first .docx or .pdf file in the folder and extracts text content.
    Returns the extracted content or empty string if no file found or error.
    """
    if not os.path.isdir(folder_path):
        logger.warning(f"Provided folder_path does not exist or is not a directory: {folder_path}")
        return ""

    for filename in os.listdir(folder_path):
        full_path = os.path.join(folder_path, filename)
        if filename.lower().endswith(".docx") and os.path.isfile(full_path):
            logger.info(f"Found .docx file for context extraction: {filename}")
            return extract_text_from_docx(full_path)
        elif filename.lower().endswith(".pdf") and os.path.isfile(full_path):
            logger.info(f"Found .pdf file for context extraction: {filename}")
            return extract_text_from_pdf(full_path)
    logger.info(f"No .docx or .pdf files found in folder: {folder_path}")
    return ""

async def decide_stage(state: OrchestratorState) -> OrchestratorState:
    print(state, "+++++++++++++++++++++++++")
    state["loop_count"] = state.get("loop_count", 0) + 1
    logger.info(f"Loop {state['loop_count']}: Deciding next stage...")
    # Send stage decision update to UI
    await orchestrator_manager.broadcast_to_session({
        "type": "orchestrator_update",
        "message": f"Analyzing request and deciding next stage (Loop {state['loop_count']})...",
        "session_id": state["session_id"],
        "stage": "decision"
    }, state["session_id"])
    if state["loop_count"] > 5:
        logger.warning("Max loop count reached. Forcing stage='done'")
        state["stage"] = "done"
        await notify_agent_completion(state["session_id"], state["stage"], False)
        return state

    # Full session reset â€” user said "start over", "reset", "start fresh" etc.
    _user_text = state.get("user_input", "")
    if _START_OVER_RE.search(_user_text):
        _sid = state["session_id"]
        logger.info(f"Start-over detected for session {_sid} â€” resetting all state.")
        try:
            await clear_orch_state(_sid)
        except Exception as _e:
            logger.warning(f"clear_orch_state failed: {_e}")
        try:
            await clear_session_artifacts(_sid)
        except Exception as _e:
            logger.warning(f"clear_session_artifacts failed: {_e}")
        agent_invocation_counters.pop(_sid, None)
        state["artifact_map"] = {
            "ingestion": ["User wants to start fresh â€” welcome them and ask for their project details or Azure Boards information"]
        }
        state["stage"] = "decide"
        return state

    # Multi-turn shortcut: skip the LLM-decide call when we already know who handles
    # this turn (an agent is mid-conversation, or the user is answering a stage gate).
    try:
        persisted = await get_orch_state(state["session_id"])
    except Exception as exc:
        logger.warning(f"Could not read orchestrator state: {exc}")
        persisted = None

    if persisted:
        active = persisted.get("current_active_agent")
        gate = persisted.get("pending_user_gate")
        user_input_text = state.get("user_input", "")

        if active:
            # Greetings always bypass the lock â€” route via LLM (which sends them to ingestion)
            if _GREETING_RE.match(user_input_text.strip()):
                logger.info(
                    f"Pure greeting detected while '{active}' is active â€” "
                    "bypassing lock and routing via LLM."
                )
                try:
                    await set_orch_state(state["session_id"], current_active_agent="")
                except Exception:
                    pass
            # Stage-change keywords also bypass the lock
            elif (
                _STAGE_CHANGE_RE.search(user_input_text)
                or _EXPLICIT_DEVELOPMENT_RE.search(user_input_text)
                or _has_deployment_intent(user_input_text)
            ):
                logger.info(
                    f"Stage-change keyword detected while '{active}' is active â€” "
                    "bypassing active-agent lock and re-routing via LLM."
                )
                try:
                    await set_orch_state(state["session_id"], current_active_agent="")
                except Exception:
                    pass
            else:
                logger.info(f"Active agent '{active}' present â€” skipping decide.")
                state["artifact_map"] = {active: ["resume in-progress conversation"]}
                state["stage"] = "decide"
                return state


        if gate:
            logger.info(
                "Clearing legacy pending_user_gate=%r; chatbot flow routes from the user's message.",
                gate,
            )
            try:
                await set_orch_state(state["session_id"], pending_user_gate="")
            except Exception:
                pass

    user_input_text = state.get("user_input", "")
    if _EXPLICIT_DEVELOPMENT_RE.search(user_input_text):
        try:
            artifacts = await fetch_session_artifacts(state["session_id"])
        except Exception as exc:
            logger.warning(f"Could not fetch artifacts for development override: {exc}")
            artifacts = {}
        if artifacts and artifacts.get("requirements_payload"):
            logger.info("Explicit development request with requirements payload present - routing to development.")
            try:
                await set_orch_state(state["session_id"], current_active_agent="")
            except Exception:
                pass
            state["artifact_map"] = {
                "development": ["User explicitly asked to start development from the finalized requirements."]
            }
            state["stage"] = "decide"
            return state

    if _has_deployment_intent(user_input_text):
        logger.info("Explicit deployment request detected - routing to deployment.")
        try:
            await set_orch_state(state["session_id"], current_active_agent="")
        except Exception:
            pass
        state["artifact_map"] = {
            "deployment": ["User explicitly asked to deploy or generate deployment assets."]
        }
        state["stage"] = "decide"
        return state

    user_input = state["user_input"]
    user_id = state["user_id"]
    session_id = state["session_id"]
    context_messages: List[BaseMessage] = state.get("messages", [])
    # Defensive â€” `state['artifact_map']` raised KeyError as
    # "Orchestration failed: 'artifact_map'" when the user routed here
    # without the typed-state init (e.g. typing a free-form message after
    # a stage finished). Match the same default used at line 1138.
    artifact_map = state.get("artifact_map", {})
    if not state.get("artifact_map"):
        state["artifact_map"] = artifact_map
    # ---- NEW helper for last 3 user + 3 agent messages ----
    def get_last_messages(messages: List[BaseMessage], num_user: int = 3, num_agent: int = 3) -> str:
        user_msgs = [m for m in messages if isinstance(m, HumanMessage)][-num_user:]
        agent_msgs = [m for m in messages if isinstance(m, AIMessage)][-num_agent:]
        selected = []
        for m in messages:
            if m in user_msgs or m in agent_msgs:
                role = "User" if isinstance(m, HumanMessage) else "Agent"
                selected.append(f"{role}: {m.content}")
        return "\n".join(selected)
    # Get condensed conversation context
    last_context = get_last_messages(context_messages)
   
    
    example_output = """
{
  "ingestion": ["The user wants to ingest approved Azure Boards work items and normalize them for downstream agents"],
  "design": [],
  "development": [],
  "testing": [],
  "deployment": [],
  "monitoring_feedback": [],
  "done": []
}
    """.strip()

    decision_prompt = f"""

    You are an advanced Orchestrator Agent responsible for routing tasks to specialized agents in a multi-agent automation framework.
    Your job is to decide the next best stage to execute based on the userâ€™s request and the accumulated conversation context.
    Goals:
    Correctly determine which specialized agent should handle the next step.
    Handle multi-task or multi-artifact requests step by step, without repeating any stage unnecessarily.
    Use the conversation context to detect if a stage has already been executed.
    Only mark as done when all explicitly requested outputs are complete.
    If the last agent reply looks like an error or exception (e.g., "KeyError", "Traceback", "Exception", "failed"), return error to pause until the user responds.
    Available Agents and Their Responsibilities:
    ingestion: Handles Azure Boards intake, work item normalization, acceptance criteria extraction, user story generation, Epic/Feature creation, BRD-to-stories pipeline, and any follow-up questions needed before design/implementation. Also handles greetings â€” if the user sends a greeting or casual message (e.g., "hi", "hello", "hey", "good morning") with no specific task, route to ingestion so it can welcome the user and ask for their project details or Azure Boards information.
    design: Handles system architecture design (HLD/LLD), C4 diagrams, API contracts, database schema design, Architecture Decision Records (ADRs), technology stack recommendations, and Mermaid diagrams. Runs AFTER ingestion and BEFORE development.
    development: Handles code generation, module implementation, or detailed coding tasks based on the design output.
    testing: Handles test plans, test cases, validation tasks, and QA workflows.
    deployment: Handles release workflows, deployment steps, infrastructure documentation, and production rollout.
    monitoring_feedback: Handles post-deployment monitoring, log analysis, production feedback, incident triage, and root-cause analysis.
    done: Use when all explicitly requested outputs are completed.
    Decision Rules:
    Always base next stage decisions primarily on the latest HumanMessage (user input).
    Treat AIMessage outputs (agent replies) only as context/status, not as new requests.
    Analyze the user query for explicit keywords, goals, or artifacts requested.
    Analyze the conversation context to see which outputs are already present.
    If the original user query contains multiple tasks, pick only the next immediate stage and rely on future iterations to handle the rest.
    If the user request involves multiple artifacts, process them in SDLC order: ingestion â†’ design â†’ development â†’ testing â†’ deployment â†’ monitoring_feedback (Skip any stage that is not needed or asked by the user or is already completed.)
    Never repeat a stage unless the user explicitly requests an update or revision.
    If the user repeats a request (even if it was already completed earlier), treat it as a new task and rerun the appropriate stage once.
    Prioritize user intent over strict SDLC order. Example: If the user wants only a test plan and enough context exists, jump directly to testing.
    Mark the process done only when all requested artifacts are complete.
    IMPORTANT â€” Greeting rule: If the user message is a greeting or social opener (e.g., "hi", "hello", "hey", "good morning", "how are you") with no other task, ALWAYS route to ingestion with the intent "User greeted the orchestrator â€” welcome them and ask for their Azure Boards project or requirements."
    IMPORTANT â€” Save/Download/Export rules: If the user says "save it", "save as file", "download it", "export it", "create a document", "save as docx", "generate file", or any similar phrase asking to persist or export previously generated content, NEVER route to done. Instead, route to the same agent that generated the content so it can save/export the file. For example: if the design agent just generated architecture/schema content and the user says "save it", route to design. If the ingestion agent just generated user stories and the user says "save it", route to ingestion.
    Inputs:
    User Query (latest HumanMessage): "{user_input}"
    Current Conversation Context: "{last_context}"
    Output Format:
    Return a JSON object listing all requested artifacts grouped by stage. For each artifact, write a simple sentence that reflects the userâ€™s intent, such as "The user wants to create a BRD".
    Only include stages and artifacts explicitly mentioned or clearly inferred from the user input.
    Each value in the list should be a plain string describing the userâ€™s intent for that artifact.
    Example Output:
    {example_output}
    NOTE : Make sure that you provide the output in above json struture only.And no other Extra text should be present. For example " Gemini JSON output: "something like this should
            not be present only provide the json.
    """


    # ---------------- LLM Call ----------------
    history_list = state.get("history", [])
    if not history_list:
        history_list = [last_context]  # fallback
    else:
        history_list = history_list

    from shared.services.model_resolver import (
        resolve_model_for_run, NoModelConfiguredError, ModelNotEnabledError)
    try:
        resolved = await resolve_model_for_run(state.get("tenant_id", ""), state.get("model_id"))
    except (NoModelConfiguredError, ModelNotEnabledError) as exc:
        # Fail closed: without a configured BYOK model the orchestrator cannot route.
        # Surface the real reason to the user instead of the generic greeting fallback —
        # otherwise "no model configured" looks like a normal welcome and hides the cause.
        logger.warning(f"Stage routing model unavailable for session {session_id}: {exc}")
        state["artifact_map"] = {}
        state["stage"] = "error"
        state["routing_error"] = (
            "⚠️ No AI model is configured for your organization, so I can't respond yet.\n\n"
            "An administrator needs to add and verify a model provider under "
            "**Settings → Model Providers**. Once a key is added and verified, the assistant "
            "will work immediately."
        )
        return state
    _orch_llm = ChatLiteLLM(
        model=resolved.model,
        custom_llm_provider=resolved.litellm_provider,
        api_base=resolved.base_url,
        api_key=resolved.api_key,
        temperature=0,
        max_tokens=4096,
    )
    _lf_cbs, _lf_meta = langfuse_langchain_extras(
        session_id=session_id, tenant_id=state.get("tenant_id", ""), agent_type="orchestrator",
        project_id=state.get("project_id"),
    )
    _orch_cfg = {"callbacks": _lf_cbs, "metadata": _lf_meta} if _lf_cbs else None
    decision = await _orch_llm.ainvoke(decision_prompt, config=_orch_cfg)
    raw_content = decision.content
    if isinstance(raw_content, list):
        raw_content = "".join(
            b.get("text", "") for b in raw_content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    logger.debug(f"Routing LLM output: {raw_content}")
    logger.info(f"Context sent to routing LLM:\n{last_context}")
    try:
        cleaned = raw_content.strip().replace("```json", "").replace("```", "").strip()
        parsed_output = json.loads(cleaned)
        state["artifact_map"] = parsed_output
        state["stage"] = "decide"
    except Exception as e:
        print(f"âš ï¸ Failed to parse JSON: {e}")
        state["artifact_map"] = {}
        state["stage"] = "error"
    return state

async def fallback_http_call(
    stage: str,
    user_input: str,
    session_id: str,
    user_id: str,
    files_meta: List[Dict],
    provider_kind: str = "azure_devops",
) -> str:
    """Fallback to HTTP API calls if WebSocket fails"""
    logger.info(f"Using HTTP fallback for {stage} agent")

    agent_call_map = {
        "ingestion": call_ingestion_agent,
        "design": call_design_agent,
        "development": call_development_agent,
        "testing": call_testing_agent,
        "deployment": call_deployment_agent,
        "monitoring": call_monitoring_agent,
        "monitoring_feedback": call_monitoring_agent,
    }

    call_func = agent_call_map.get(stage)
    if call_func:
        enriched_input = {
            "conversation_context": str(user_input or ""),
            "task_intent": str(user_input or ""),
            "pipeline_context": empty_pipeline_context(
                session_id=session_id,
                user_id=user_id,
                provider_kind=provider_kind,
            ),
        }
        if stage == "ingestion":
            return await call_func(enriched_input, session_id, user_id, files_meta, provider_kind)
        return await call_func(enriched_input, session_id, user_id, files_meta)
    else:
        return f"Stage '{stage}' handled with fallback HTTP call"

def _agent_form_data(enriched_input: Dict[str, Any], session_id: str, user_id: str) -> Dict[str, str]:
    data = {
        "session_id": session_id,
        "user_id": user_id,
        "conversation_context": enriched_input["conversation_context"],
        "task_intent": enriched_input["task_intent"],
        "pipeline_context": pipeline_context_json(enriched_input.get("pipeline_context")),
    }
    # Forward tenant/model so the agent's graph can resolve the org's BYOK model.
    # Without these the agent node sees tenant_id="" and reports "no model configured".
    tenant_id = enriched_input.get("tenant_id")
    if tenant_id:
        data["tenant_id"] = tenant_id
    model_id = enriched_input.get("model_id")
    if model_id:
        data["model_id"] = model_id
    return data


# Modified agent calls to accept multiple files and send raw files
async def call_ingestion_agent(
    enriched_input: Dict[str, Any],
    session_id: str,
    user_id: str,
    files_meta: List[Dict],
    provider_kind: str = "azure_devops",
) -> str:
    print(enriched_input,"************)00000000000000")
    url = f"{AGENTIC_INTERNAL_BASE_URL}/sdlc/agent/ingestion_orchestrator/chat/"
    logger.info(f"Calling ingestion agent HTTP endpoint: {url}")

    files = []
    file_objs = []

    try:
        for file_data in files_meta:
            f = open(file_data["path"], "rb")
            file_objs.append(f)
            files.append((
                "uploaded_files",
                (os.path.basename(file_data["path"]), f, file_data.get("type", "application/octet-stream"))
            ))

        async with httpx.AsyncClient(timeout=300.00, headers=_internal_agent_headers()) as client:
            data = _agent_form_data(enriched_input, session_id, user_id)
            data["provider_kind"] = provider_kind or "azure_devops"
            response = await client.post(url, data=data, files=files)
            response.raise_for_status()
            json_response = response.json()

            output = json_response.get("responses", "No response from ingestion agent.")
            # files_meta = save_agent_output("ingestion", session_id, user_id, output, files_meta)
            return output

    except Exception as e:
        logger.error(f"HTTP call to ingestion agent failed: {e}")
        return f"Ingestion agent HTTP call failed: {str(e)}"
    finally:
        for f in file_objs:
            f.close()

async def call_design_agent(enriched_input: Dict[str, Any], session_id: str, user_id: str, files_meta: List[Dict]) -> str:
    url = f"{AGENTIC_INTERNAL_BASE_URL}/sdlc/agent/design_orchestrator/chat/"
    logger.info(f"Calling design agent HTTP endpoint: {url}")
    files = []
    file_objs = []
    try:
        for file_data in files_meta:
            f = open(file_data["path"], "rb")
            file_objs.append(f)
            files.append(("uploaded_files", (os.path.basename(file_data["path"]), f, file_data.get("type", "application/octet-stream"))))
        async with httpx.AsyncClient(timeout=600.00, headers=_internal_agent_headers()) as client:
            data = _agent_form_data(enriched_input, session_id, user_id)
            response = await client.post(url, data=data, files=files)
            response.raise_for_status()
            json_resp = response.json()
            # Re-broadcast file_generated from orchestrator context (reliable async context)
            gen_file = json_resp.get("generated_file")
            if gen_file:
                try:
                    await manager.broadcast({
                        "type": "file_generated",
                        "session_id": session_id,
                        "filename": gen_file["filename"],
                        "url": gen_file["url"],
                        "file_size": gen_file.get("file_size", 0),
                        "agent_name": "Design Agent",
                    })
                except Exception as be:
                    logger.warning(f"Orchestrator file_generated broadcast failed: {be}")
            return json_resp.get("responses", "No response from design agent.")
    except Exception as e:
        logger.error(f"HTTP call to design agent failed: {e}")
        return f"Design agent HTTP call failed: {str(e)}"
    finally:
        for f in file_objs:
            f.close()

async def call_development_agent(enriched_input: Dict[str, Any], session_id: str, user_id: str, files_meta: List[Dict]) -> str:
    url = f"{AGENTIC_INTERNAL_BASE_URL}/sdlc/agent/development_orchestrator/chat/"
    logger.info(f"Calling development agent HTTP endpoint: {url}")

    files = []
    file_objs = []

    try:
        for file_data in files_meta:
            f = open(file_data["path"], "rb")
            file_objs.append(f)
            files.append((
                "uploaded_files",
                (os.path.basename(file_data["path"]), f, file_data.get("type", "application/octet-stream"))
            ))

        async with httpx.AsyncClient(timeout=1800.00, headers=_internal_agent_headers()) as client:
            data = _agent_form_data(enriched_input, session_id, user_id)
            response = await client.post(url, data=data, files=files)
            response.raise_for_status()
            json_response = response.json()

            output = json_response.get("responses", "No response from development agent.")
            return output

    except Exception as e:
        logger.error(f"HTTP call to development agent failed: {e}")
        return f"Development agent HTTP call failed: {str(e)}"
    finally:
        for f in file_objs:
            f.close()

async def call_testing_agent(enriched_input: Dict[str, Any], session_id: str, user_id: str, files_meta: List[Dict]) -> str:
    url = f"{AGENTIC_INTERNAL_BASE_URL}/sdlc/agent/testing_orchestrator/chat/"
    logger.info(f"Calling testing agent HTTP endpoint: {url}")

    files = []
    file_objs = []

    try:
        for file_data in files_meta:
            f = open(file_data["path"], "rb")
            file_objs.append(f)
            files.append((
                "uploaded_files",
                (os.path.basename(file_data["path"]), f, file_data.get("type", "application/octet-stream"))
            ))

        # Phase 8.5 â€” bumped from 300s to 1800s (matches call_development_agent
        # at line 1014). Real testing runs (carelon clone + dotnet test)
        # routinely take 200-321s; the 5-min ceiling caused random timeouts
        # as repos grew. 30 min is generous headroom + matches the dev agent
        # which has the same long-running profile.
        async with httpx.AsyncClient(timeout=1800.00, headers=_internal_agent_headers()) as client:
            # Infer explicit test types from the latest user request only.
            # The conversation context can mention unit/UI/API historically and
            # must not skip testing's scope question for a generic "test it".
            combined_text = f"{enriched_input.get('task_intent', '')}".lower()
            selected = []
            if "unit" in combined_text or "code test" in combined_text:
                selected.append("unit")
            if (
                "functional api" not in combined_text
                and ("functional" in combined_text or "ui test" in combined_text or "browser" in combined_text)
            ):
                selected.append("functional")
            if "api" in combined_text or "endpoint" in combined_text:
                selected.append("api")
            data = _agent_form_data(enriched_input, session_id, user_id)
            if selected:
                data["selected_test_types"] = json.dumps(selected)
            response = await client.post(url, data=data, files=files)
            response.raise_for_status()
            json_response = response.json()

            output = json_response.get("responses", "No response from testing agent.")
            # files_meta = save_agent_output("testing", session_id, user_id, output, files_meta)
            return output

    except Exception as e:
        logger.error(f"HTTP call to testing agent failed: {e}")
        return f"Testing agent HTTP call failed: {str(e)}"
    finally:
        for f in file_objs:
            f.close()

async def call_deployment_agent(enriched_input: Dict[str, Any], session_id: str, user_id: str, files_meta: List[Dict]) -> str:
    url = f"{AGENTIC_INTERNAL_BASE_URL}/sdlc/agent/deployment_orchestrator/chat/"
    logger.info(f"Calling deployment agent HTTP endpoint: {url}")

    files = []
    file_objs = []

    try:
        for file_data in files_meta:
            f = open(file_data["path"], "rb")
            file_objs.append(f)
            files.append((
                "uploaded_files",
                (os.path.basename(file_data["path"]), f, file_data.get("type", "application/octet-stream"))
            ))

        async with httpx.AsyncClient(timeout=300.00, headers=_internal_agent_headers()) as client:
            data = _agent_form_data(enriched_input, session_id, user_id)
            response = await client.post(url, data=data, files=files)
            response.raise_for_status()
            json_response = response.json()

            output = json_response.get("responses", "No response from deployment agent.")
            # files_meta = save_agent_output("deployment", session_id, user_id, output, files_meta)
            return output

    except Exception as e:
        logger.error(f"HTTP call to deployment agent failed: {e}")
        return f"Deployment agent HTTP call failed: {str(e)}"
    finally:
        for f in file_objs:
            f.close()

async def call_monitoring_agent(enriched_input: Dict[str, Any], session_id: str, user_id: str, files_meta: List[Dict]) -> str:
    url = f"{AGENTIC_INTERNAL_BASE_URL}/sdlc/agent/monitoring_orchestrator/chat/"
    logger.info(f"Calling monitoring agent HTTP endpoint: {url}")

    files = []
    file_objs = []

    try:
        for file_data in files_meta:
            f = open(file_data["path"], "rb")
            file_objs.append(f)
            files.append((
                "uploaded_files",
                (os.path.basename(file_data["path"]), f, file_data.get("type", "application/octet-stream"))
            ))

        async with httpx.AsyncClient(timeout=300.00, headers=_internal_agent_headers()) as client:
            data = _agent_form_data(enriched_input, session_id, user_id)
            response = await client.post(url, data=data, files=files)
            response.raise_for_status()
            json_response = response.json()

            output = json_response.get("responses", "No response from monitoring agent.")
            # files_meta = save_agent_output("monitoring_feedback", session_id, user_id, output, files_meta)
            return output

    except Exception as e:
        logger.error(f"HTTP call to monitoring agent failed: {e}")
        return f"Monitoring agent HTTP call failed: {str(e)}"
    finally:
        for f in file_objs:
            f.close()
            
def get_last_messages(messages: List[BaseMessage], num_user: int = 3, num_agent: int = 3) -> str:
        user_msgs = [m for m in messages if isinstance(m, HumanMessage)][-num_user:]
        agent_msgs = [m for m in messages if isinstance(m, AIMessage)][-num_agent:]
        selected = []
        for m in messages:
            if m in user_msgs or m in agent_msgs:
                role = "User" if isinstance(m, HumanMessage) else "Agent"
                selected.append(f"{role}: {m.content}")
        return "\n".join(selected)

async def orchestrate_agents(state: OrchestratorState) -> OrchestratorState:
    print(state, "1111111111111111111!!!!!!!!!!")
    # âœ… Ensure keys exist even if missing from initial_state
    state.setdefault("messages", [])
    state.setdefault("history", [])
    state.setdefault("artifact_map", {})
    state.setdefault("files_meta", [])
    state.setdefault("pipeline_context", {})
    artifact_map = state["artifact_map"]
    user_message = state["user_input"]
    session_id = state["session_id"]
    user_id = state["user_id"]
    provider_kind = state.get("provider_kind") or "azure_devops"
    files_meta = state["files_meta"]
    artifacts = await fetch_session_artifacts(session_id)
    state["pipeline_context"] = build_pipeline_context(
        session_id=session_id,
        user_id=user_id,
        provider_kind=provider_kind,
        artifacts=artifacts,
    )
    # âœ… Append user input into rolling history
    user_message_obj = HumanMessage(content=user_message)
    state["messages"].append(user_message_obj)
    print("========= CURRENT STATE =========")
    print("files_meta:", files_meta)
    print("current messages:", state["messages"])
    # ---- Helper to enrich input with typed artifact context ----
    async def enrich_input(stage_key: str, artifact_map: Dict, messages: List[BaseMessage]) -> Dict[str, Any]:
        artifact_intents = artifact_map.get(stage_key, [])
        first_intent = artifact_intents[0] if artifact_intents else ""
        last_context = get_last_messages(messages)
        pipeline_context = state.get("pipeline_context") or empty_pipeline_context(
            session_id=session_id,
            user_id=user_id,
            provider_kind=provider_kind,
        )

        if stage_key == "design":
            req_context = _format_requirements_context(
                pipeline_context.get("requirements")
            )
            conversation_context = (
                f"Requirements Context (from Requirements Agent):\n{req_context}"
                f"\n\nRecent Conversation:\n{last_context}"
            )
        elif stage_key == "development":
            req_context = _format_requirements_context(
                pipeline_context.get("requirements")
            )
            design_context = _format_design_context(
                pipeline_context.get("design")
            )
            dev_context = _format_development_context(
                pipeline_context.get("development")
            )
            test_context = _format_testing_context(
                pipeline_context.get("testing")
            )
            conversation_context = (
                f"Requirements Context:\n{req_context}"
                f"\n\nDesign Context (LLD, API Contracts, DB Schema, Tech Stack, ADRs):\n{design_context}"
                f"\n\nPrevious Development Context (branch, repo, PR):\n{dev_context}"
                f"\n\nTesting Context (if tests already ran):\n{test_context}"
                f"\n\nRecent Conversation:\n{last_context}"
            )
        elif stage_key == "testing":
            dev_artifacts = pipeline_context.get("development")
            req_context = _format_requirements_context(
                pipeline_context.get("requirements")
            )
            design_context = _format_design_context(
                pipeline_context.get("design")
            )
            dev_context = _format_development_context(dev_artifacts)
            # Phase 11.2 â€” include Design Context so the testing agent's
            # contract / accessibility / functional_api skills can drive tests
            # from the API contracts + DB schema + ADRs the design agent
            # produced. Pre-fix, only req + dev context reached testing,
            # making it impossible to validate "the implementation matches
            # the documented contract".
            #
            # Phase 10.2 â€” include Recent Conversation so testing's Phase 8.9d
            # chat-history fallback can mine the branch/repo from the dev's
            # user-visible chat output when development_artifacts is NULL
            # (e.g. user said "push but don't create PR" â†’ dev never called
            # submit_development_artifacts â†’ development_artifacts row stays empty).
            conversation_context = (
                f"Requirements Context:\n{req_context}"
                f"\n\nDesign Context (LLD, API Contracts, DB Schema, Tech Stack, ADRs):\n{design_context}"
                f"\n\nDevelopment Context (summary, repo, branch, test instructions):\n{dev_context}"
                f"\n\nRecent Conversation:\n{last_context}"
            )
        elif stage_key == "deployment":
            req_context = _format_requirements_context(
                pipeline_context.get("requirements")
            )
            dev_context = _format_development_context(
                pipeline_context.get("development")
            )
            test_context = _format_testing_context(
                pipeline_context.get("testing")
            )
            conversation_context = (
                f"Requirements Context:\n{req_context}"
                f"\n\nDevelopment Context (branch, repo, test instructions):\n{dev_context}"
                f"\n\nTesting Context (pass/fail counts, coverage, summary):\n{test_context}"
            )
        else:
            conversation_context = (
                f"Conversation context (last 3 user + 3 agent messages):\n{last_context}"
            )

        return {
            "conversation_context": conversation_context,
            "task_intent": first_intent,
            "pipeline_context": pipeline_context,
            "tenant_id": state.get("tenant_id", ""),
            "model_id": state.get("model_id"),
        }

    # Dispatch table: stage key -> (display name, call function)
    _dispatch = {
        "ingestion": ("Ingestion Agent", call_ingestion_agent),
        "design": ("Design & Architecture Agent", call_design_agent),
        "development": ("Development Agent", call_development_agent),
        "testing": ("Testing Agent", call_testing_agent),
        "deployment": ("Deployment Agent", call_deployment_agent),
        "monitoring_feedback": ("Monitoring Feedback Agent", call_monitoring_agent),
    }

    async def _run_stage(stage_key: str) -> tuple[str, Dict[str, Any] | None]:
        display, call_func = _dispatch[stage_key]
        await notify_agent_routing(session_id, display, stage_key.capitalize())
        enriched_input = await enrich_input(stage_key, artifact_map, state["messages"])
        if stage_key == "ingestion":
            raw_response = await call_func(
                enriched_input,
                session_id,
                user_id,
                files_meta,
                state.get("provider_kind") or "azure_devops",
            )
        else:
            raw_response = await call_func(enriched_input, session_id, user_id, files_meta)
        await notify_agent_completion(session_id, display, False)

        # Parse HANDOFF sentinel (if any) and strip it from user-visible text.
        handoff = parse_handoff(raw_response)
        visible = strip_handoff(raw_response)

        # Persist orchestrator state based on handoff outcome.
        try:
            if handoff:
                next_stage = handoff.get("to")
                await set_orch_state(
                    session_id,
                    current_active_agent="",
                    current_batch_id=handoff.get("batch_id", ""),
                    pending_user_gate="",
                    last_handoff_event=handoff,
                )
            else:
                # No handoff â€” agent is still mid-conversation, keep it active.
                await set_orch_state(
                    session_id,
                    current_active_agent=stage_key,
                    pending_user_gate="",
                )
        except Exception as exc:
            logger.warning(f"Orchestrator state write failed for stage {stage_key}: {exc}")

        try:
            refreshed_artifacts = await fetch_session_artifacts(session_id)
            state["pipeline_context"] = build_pipeline_context(
                session_id=session_id,
                user_id=user_id,
                provider_kind=provider_kind,
                artifacts=refreshed_artifacts,
                last_handoff_event=handoff,
            )
        except Exception as exc:
            logger.warning(f"Pipeline context refresh failed for stage {stage_key}: {exc}")

        agent_msg = AIMessage(content=visible)
        state["messages"].append(agent_msg)
        await manager.send_agent_response(f"{stage_key}_agent", visible, session_id)
        return f"{display}:\n{visible}", handoff

    combined_outputs = []
    for stage in ("ingestion", "design", "development", "testing", "deployment", "monitoring_feedback"):
        if artifact_map.get(stage):
            output, handoff = await _run_stage(stage)
            combined_outputs.append(output)
            break  # One stage per user turn; gate question asks for explicit approval before continuing.

    # ==== Finalize ====
    if combined_outputs:
        final_output = "\n\n".join(combined_outputs)
    else:
        # A routing failure (e.g. no model configured) must be shown truthfully — never
        # papered over with the cheerful greeting, which makes a broken org look healthy.
        # `stage == "error"` is a declared channel and always survives the node hop, so it
        # is the reliable signal even if a specific routing_error message wasn't carried.
        routing_error = state.get("routing_error")
        if routing_error:
            final_output = routing_error
        elif state.get("stage") == "error":
            final_output = (
                "⚠️ No AI model is configured for your organization, so I can't respond yet.\n\n"
                "An administrator needs to add and verify a model provider under "
                "**Settings → Model Providers**. Once a key is added and verified, the assistant "
                "will work immediately."
            )
        else:
            final_output = (
                "Hello! I'm your SDLC Orchestrator. I can help you manage your entire software "
                "development lifecycle — from requirements ingestion through design, development, "
                "testing, and deployment.\n\n"
                "To get started, please share your Azure Boards project details, paste your user "
                "stories, or describe what you'd like to build."
            )
        greeting_msg = AIMessage(content=final_output)
        state["messages"].append(greeting_msg)
        await manager.send_agent_response("Orchestrator", final_output, session_id)
    state["history"].append(final_output)
    state["stage"] = "done"
    print(state["messages"], "1----------11111111111111")
    return state

# Build Orchestration Graph with WebSocket support
workflow = StateGraph(OrchestratorState)

# Add nodes
workflow.add_node("decide", decide_stage)
workflow.add_node("orchestrate", orchestrate_agents)

workflow.set_entry_point("decide")
workflow.add_edge("decide", "orchestrate")
workflow.add_edge("orchestrate", END)

orchestrator = workflow.compile(checkpointer=_build_checkpointer("orchestrator"))

# WebSocket endpoint for orchestrator
@orchestrator_router.websocket("/ws")
async def orchestrator_websocket_endpoint(websocket: WebSocket):
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
    tenant_id = claims.get("tenant_id", "")
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            session_id = message_data.get("session_id")
            
            from config.websocket_utils import set_websocket_context
            from config.ws_helper import set_session_id, set_user_id
            
            set_websocket_context(manager, session_id)
            set_session_id(session_id)
            set_user_id(user_id)
            
            if message_data.get("type") == "user_message_with_files":
                await process_orchestrator_message_ws(message_data, websocket, session_id, user_id, tenant_id)
            elif message_data.get("type") in ("clear_session", "clear_agents"):
                await handle_orchestrator_cleanup_ws(session_id, user_id, websocket)
            else:
                await orchestrator_manager.send_personal_message(
                    json.dumps({"type": "echo", "message": f"Echo: {data}"}),
                    websocket
                )
               
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Orchestrator WebSocket error: {e}")
        manager.disconnect(websocket)

def _extract_agent_reply(final_state: dict) -> str:
    """Pull the most recent agent (AIMessage) reply text from the graph state.

    The graph appends each agent's visible output as an AIMessage on
    state["messages"] (and the raw text on state["history"]). The chat UI wants
    the latest one. Handles Anthropic block-list content shapes. Never raises."""
    try:
        for m in reversed(final_state.get("messages", []) or []):
            if isinstance(m, AIMessage):
                content = m.content
                if isinstance(content, list):
                    content = "".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if isinstance(content, str) and content.strip():
                    return content.strip()
        history = final_state.get("history", []) or []
        if history and isinstance(history[-1], str) and history[-1].strip():
            return history[-1].strip()
    except Exception:  # noqa: BLE001 — reply extraction must never break the WS
        pass
    return ""


async def process_orchestrator_message_ws(message_data: dict, websocket: WebSocket, session_id: str, user_id: str, tenant_id: str = ""):
    # Target streamed chunks at this socket's session.
    try:
        manager.register_session(websocket, session_id)
    except Exception:  # noqa: BLE001
        pass
    # Mint the internal bearer token for this turn's sibling-agent HTTP calls.
    try:
        token = create_access_token(user_id or "orchestrator", tenant_id or "", permissions=["artifact:view"])
        _internal_auth_headers_var.set({"Authorization": f"Bearer {token}"})
    except Exception as exc:  # noqa: BLE001 — never block the turn on token mint
        logger.warning(f"internal token mint failed: {type(exc).__name__}: {exc}")
    try:
        # The chat BFF bridge sends the user's message as `task_intent`; older
        # clients used `text`. Accept either so the message reaches the agent.
        user_message = message_data.get("task_intent") or message_data.get("text", "") or ""
        files_meta = message_data.get("files", [])
        provider_kind = message_data.get("provider_kind", "azure_devops")

        # Chat attachments (uploaded via POST /conversations/{id}/attachments) arrive as
        # paths in pipeline_context.attachments — append to the routed agent's input and
        # persist refs so a reopened session shows + can download them.
        _pctx = message_data.get("pipeline_context")
        _attachments = _pctx.get("attachments") if isinstance(_pctx, dict) else None
        _attach_paths = [a.get("path") for a in (_attachments or []) if isinstance(a, dict) and a.get("path")]
        if _attach_paths:
            user_message = f"{user_message}\n\nplease use the following files {', '.join(_attach_paths)}"

        # Persist the user turn to the conversation transcript (§11A) — best-effort.
        await persist_turn(
            session_id, "user", user_message, tenant_id=tenant_id or None, author_id=str(user_id),
            artifact_refs=_attachments or None,
        )

        initial_state = {
            "user_input": user_message,
            "session_id": session_id,
            "user_id": user_id,
            "files_meta": files_meta,
            "provider_kind": provider_kind,
            "tenant_id": tenant_id,
            "model_id": message_data.get("model_id"),
        }

        logger.info(f"Starting orchestration workflow for session {session_id}")

        config = {"configurable": {"thread_id": session_id}}
        final_state = await orchestrator.ainvoke(initial_state, config=config)

        # Stream the agent's reply to the chat UI. The BFF mapper renders
        # `stream_chunk` (content delta) then clears the typing indicator on
        # `stream_end`. (Replaces the old `agent_response` type, which the BFF
        # discarded — the reply never reached the drawer.)
        reply = _extract_agent_reply(final_state) or (
            "The agent finished but didn't return a message. "
            "Check that a verified model provider is configured."
        )
        # Persist the agent turn to the conversation transcript (§11A) — best-effort.
        await persist_turn(session_id, "agent", reply, tenant_id=tenant_id or None, author_id="orchestrator")
        await manager.broadcast(
            {"type": "stream_chunk", "content": reply, "session_id": session_id}
        )
        await manager.broadcast({"type": "stream_end", "session_id": session_id})

    except Exception as e:
        logger.error(f"Orchestration failed for session {session_id}: {type(e).__name__}: {e}")
        await manager.broadcast(
            {
                "type": "stream_chunk",
                "content": f"Sorry — the agent hit an error ({type(e).__name__}).",
                "session_id": session_id,
            }
        )
        await manager.broadcast({"type": "stream_end", "session_id": session_id})


async def handle_orchestrator_cleanup_ws(session_id: str, user_id: str, websocket: WebSocket):
    try:
        logger.info(f"Cleaning up session {session_id} for user {user_id}")

        # 1. Clear orchestrator state rows from DB
        if session_id:
            try:
                await clear_orch_state(session_id)
            except Exception as e:
                logger.warning(f"clear_orch_state failed: {e}")

            # 2. Clear pipeline artifacts (requirements, design, dev payloads)
            try:
                await clear_session_artifacts(session_id)
            except Exception as e:
                logger.warning(f"clear_session_artifacts failed: {e}")

            # 3. Delete all session files from disk
            if user_id:
                import shutil
                session_root = os.path.join(esett.FILES, str(user_id), "orchestrator", str(session_id))
                if os.path.exists(session_root):
                    try:
                        shutil.rmtree(session_root)
                        logger.info(f"Deleted session files at {session_root}")
                    except Exception as e:
                        logger.warning(f"Failed to delete session files: {e}")

            # 4. Reset in-memory invocation counters for this session
            agent_invocation_counters.pop(session_id, None)

        await orchestrator_manager.send_personal_message(
            json.dumps({
                "type": "agents_cleared",
                "message": "Session cleared successfully.",
                "session_id": session_id,
            }),
            websocket,
        )
    except Exception as e:
        logger.error(f"Orchestrator cleanup failed: {e}")

async def copy_files_to_agent_path(files_meta: List[Dict], session_id: str, user_id: str, agent_name: str):
    orchestrator_input_path = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input"
    agent_input_path = f"{esett.FILES}/{user_id}/{agent_name}_agent/{session_id}/input"
    
    os.makedirs(agent_input_path, exist_ok=True)
    
    copied_files = []
    
    try:
        if os.path.exists(orchestrator_input_path):
            for filename in os.listdir(orchestrator_input_path):
                src_file = os.path.join(orchestrator_input_path, filename)
                dst_file = os.path.join(agent_input_path, filename)
                
                if os.path.isfile(src_file):
                    shutil.copy2(src_file, dst_file)
                    copied_files.append(dst_file)
                    logger.info(f"Copied {filename} to {agent_name} agent path")
        
        return copied_files
        
    except Exception as e:
        logger.error(f"Failed to copy files to {agent_name} agent path: {e}")
        return []

# Modified REST endpoint to receive files and save raw without base64, pass files_meta downstream
@orchestrator_router.post("/chat/")
async def orchestrator_chat(
    session_id: str = Form(...),
    user_message: str = Form(...),
    user_id: str = Form(...),
    provider_kind: str = Form("azure_devops"),
    uploaded_files: List[UploadFile] = File(None)
):
    print(uploaded_files,"!!!!!!!!!!!!!^^^^^^^^^^^^^^^^^")
    if not session_id or session_id.lower() in ("undefined", "null", "none", ""):
        session_id = uuid.uuid4().hex
        logger.warning(f"Received invalid session_id from client; generated fallback {session_id}")
    logger.info(f"HTTP orchestrator chat request for session {session_id}")

    input_directory = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input"
    os.makedirs(input_directory, exist_ok=True)
    saved_file_paths = []
    files_meta = []

    if uploaded_files:
        for uploaded_file in uploaded_files:
            file_path = os.path.join(input_directory, uploaded_file.filename)
            contents = await uploaded_file.read()
            with open(file_path, "wb") as f:
                f.write(contents)
            saved_file_paths.append(file_path)
            files_meta.append({
                "name": uploaded_file.filename,
                "path": file_path,
                "type": uploaded_file.content_type,
                "size": len(contents)
            })

    result = await run_orchestration(
        user_message,
        session_id=session_id,
        user_id=user_id,
        files_meta=files_meta,
        provider_kind=provider_kind,
    )
    logger.info(f"HTTP orchestration completed for session {session_id}")
    if isinstance(result, dict):
        result.setdefault("session_id", session_id)
    return JSONResponse(content=result)

async def run_orchestration(
    user_query: str,
    session_id: str = None,
    user_id: str = None,
    files_meta: List[Dict] = None,
    provider_kind: str = "azure_devops",
    tenant_id: str = "",
    model_id: str | None = None,
) -> Dict[str, Any]:
    if session_id is None:
        session_id = str(uuid.uuid4())

    initial_state = {
        "user_input": user_query,
        "stage": "",

        "session_id": session_id,

        # "completed_stages": set()
        "artifact_map":{},
        "user_id": user_id,
        "session_id": session_id,
        "files_meta": files_meta or [],
        "provider_kind": provider_kind or "azure_devops",
        "tenant_id": tenant_id,
        "model_id": model_id,
        "pipeline_context": empty_pipeline_context(
            session_id=session_id,
            user_id=user_id or "",
            provider_kind=provider_kind or "azure_devops",
        ),

    }
    config = {"configurable": {"thread_id": session_id}}
    final_state = await orchestrator.ainvoke(initial_state, config=config)
 
    # Serialize messages to simple dicts
    serialized_messages = [
    {"type": msg.__class__.__name__, "content": msg.content}
    for msg in final_state.get("messages", [])
]
 
    return {
    "session_id": session_id,
    "final_context": serialized_messages,
    "history": final_state.get("history", []),
}
 

# Test endpoint to verify agent connectivity
@orchestrator_router.get("/test-agent-connections")
async def test_agent_connections():
    logger.info("Testing agent WebSocket connections...")
    
    connection_results = {}
    
    for agent_name, ws_url in AGENT_WS_MAP.items():
        try:
            logger.info(f"Testing connection to {agent_name} at {ws_url}")
            test_client = AgentWebSocketClient(agent_name, "test-session")
            connection_success = await test_client.connect()
            
            if connection_success:
                test_message = {
                    "type": "health_check",
                    "session_id": "test-session",
                    "user_id": "system",
                    "text": "Health check from orchestrator"
                }
                
                message_sent = await test_client.send_message(test_message)
                await asyncio.sleep(2)
                await test_client.disconnect()
                
                connection_results[agent_name] = {
                    "status": "success",
                    "connected": True,
                    "message_sent": message_sent,
                    "url": ws_url
                }
                logger.info(f"âœ… {agent_name} connection successful")
            else:
                connection_results[agent_name] = {
                    "status": "failed",
                    "connected": False,
                    "message_sent": False,
                    "url": ws_url,
                    "error": "Failed to connect"
                }
                logger.error(f"âŒ {agent_name} connection failed")
                
        except Exception as e:
            connection_results[agent_name] = {
                "status": "error",
                "connected": False,
                "message_sent": False,
                "url": ws_url,
                "error": str(e)
            }
            logger.error(f"âŒ {agent_name} connection error: {e}")
    
    return JSONResponse(content={
        "test_timestamp": datetime.now().isoformat(),
        "total_agents": len(AGENT_WS_MAP),
        "successful_connections": sum(1 for r in connection_results.values() if r["connected"]),
        "results": connection_results
    })

# FastAPI app setup
app = FastAPI()
app.include_router(orchestrator_router, prefix="/orchestrator")
