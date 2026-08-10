import os
import asyncio
import json 
import contextvars

import logging

from datetime import datetime

import uuid
import base64
import aiofiles
import asyncio
from typing import List, Dict, Any
from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, UploadFile, File, APIRouter
from fastapi.middleware.cors import CORSMiddleware
# Removed planning app import
from dotenv import load_dotenv
import sys
import google.generativeai as genai
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from uuid import uuid4
from agents_orchestrator.development_agent.config import shared
from config.websocket_utils import set_websocket_context
from pydantic import BaseModel
from pathlib import Path
from agents_orchestrator.development_agent.util import save_python_file, save_zip_file
from config.ws_helper import set_session_id, broadcast_log,set_user_id
from config.connection_manager import manager
 
# Import your development graph
from agents_orchestrator.development_agent.Nodes.node import graph
from config.websocket_utils import set_websocket_context
import sys, inspect

from config.agent_context import get_agent_folder, set_agent_folder
from config import sdlcSettings
esett = sdlcSettings()

load_dotenv()

development_router = APIRouter()


SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default=None)

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

logger = logging.getLogger("development_agent")

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


# UPLOAD_ZIP_DIR       = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/uploaded_zip"
# UPLOAD_CODE_DIR      = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/uploaded_code"
# UNZIPPED_DIR         = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/unzipped"
# PROCESSED_ZIP_DIR    = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/processed/zips"
# PROCESSED_PROJ_DIR   = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/processed/projects"

# Create directories for development module
# UPLOAD_ZIP_DIR, UPLOAD_CODE_DIR, UNZIPPED_DIR, PROCESSED_ZIP_DIR, PROCESSED_PROJ_DIR = create_directory()

SYS_MESSAGE = """
You are a highly specialised agent that operates as a state machine. Your preferred mode of action is to use the tools you have been provided.
You can answer in text and also correct mistakes in generated content.
You can answer directly any query that is not related to documents or files.
When updating content, DO NOT invent information or change formatting.
You MUST Maintain consistency with the language of the generated content.
You MUST not infinitely loop.
YOU MUST Follow these core instructions very carefully:

- WHEN USING UPDATE MAKE SURE ALL THE REQUIRED CONTEXT IS COMPILED CAREFULLY
- DO NOT REUPLOAD OR REUSE A TOOL WHEN IT DID NOT ERROR OR THE USER DID NOT EXPLICITLY ASK FOR SOMETHING THAT NEEDS A TOOL.
- DO NOT REUSE DELETE FILE IF IT ERRORS.
- ONLY SAVE TO DOCX WHEN EXPLICITLY ASKED TO SAVE IT.
- ONLY CLEANUP WHEN THE USER ASKS FOR IT.
- WHEN THE USER ASKS FOR AN UPDATE, MAKE SURE TO INCLUDE ALL THEIR HISTORICAL NEEDS AND REFERENCE THE CORRECT CONTENT.
- FOR a general query or update YOU MUST curate the query as an LLM prompt before calling the tool.
- WHEN YOU CAN USE A TOOL YOU MUST USE A TOOL.
- PROVIDE AN APOLOGY RESPONSE FOR QUERIES THAT ARE NOT RELATED TO DOCUMENT PROCESSING.
- YOU CAN ANSWER SIMPLE GREETINGS FROM THE USER.
- ANY GENERATED CONTENT MUST BE INCLUDED IN YOUR RESPONSE TO THE USER.

Follow this strict procedure:
1. Analyze the User's request to identify any local file paths mentioned or any previous content referenced.
2. If a local file path is provided or expected, call the 'uploaded_file' tool.
3. Otherwise, use the conversation context provided by the orchestrator.
4. Your next action must be to call the appropriate processing tool. You MUST pass the file name references you received from the previous step.
5. If the user asks for cleanup, you must call `delete_file`, using the same file name reference.
6. Between each query, provide an interactive response to the user. If any content was generated, you MUST include that in your response.
7. If asked to save a file, save it only to outputs/file.docx where file is one of (brd, pdd, mom, risk_register, user_stories, summary) accordingly.
8. For Saving a file, double-check that the output path is of the format outputs/file.docx where file is one of (brd, pdd, mom, risk_register, user_stories, summary).

You MUST Remember the Core instructions.
"""

# Pydantic models
class ChatInput(BaseModel):
    session_id: str
    messages: str
    new_chat: bool = False

def process_agent_stream_for_chat_display(stream):
    """Helper function for processing agent streams - currently unused"""
    responses = []
    for s in stream:
        for message in s["messages"]:
            if isinstance(message, (HumanMessage, ToolMessage, SystemMessage)):
                continue
            if not message.tool_calls:
                responses.append(message.content)
    return responses




@development_router.websocket("/ws")


