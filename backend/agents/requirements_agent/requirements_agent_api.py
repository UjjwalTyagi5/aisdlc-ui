import os
import asyncio
import json
from typing import List, Dict, Any
import httpx
from fastapi import FastAPI, Form, Query, WebSocket, WebSocketDisconnect, UploadFile, File, APIRouter
import sys
import base64
from fastapi.responses import FileResponse
from fastapi import HTTPException
from pydantic import BaseModel
from agents_orchestrator.requirements_agent.agents.planning import app as planning_app
from config.ws_helper import set_session_id, broadcast_log, set_user_id, set_provider_kind, get_provider_kind
from config.connection_manager import manager
from config.agent_context import set_agent_folder
from dotenv import load_dotenv
import google.generativeai as genai
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from uuid import uuid4
from agents.requirements_agent.config import shared
from config.websocket_utils import set_websocket_context
import sys, inspect
import aiofiles
 
from config import sdlcSettings
from config.connector_factory import get_connector_for_session
from config.connectors.base import ConnectorNotAvailableError
from shared.tools.ingestion_summary import build_ingestion_summary
esett = sdlcSettings()

load_dotenv()

requirement_router = APIRouter() 


class WorkItemImportRequest(BaseModel):
    organization_url: str | None = None
    project: str | None = None
    team: str | None = None
    work_item_id: int
    provider_kind: str = "azure_devops"


AdoWorkItemImportRequest = WorkItemImportRequest

from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE
SYS_MESSAGE = INGESTION_SYS_MESSAGE
import contextvars

import logging

from datetime import datetime

import uuid

import asyncio

SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default=None)
_session_provider_kinds: dict[str, str] = {}

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

        # Fire-and-forget broadcast so logging doesn’t block

        asyncio.create_task(manager.broadcast({

            "type": "activity_update",

            "activity": activity

        }))

# Configure the logger

logger = logging.getLogger("requirements_agent")

logger.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(levelname)s: %(message)s")

ws_handler = WebsocketBroadcastHandler()

ws_handler.setFormatter(formatter)

logger.addHandler(ws_handler)

# Also keep printing to console if desired

console_handler = logging.StreamHandler(sys.stdout)

console_handler.setFormatter(formatter)

logger.addHandler(console_handler)

# OPTIONAL: capture stray print(...) calls into logger (temporary)

class PrintToLogger:

    def write(self, msg):

        msg = msg.strip()

        if msg:

            logger.info(msg)

    def flush(self):

        pass

def process_agent_stream_for_chat_display(stream):
    responses = []
    for s in stream:
        for message in s["messages"]:
            if isinstance(message, (HumanMessage, ToolMessage, SystemMessage)):
                continue
            if not message.tool_calls:
                responses.append(message.content)
    return responses


async def _get_provider(kind: str):
    try:
        return await get_connector_for_session(kind)
    except ConnectorNotAvailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@requirement_router.get("/ado/projects")
