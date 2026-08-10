import os
import asyncio
import json
from typing import List, Dict, Any
from fastapi import FastAPI, Form, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from agents.planning import app as planning_app  # Your agent app
from dotenv import load_dotenv
import google.generativeai as genai
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from uuid import uuid4
from config import shared
from websocket_utils import set_websocket_context

load_dotenv()

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
2. Your first action MUST be to call the `upload_file` tool for every local file required.
3. Wait for the `upload_file` tool to return a file name reference.
4. Your next action must be to call the appropriate processing tool. You MUST pass the file name references you received from the previous step.
5. If the user asks for cleanup, you must call `delete_file`, using the same file name reference.
6. Between each query, provide an interactive response to the user. If any content was generated, you MUST include that in your response.
7. If asked to save a file, save it only to outputs/file.docx where file is one of (brd, pdd, mom, risk_register, user_stories, summary) accordingly.
8. For Saving a file, double-check that the output path is of the format outputs/file.docx where file is one of (brd, pdd, mom, risk_register, user_stories, summary).

You MUST Remember the Core instructions.
"""

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.agents: Dict[str, Dict] = {}  # For simulated agent joining
        self.sessions: Dict[str, Dict] = {}  # Not actively used

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            print(f"Error sending personal message: {e}")

    async def broadcast(self, message: dict):
        if self.active_connections:
            message_str = json.dumps(message)
            disconnected = []
            for connection in self.active_connections:
                try:
                    await connection.send_text(message_str)
                except Exception as e:
                    print(f"Error broadcasting to connection: {e}")
                    disconnected.append(connection)
            # Remove disconnected connections
            for conn in disconnected:
                self.disconnect(conn)

    async def add_agent(self, agent_data: dict):
        agent_id = str(uuid4())
        self.agents[agent_id] = {
            **agent_data,
            "id": agent_id,
            "status": "joining",
            "progress": 0
        }
        await self.broadcast({
            "type": "agent_joining",
            "agent": self.agents[agent_id]
        })
        # Simulate agent progress
        for progress in [25, 50, 75, 100]:
            await asyncio.sleep(0.1)  # Reduced sleep for faster simulation
            if agent_id in self.agents:
                self.agents[agent_id]["progress"] = progress
                await self.broadcast({
                    "type": "agent_progress",
                    "agent_id": agent_id,
                    "progress": progress
                })
        # Mark agent as active
        if agent_id in self.agents:
            self.agents[agent_id]["status"] = "active"
            await self.broadcast({
                "type": "agent_active",
                "agent_id": agent_id,
                "response_time": 1.2
            })
        return agent_id

    async def send_agent_response(self, agent_name: str, message: str, session_id: str):
        await self.broadcast({
            "type": "agent_response",
            "agent_name": agent_name,
            "message": message,
            "session_id": session_id
        })

    async def send_file_processing_update(self, session_id: str, file_names: List[str]):
        await self.broadcast({
            "type": "file_processing",
            "session_id": session_id,
            "files": file_names,
            "message": f"Processing {len(file_names)} file(s)..."
        })

    async def send_session_update(self, session_id: str, status: str, message: str):
        await self.broadcast({
            "type": "session_update",
            "session_id": session_id,
            "status": status,
            "message": message
        })

    async def clear_agents(self):
        self.agents.clear()
        await self.broadcast({"type": "agents_cleared"})

manager = ConnectionManager()

def process_agent_stream_for_chat_display(stream):
    responses = []
    for s in stream:
        for message in s["messages"]:
            if isinstance(message, (HumanMessage, ToolMessage, SystemMessage)):
                continue
            if not message.tool_calls:
                responses.append(message.content)
    return responses

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            session_id = message_data.get("session_id", str(uuid4()))
            set_websocket_context(manager, session_id)
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

async def process_user_message_ws(message_data: dict, websocket: WebSocket):
    """Process user message with files and send real-time updates via WebSocket"""
    try:
        print(message_data,"@@!!###")
        session_id = message_data.get("session_id", str(uuid4()))
        user_message = message_data.get("text", "")
        files_data = message_data.get("files", [])  # Array of {name, content} objects

        input_directory = shared.input_dir
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
                base_name, extension = os.path.splitext(file_data["name"])
                new_file_name = f"{base_name}_{session_id}{extension}"
                file_path = os.path.join(input_directory, new_file_name)
                file_names.append(file_path)

                # Write the file content (assuming base64 encoded content)
                import base64
                try:
                    file_content = base64.b64decode(file_data["content"])
                    with open(file_path, "wb") as file:
                        file.write(file_content)
                    print(f"DEBUG: Successfully saved uploaded file: {file_path}")
                except Exception as e:
                    print(f"ERROR: Error processing file {file_data['name']}: {str(e)}")
                    await manager.send_agent_response("Error Agent", f"Error processing file {file_data['name']}: {str(e)}", session_id)
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

        # Send output file information if available
        if shared.output_file:
            await manager.broadcast({
                "type": "file_generated",
                "session_id": session_id,
                "filename": shared.output_file,
                "message": f"Generated file: {shared.output_file}"
            })
            print(f"DEBUG: Notified UI about generated file: {shared.output_file}")
            shared.output_file = ""  # Reset for next operation

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
        print(f"ERROR: An error occurred during message processing for session {session_id}: {str(e)}")
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

@app.post("/chat/")
async def chat(
    session_id: str = Form(...),
    user_message: str = Form(...),
    uploaded_files: List[UploadFile] = File(None)  # Accept multiple files
):
    """REST endpoint for backward compatibility"""
    set_websocket_context(manager, session_id)
    print(f"DEBUG: WebSocket context set for session: {session_id} in /chat endpoint (REST).")
    print(f"DEBUG: REST chat request received for session {session_id}, message: {user_message[:100]}...")

    if uploaded_files:
        print(f"DEBUG: {len(uploaded_files)} files received via REST.")
    input_directory = shared.input_dir
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
            new_file_name = f"{base_name}_{session_id}{extension}"
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

# Health check endpoint
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "active_connections": len(manager.active_connections),
        "active_agents": len(manager.agents),  # This tracks simulated agents
        "current_session": shared.prev_session_id
    }

# Get active sessions endpoint
@app.get("/sessions")
async def get_sessions():
    return {
        "current_session": shared.prev_session_id,
        "active_connections": len(manager.active_connections)
    }

if __name__ == "__main__":
    import uvicorn
    # Make sure to run with reload in development for easy iteration
    uvicorn.run(app, host="0.0.0.0", port=5004, reload=True)