async def websocket_endpoint(websocket: WebSocket):
    set_agent_folder("development_agent")
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            session_id = message_data.get("session_id", str(uuid4()))
            set_websocket_context(manager, session_id)
            set_session_id(session_id)
            print(f"DEBUG: WebSocket context set for session: {session_id} in /ws endpoint.")

            if message_data.get("type") == "user_message_with_files":
                print("assddddd")
                await process_user_message_ws(message_data, websocket)
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


async def process_development_message_ws(message_data: dict, websocket: WebSocket, user_id):
    """Process development-related messages via WebSocket"""
    try:
        session_id = message_data.get("session_id", str(uuid4()))
        user_message = message_data.get("messages", "")
        files_data = message_data.get("files", [])  # Array of {name, content} objects
 
        config = {"configurable": {"thread_id": str(session_id)}}
        
        # Send processing acknowledgment
         
        target_dir_py = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/uploaded_code"
        target_dir_zip = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/uploaded_zip"
        saved_files = ""

        # Process files if provided
        if files_data:
            
            
            for file_data in files_data:
                file_name = file_data["name"]
                file_content = file_data["content"]
                
                # Create a mock UploadFile-like object for processing
                import tempfile
                import base64
                
                try:
                    # Decode base64 content
                    decoded_content = base64.b64decode(file_content)
                    
                    # Create temporary file
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]) as temp_file:
                        temp_file.write(decoded_content)
                        temp_file_path = temp_file.name
                    
                    # Create mock upload file object
                    class MockUploadFile:
                        def __init__(self, filename, file_path):
                            self.filename = filename
                            self.file_path = file_path
                        
                        async def read(self):
                            with open(self.file_path, 'rb') as f:
                                return f.read()
                    
                    mock_file = MockUploadFile(file_name, temp_file_path)
                    
                    if file_name.endswith(".py"):
                        print("Uploading Python file via WebSocket")
                        saved_files = save_python_file(mock_file, target_dir_py)
                        print("Python File Uploaded Successfully")
                    elif file_name.endswith(".zip"):
                        print("Uploading ZIP file via WebSocket")
                        saved_files = save_zip_file(mock_file, target_dir_zip)
                        print("ZIP File Uploaded Successfully")
                    
                    # Clean up temp file
                    os.unlink(temp_file_path)
                    
                except Exception as e:
                    print(f"Error processing file {file_name}: {str(e)}")
                    await manager.send_agent_response("Development Agent", f"Error processing file {file_name}: {str(e)}", session_id)

        # Prepare state for development graph
        if saved_files:
            state = {
                "messages": user_message,
                "saved_files": saved_files if saved_files is not None else []
            }
        else:
            state = {
                "messages": user_message
            }

        # Process with development graph
        response = await graph.ainvoke(state, config=config)
        output_message = response["messages"][-1].content

       

       

        print(f"DEBUG: Development message processing complete for session {session_id}.")

    except Exception as e:
        print(f"ERROR: Development message processing error for session {session_id}: {str(e)}")
        
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

# async def process_user_message_ws(message_data: dict, websocket: WebSocket, user_id):
#     """Process user message with files and send real-time updates via WebSocket"""
#     try:
#         print(message_data,"@@!!###")
#         session_id = message_data.get("session_id", str(uuid4()))
#         user_message = message_data.get("text", "")
#         files_data = message_data.get("files", [])  # Array of {name, content} objects
#         # input_directory = shared.input_dir
#         input_directory = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input"
#         file_names = []
#         first_message_in_new_session = False

#         if shared.prev_session_id == "":
#             shared.prev_session_id = session_id

#         if shared.prev_session_id != session_id:
#             first_message_in_new_session = True
#             print(f"DEBUG: New session ({session_id}) detected. Initiating cleanup of previous session ({shared.prev_session_id})...")
#             await manager.send_session_update(session_id, "cleanup", "Cleaning up previous session...")
#             config_cleanup = {"configurable": {"thread_id": shared.prev_session_id}}
#             state_cleanup = {"messages": [HumanMessage(content="cleanup if needed")]}
#             planning_app.invoke(state_cleanup, config=config_cleanup)
#             shared.prev_session_id = session_id
#             await manager.send_session_update(session_id, "ready", "Session cleaned up, ready for new requests")
#             print(f"DEBUG: Previous session ({shared.prev_session_id}) cleaned up. Ready for {session_id}.")

#         config = {"configurable": {"thread_id": session_id}}
#         os.makedirs(input_directory, exist_ok=True)