async def ado_projects(provider_kind: str = Query("azure_devops")):
    provider = await _get_provider(provider_kind)
    try:
        return {"projects": await provider.list_projects()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@requirement_router.get("/ado/teams")
async def ado_teams(project: str, provider_kind: str = Query("azure_devops")):
    if not project.strip():
        raise HTTPException(status_code=400, detail="Project is required.")
    provider = await _get_provider(provider_kind)
    try:
        return {"teams": await provider.list_teams(project)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@requirement_router.get("/ado/states")
async def ado_states(project: str, work_item_type: str = "User Story", provider_kind: str = Query("azure_devops")):
    if not project.strip():
        raise HTTPException(status_code=400, detail="Project is required.")
    provider = await _get_provider(provider_kind)
    try:
        return {"states": await provider.list_states(project, work_item_type)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@requirement_router.get("/ado/stories")
async def ado_stories(project: str, state: str, team: str | None = None, provider_kind: str = Query("azure_devops")):
    if not project.strip():
        raise HTTPException(status_code=400, detail="Project is required.")
    provider = await _get_provider(provider_kind)
    try:
        return {"stories": await provider.list_stories(project, state, team or None)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@requirement_router.post("/ado/work-item")
async def import_ado_work_item(payload: WorkItemImportRequest):
    kind = payload.provider_kind or get_provider_kind()
    provider = await _get_provider(kind)
    project = (payload.project or "").strip()
    team = (payload.team or "").strip()

    if not project:
        raise HTTPException(status_code=400, detail="Project is required.")

    try:
        normalized = await provider.fetch_item_detail(project, payload.work_item_id)
        if team:
            normalized.setdefault("team", team)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    summary = build_ingestion_summary(normalized)
    return {"status": "ok", "normalized": normalized, "summary": summary}

@requirement_router.websocket("/test-ws")

async def test_websocket(websocket: WebSocket):

    await websocket.accept()

    await websocket.send_text("WebSocket connection successful!")

    try:

        while True:

            data = await websocket.receive_text()

            await websocket.send_text(f"Echo: {data}")

    except WebSocketDisconnect: 
        print("Test WebSocket disconnected")

@requirement_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            session_id = message_data.get("session_id", str(uuid4()))
            user_id = message_data.get("user_id")
            shared.set_agent_folder("requirements_agent")
            set_websocket_context(manager, session_id)
            set_session_id(session_id)
            set_user_id(user_id)
            print(f"DEBUG: WebSocket context set for session: {session_id} in /ws endpoint.")

            if message_data.get("type") == "user_message_with_files":
                provider_kind = message_data.get("provider_kind") or _session_provider_kinds.get(session_id)
                if provider_kind:
                    _session_provider_kinds[session_id] = provider_kind
                    set_provider_kind(provider_kind)
                await process_user_message_ws(message_data, websocket,user_id)
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
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)

async def encode_files_to_base64(file_paths: List[str]) -> List[Dict[str, str]]:
    """
    Given a list of file paths, read and base64 encode them.
    Returns list of dicts with file name and base64 content.
    """
    encoded_files = []
    for path in file_paths:
        print(path,"@#$$$$$$$$$$$$$$$$$$$$$4")
        try:
            async with aiofiles.open(path, "rb") as f:
                content_bytes = await f.read()
            content_b64 = base64.b64encode(content_bytes).decode('utf-8')
            encoded_files.append({
                "name": os.path.basename(path),
                "content": content_b64
            })
        except Exception as e:
            print(f"Error encoding file {path}: {e}")
            # Optionally handle/log error or skip file
    return encoded_files

async def process_user_message_ws(message_data: dict, websocket: WebSocket, user_id):
    """Process user message with files and send real-time updates via WebSocket"""
    try:
        print(message_data,"@@!!###")
        session_id = message_data.get("session_id", str(uuid4()))
        user_message = message_data.get("text", "")
        files_data = message_data.get("files", [])  # Array of {name, content} objects
        # input_directory = shared.input_dir
        input_directory = f"{esett.FILES}/{user_id}/{shared.get_agent_folder()}/{session_id}/input"
        file_names = []
        first_message_in_new_session = False

        if shared.prev_session_id == "":
            shared.prev_session_id = session_id

        if shared.prev_session_id != session_id:
            first_message_in_new_session = True
            print(f"DEBUG: New session ({session_id}) detected. Initiating cleanup of previous session ({shared.prev_session_id})...")
            await manager.send_session_update(session_id, "cleanup", "Cleaning up previous session...")
            config_cleanup = {"configurable": {"thread_id": shared.prev_session_id}}
            state_cleanup = {"messages": [HumanMessage(content="cleanup if needed")]}
            planning_app.invoke(state_cleanup, config=config_cleanup)
            shared.prev_session_id = session_id
            await manager.send_session_update(session_id, "ready", "Session cleaned up, ready for new requests")
            print(f"DEBUG: Previous session ({shared.prev_session_id}) cleaned up. Ready for {session_id}.")

        config = {"configurable": {"thread_id": session_id}}
        os.makedirs(input_directory, exist_ok=True)

        # Process files if provided
        if files_data:
            await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])
            print(f"DEBUG: Processing {len(files_data)} uploaded file(s) for session {session_id}.")
            for file_data in files_data:
                try:
                    # print(file_dat"==============")
                    base_name, extension = os.path.splitext(file_data.get("name"))
                    print(base_name,"=====++++")
                    new_file_name = f"{base_name}{extension}"
                    file_path = os.path.join(input_directory, new_file_name)
                    file_names.append(file_path)
                    if "content" not in file_data:
                        try:
                            encoded_files = await encode_files_to_base64(file_names)
                        except Exception as e:
                            print(e,"1111111111111")
                        try:
                            file_content = base64.b64decode(encoded_files[0]["content"])
                            with open(file_path, "wb") as file:
                                file.write(file_content)
                            print(f"DEBUG: Successfully saved uploaded file: {file_path}")
                        except Exception as e:
                            print(f"ERROR: Error processing file {encoded_files['name']}: {str(e)}")
                            await manager.send_agent_response("Error Agent", f"Error processing file {encoded_files['name']}: {str(e)}", session_id)
                            continue
                    else:
                        file_content_bytes = base64.b64decode(file_data["content"])
                        with open(file_path, "wb") as file:
                            file.write(file_content_bytes)
                        
                        full_log_content += file_content_bytes.decode("utf-8", errors="ignore") + "\n"
                        logger.info(f"Successfully saved uploaded file: {file_path}")
                except Exception as e:
                    error_msg = f"Error processing file {file_data['name']}: {str(e)}"
                    logger.error(error_msg)
                    await manager.send_agent_response("Error Agent", error_msg, session_id)
                    continue


        # Prepare state messages
        state = {
            "messages": [SystemMessage(content=SYS_MESSAGE)] + [HumanMessage(content=user_message)] if first_message_in_new_session
            else [HumanMessage(content=user_message)]
        }

        if file_names:
            upload_messages = ", ".join(file_names)
            state["messages"].append(HumanMessage(content=f"please use the following files {upload_messages}"))

        # Send processing acknowledgment to the UI (chat tab)
        await manager.broadcast({
            "type": "message_received",
            "session_id": session_id,
            "message": "Processing your request..."
        })

        print(f"DEBUG: Initiating agent processing for user message: '{user_message[:100]}...'")
        responses = process_agent_stream_for_chat_display(planning_app.stream(state, stream_mode="values", config=config))
 
        # Send agent's final response to the UI (chat tab)
        if responses:
            final_response = responses[-1]
            await manager.send_agent_response("Requirements Agent", final_response, session_id)
            print(f"DEBUG: Agent sent final chat response: '{final_response[:100]}...'")

        

        # Mark activity as complete
        await manager.broadcast({
            "type": "activity_update",
            "activity": {
                "id": str(uuid4()),
                "type": "complete",
                "session_id": session_id,
                "message": f"Processed message: '{user_message[:50]}...' ",
                "time": "Just now"
            }
        })

        print(f"DEBUG: User message processing complete for session {session_id}.")
    
    except Exception as e:
        print(f"ERROR: AnProcessing 1 uploaded file(s) error occurred during message processing for session {session_id}: {str(e)}")
        await manager.send_agent_response("Error Agent", f"An error occurred: {str(e)}", session_id)

async def handle_session_cleanup_ws(message_data: dict, websocket: WebSocket):
    """Handle session cleanup via WebSocket"""
    try:
        session_id_to_clean = message_data.get("session_id")
        if session_id_to_clean:
            print(f"DEBUG: Executing explicit session cleanup for session: {session_id_to_clean}")
            config = {"configurable": {"thread_id": session_id_to_clean}}
            state = {"messages": [HumanMessage(content="cleanup if needed")]}
            planning_app.invoke(state, config=config)
            await manager.send_session_update(session_id_to_clean, "cleaned", "Session cleanup completed")
            print(f"DEBUG: Explicit cleanup completed for session: {session_id_to_clean}.")
        else:
            print("WARNING: No session_id provided for explicit cleanup request.")
            await manager.send_personal_message("Error: No session_id provided for cleanup", websocket)
    except Exception as e:
        print(f"ERROR: Explicit cleanup error for session {message_data.get('session_id')}: {str(e)}")
        await manager.send_personal_message(f"Cleanup error: {str(e)}", websocket)

# @app.post("/chat/")
@requirement_router.post("/chat/")
async def chat(
    session_id: str = Form(...),
    user_message: str = Form(...),
    user_id: str = Form(...),
    provider_kind: str = Form("azure_devops"),
    uploaded_files: List[UploadFile] = File(None)  # Accept multiple files
): 
    """REST endpoint for backward compatibility"""
    shared.set_agent_folder("requirements_agent")
    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(user_id)
    resolved_provider_kind = provider_kind or _session_provider_kinds.get(session_id) or "azure_devops"
    _session_provider_kinds[session_id] = resolved_provider_kind
    set_provider_kind(resolved_provider_kind)
    print(user_id,"@@@@@@@@@@@@@@@@@@@@")
    print(f"DEBUG: WebSocket context set for session: {session_id} in /chat endpoint (REST).")
    print(f"DEBUG: REST chat request received for session {session_id}, message: {user_message[:100]}...")

    if uploaded_files:
        print(f"DEBUG: {len(uploaded_files)} files received via REST.")
    input_directory = f"{esett.FILES}/{user_id}/{shared.get_agent_folder()}/{session_id}/input"
    # input_directory = shared.input_dir
    file_names = []
    first_message_in_new_session = False

    if shared.prev_session_id == "":
        shared.prev_session_id = session_id

    if shared.prev_session_id != session_id:
        first_message_in_new_session = True
        print(f"DEBUG: New session ({session_id}) detected in REST. Initiating cleanup of previous session ({shared.prev_session_id})...")
        config_cleanup = {"configurable": {"thread_id": shared.prev_session_id}}
        state_cleanup = {"messages": [HumanMessage(content="cleanup if needed")]}
        planning_app.invoke(state_cleanup, config=config_cleanup)
        shared.prev_session_id = session_id
        print(f"DEBUG: Previous session ({shared.prev_session_id}) cleaned up for REST endpoint.")

    config = {"configurable": {"thread_id": session_id}}
    
    if first_message_in_new_session:
        state = {"messages": [SystemMessage(content=SYS_MESSAGE)] + [HumanMessage(content=user_message)]}
    else:
        state = {"messages": [HumanMessage(content=user_message)]}

    os.makedirs(input_directory, exist_ok=True)

    # Process files if uploaded
    if uploaded_files:
        for uploaded_file in uploaded_files:
            base_name, extension = os.path.splitext(uploaded_file.filename)
            new_file_name = f"{base_name}{extension}"
            file_path = os.path.join(input_directory, new_file_name)
            file_names.append(file_path)

            # Write the file content to the input directory
            try:
                with open(file_path, "wb") as file:
                    file.write(await uploaded_file.read())
                print(f"DEBUG: Saved uploaded file (REST): {file_path}")
            except Exception as e:
                print(f"ERROR: Error saving uploaded file (REST) {uploaded_file.filename}: {str(e)}")
                return {"error": f"Failed to save file {uploaded_file.filename}: {str(e)}"}

        upload_messages = ", ".join(file_names)
        print(f"DEBUG: Files to be used by agent (REST): {upload_messages}")
        state["messages"].append(HumanMessage(content=f"please use the following files {upload_messages}"))

    print("DEBUG: Invoking agent for REST request.")
    responses = process_agent_stream_for_chat_display(planning_app.stream(state, stream_mode="values", config=config))

    response_data = {
        "conversation_id": session_id,
        "responses": responses[-1] if responses else "No response generated.",
        "output_filename": shared.output_file
    }
    
    if shared.output_file:
        print(f"DEBUG: Generated output file (REST): {shared.output_file}")

    shared.output_file = ""  # Reset for next operation
    print(f"DEBUG: REST request processed. Response data: {response_data}")
    return response_data

@requirement_router.get("/download/{filename}")
async def download_generated_file(filename: str):
    """Download endpoint for agent-generated files"""
    try:
        # Construct the file path - adjust based on your storage structure
        file_path = os.path.join("outputs", filename)
        
        # Security check - ensure file exists and is within outputs directory
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        # Additional security check to prevent directory traversal
        if not os.path.commonpath([os.path.realpath(file_path), os.path.realpath("outputs")]) == os.path.realpath("outputs"):
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Return the file
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type='application/octet-stream'
        )
        
    except Exception as e:
        print(f"Error downloading file {filename}: {str(e)}")
        raise HTTPException(status_code=500, detail="Download failed")




@requirement_router.get("/sessions")
async def get_sessions():
    return {
        "current_session": "req"
    }