#         # Process files if provided
#         if files_data:
#             await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])
#             print(f"DEBUG: Processing {len(files_data)} uploaded file(s) for session {session_id}.")
#             for file_data in files_data:
#                 try:
#                     # print(file_dat"==============")
#                     base_name, extension = os.path.splitext(file_data.get("name"))
#                     print(base_name,"=====++++")
#                     new_file_name = f"{base_name}{extension}"
#                     file_path = os.path.join(input_directory, new_file_name)
#                     file_names.append(file_path)
#                     if "content" not in file_data:
#                         try:
#                             encoded_files = await encode_files_to_base64(file_names)
#                         except Exception as e:
#                             print(e,"1111111111111")
#                         try:
#                             file_content = base64.b64decode(encoded_files[0]["content"])
#                             with open(file_path, "wb") as file:
#                                 file.write(file_content)
#                             print(f"DEBUG: Successfully saved uploaded file: {file_path}")
#                         except Exception as e:
#                             print(f"ERROR: Error processing file {encoded_files['name']}: {str(e)}")
#                             await manager.send_agent_response("Error Agent", f"Error processing file {encoded_files['name']}: {str(e)}", session_id)
#                             continue
#                     else:
#                         file_content_bytes = base64.b64decode(file_data["content"])
#                         with open(file_path, "wb") as file:
#                             file.write(file_content_bytes)
                        
#                         full_log_content += file_content_bytes.decode("utf-8", errors="ignore") + "\n"
#                         logger.info(f"Successfully saved uploaded file: {file_path}")
#                 except Exception as e:
#                     error_msg = f"Error processing file {file_data['name']}: {str(e)}"
#                     logger.error(error_msg)
#                     await manager.send_agent_response("Error Agent", error_msg, session_id)
#                     continue


#         # Prepare state messages
#         state = {
#             "messages": [SystemMessage(content=SYS_MESSAGE)] + [HumanMessage(content=user_message)] if first_message_in_new_session
#             else [HumanMessage(content=user_message)]
#         }

#         if file_names:
#             upload_messages = ", ".join(file_names)
#             state["messages"].append(HumanMessage(content=f"please use the following files {upload_messages}"))

#         # Send processing acknowledgment to the UI (chat tab)
#         await manager.broadcast({
#             "type": "message_received",
#             "session_id": session_id,
#             "message": "Processing your request..."
#         })

#         print(f"DEBUG: Initiating agent processing for user message: '{user_message[:100]}...'")
#         responses = process_agent_stream_for_chat_display(planning_app.stream(state, stream_mode="values", config=config))
 
#         # Send agent's final response to the UI (chat tab)
#         if responses:
#             final_response = responses[-1]
#             await manager.send_agent_response("Requirements Agent", final_response, session_id)
#             print(f"DEBUG: Agent sent final chat response: '{final_response[:100]}...'")

        

#         # Mark activity as complete
#         await manager.broadcast({
#             "type": "activity_update",
#             "activity": {
#                 "id": str(uuid4()),
#                 "type": "complete",
#                 "session_id": session_id,
#                 "message": f"Processed message: '{user_message[:50]}...' ",
#                 "time": "Just now"
#             }
#         })

#         print(f"DEBUG: User message processing complete for session {session_id}.")
    
#     except Exception as e:
#         print(f"ERROR: AnProcessing 1 uploaded file(s) error occurred during message processing for session {session_id}: {str(e)}")
#         await manager.send_agent_response("Error Agent", f"An error occurred: {str(e)}", session_id)

async def handle_session_cleanup_ws(message_data: dict, websocket: WebSocket):
    """Handle session cleanup via WebSocket"""
    try:
        session_id_to_clean = message_data.get("session_id")
        if session_id_to_clean:
            print(f"DEBUG: Executing explicit session cleanup for session: {session_id_to_clean}")
            
            print(f"DEBUG: Explicit cleanup completed for session: {session_id_to_clean}.")
        else:
            print("WARNING: No session_id provided for explicit cleanup request.")
            
    except Exception as e:
        print(f"ERROR: Explicit cleanup error for session {message_data.get('session_id')}: {str(e)}")
       

# REST Endpoints

# @development_router.post("/chat/")
# async def chat(
#     session_id: str = Form(...),
#     user_message: str = Form(...),
#     user_id: str = Form(...),
#     uploaded_files: List[UploadFile] = File(None)  # Accept multiple files
# ):
#     """REST endpoint for general processing"""
   
#     print(f"DEBUG: WebSocket context set for session: {session_id} in /chat endpoint (REST).")
#     print(f"DEBUG: REST chat request received for session {session_id}, message: {user_message[:100]}...")
 
#     if uploaded_files:
#         print(f"DEBUG: {len(uploaded_files)} files received via REST.")
#     input_directory = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input"
#     file_names = []
#     first_message_in_new_session = False

#     if shared.prev_session_id == "":
#         shared.prev_session_id = session_id

#     if shared.prev_session_id != session_id:
#         first_message_in_new_session = True
#         print(f"DEBUG: New session ({session_id}) detected in REST. Previous session was ({shared.prev_session_id})")
#         shared.prev_session_id = session_id
#         print(f"DEBUG: New session ({session_id}) initialized for REST endpoint.")

#     config = {"configurable": {"thread_id": session_id}}
   
#     os.makedirs(input_directory, exist_ok=True)

#     # Process files if uploaded
#     if uploaded_files:
#         for uploaded_file in uploaded_files:
#             base_name, extension = os.path.splitext(uploaded_file.filename)
#             new_file_name = f"{base_name}_{session_id}{extension}"
#             file_path = os.path.join(input_directory, new_file_name)
#             file_names.append(file_path)

#             # Write the file content to the input directory
#             try:
#                 with open(file_path, "wb") as file:
#                     file.write(await uploaded_file.read())
#                 print(f"DEBUG: Saved uploaded file (REST): {file_path}")
#             except Exception as e:
#                 print(f"ERROR: Error saving uploaded file (REST) {uploaded_file.filename}: {str(e)}")
#                 return {"error": f"Failed to save file {uploaded_file.filename}: {str(e)}"}

#     # Prepare state for processing
#     state = {
#         "messages": user_message,
#         "files": file_names if file_names else []
#     }

#     print("DEBUG: Invoking agent for REST request.")
#     response = await graph.ainvoke(state, config=config)
#     output_message = response["messages"][-1].content

#     response_data = {
#         "conversation_id": session_id,
#         "responses": output_message,
#         "output_filename": shared.output_file if shared.output_file else ""
#     }
   
#     if shared.output_file:
#         print(f"DEBUG: Generated output file (REST): {shared.output_file}")

#     shared.output_file = ""  # Reset for next operation
#     print(f"DEBUG: REST request processed. Response data: {response_data}")
#     return response_data

# @app.post("/development/")
@development_router.post("/chat/")
async def development_chat(
    session_id: str = Form(...),
    user_message: str = Form(...),
    user_id: str = Form(...),
    uploaded_files: UploadFile | None = None
):
    """REST endpoint for development-related tasks"""
    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(user_id)
    print(f"DEBUG: Development REST request received for session {session_id}, message: {user_message[:100]}...")

    config = {"configurable": {"thread_id": str(session_id)}}

    target_dir_py = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/uploaded_code"
    target_dir_zip = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/uploaded_zip"

    saved_files = ""

    if uploaded_files and uploaded_files.filename.endswith(".py"):
        print("Uploading Python file")
        saved_files = save_python_file(uploaded_files, target_dir_py)
        print("Python File Uploaded Successfully")
    elif uploaded_files and uploaded_files.filename.endswith(".zip"):
        print("Uploading ZIP file")
        saved_files = save_zip_file(uploaded_files, target_dir_zip)
        print("ZIP File Uploaded Successfully")

    if saved_files:
        state = {
            "messages": user_message,
            "saved_files": saved_files if saved_files is not None else []
        }
    else:
        state = {
            "messages": user_message
        }

    response = await graph.ainvoke(state, config=config)
    output_message = response["messages"][-1].content

    response_data = {
        "conversation_id": session_id,
        "responses": output_message,
        "output_filename": ""
    }
    
    print(f"DEBUG: Development REST request processed. Response data: {response_data}")
    return response_data

@development_router.get("/sessions")
async def get_sessions():
    set_agent_folder("development_agent")
    return {
        "current_session": "dev"
    }


# ── Azure Pipelines endpoints ─────────────────────────────────────────────────

from fastapi import HTTPException as _HTTPException
from config.connector_client import get_connector, ConnectorNotInstalledError
import httpx as _httpx


def _get_ado_creds() -> dict:
    try:
        return get_connector("azure_devops")
    except ConnectorNotInstalledError as exc:
        raise _HTTPException(status_code=400, detail=str(exc)) from exc


@development_router.get("/pipelines/list")
async def list_pipelines(project: str):
    """List all pipelines in the given ADO project."""
    if not project.strip():
        raise _HTTPException(status_code=400, detail="project is required.")
    creds = _get_ado_creds()
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
        raise _HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


@development_router.post("/pipelines/trigger")
async def trigger_pipeline(project: str, pipeline_id: int, branch: str = "main"):
    """Trigger a pipeline run on the given branch."""
    if not project.strip():
        raise _HTTPException(status_code=400, detail="project is required.")
    creds = _get_ado_creds()
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
        raise _HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc


@development_router.get("/pipelines/status")
async def pipeline_run_status(project: str, pipeline_id: int, run_id: int):
    """Get the current status of a pipeline run."""
    if not project.strip():
        raise _HTTPException(status_code=400, detail="project is required.")
    creds = _get_ado_creds()
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
        raise _HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
