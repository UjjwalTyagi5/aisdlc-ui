# log_analysis_router.py
import os
import re
import json
import asyncio
from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import contextvars
import base64
from typing import List, Optional, Dict

from agents_orchestrator.monitoring_feedback_agent._byok import resolved_litellm_kwargs
from agents_orchestrator.monitoring_feedback_agent.pipeline_controller import process_latest_issue,process_pasted_logs_batch
import agents_orchestrator.monitoring_feedback_agent.shared_state as shared_state
# NEW: Import the follow-up handler
from agents_orchestrator.monitoring_feedback_agent.follow_up_handler import generate_follow_up_rest
# Import connection manager and utilities (assuming similar structure as requirements agent)
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE
from config.connection_manager import manager
from config.ws_helper import set_session_id, set_user_id
from config.websocket_utils import set_websocket_context
from config.agent_context import build_agent_input_text, set_agent_folder
from config import sdlcSettings

import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# --- Part 2: SECURELY CONFIGURE THE AI MODEL ---
# This part loads your secret key from the .env file and sets up the connection to Google's AI.
logger.info("Loading API key and configuring Gemini...")
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    raise ValueError(" ERROR: GOOGLE_API_KEY not found. Please create a .env file and add it.")

genai.configure(api_key=api_key)

esett = sdlcSettings()

FILES_BASE_DIR = "temp_files"
AGENT_NAME = "monitoring_agent"
STANDARD_GREETING_RESPONSE = "Hello! I am a log analysis and monitoring agent. I can help you analyze logs to find root causes and provide suggestions. Please paste your logs or upload a log file to get started."

monitoring_feedback_router_orchestrator = APIRouter()

# Session context variable
SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default=None)

# WebSocket logging handler (similar to requirements agent)
class WebsocketBroadcastHandler(logging.Handler):
    def emit(self, record):
        try:
            session_id = SESSION_ID.get()
        except LookupError:
            session_id = None
        
        message = self.format(record)
        activity = {
            "id": f"activity_{int(datetime.utcnow().timestamp()*1000)}_{uuid4().hex[:6]}",
            "message": message,
            "type": "log",
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "printData": None,
            "sessionId": session_id,
        }
        
        # Fire-and-forget broadcast so logging doesn't block
        asyncio.create_task(manager.broadcast({
            "type": "activity_update",
            "activity": activity
        }))

# Configure the logger for monitoring agent
logger = logging.getLogger("monitoring_agent")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(levelname)s: %(message)s")

ws_handler = WebsocketBroadcastHandler()
ws_handler.setFormatter(formatter)
logger.addHandler(ws_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


# --- Pre-Processor Agent (UPDATED)---
def classify_and_separate_input(full_text: str, has_files: bool, chat_history: List[Dict]) -> Dict:
    """
    Uses an LLM to classify user intent AND separate the question from log data.
    NOW includes 'follow-up' intent detection.
    """
    print("[LLM Pre-Processor] Classifying intent and separating input...")
    
    if has_files:
        print("[LLM Pre-Processor] Files detected. Assuming 'analyze' intent.")
        return {"intent": "analyze", "question": full_text, "log_data": ""}

    # NEW: Context for the LLM
    history_exists = bool(chat_history)

    prompt = f"""
    You are an expert text analysis system. Your task is to analyze the user's message and perform two tasks:
    1.  Classify the user's INTENT. The intent must be one of: "analyze", "greeting", "follow-up", or "unknown".
    2.  Separate the natural language QUESTION from the raw LOG DATA.

    - INTENT "greeting": If the user is saying hello, asking for help, or asking what you can do.
    - INTENT "analyze": If the user provides any log text for a new analysis.
    - INTENT "follow-up": ONLY if `history_exists` is true AND the user is asking a question related to the previous analysis (e.g., "tell me more", "explain that error", "what about the database?").
    - INTENT "unknown": For anything else.
    - QUESTION: The user's query. Default to the original text if unsure.
    - LOG DATA: ONLY the raw log text. Empty string if none is found.

    Context:
    - `history_exists`: {history_exists}

    User Input:
    ---
    {full_text}
    ---

    You MUST return a single, valid JSON object with the keys "intent", "question", and "log_data".

    Example 1 (New Analysis):
    Input: "what do these logs mean? 2024-05-22 10:25:00 [INFO] User logged in"
    Output: {{"intent": "analyze", "question": "what do these logs mean?", "log_data": "2024-05-22 10:25:00 [INFO] User logged in"}}

    Example 2 (Greeting):
    Input: "Hello, what can you do?"
    Output: {{"intent": "greeting", "question": "Hello, what can you do?", "log_data": ""}}
    
    Example 3 (Follow-up, assuming history_exists=true):
    Input: "Can you explain the NullPointerException in more detail?"
    Output: {{"intent": "follow-up", "question": "Can you explain the NullPointerException in more detail?", "log_data": ""}}
    """
    
    try:
        # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
        import litellm
        response = litellm.completion(
            messages=[{"role": "user", "content": prompt}],
            **resolved_litellm_kwargs(),
        )
        response_text = response.choices[0].message.content or ""
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            result_dict = json.loads(json_match.group(0))
            if all(k in result_dict for k in ["intent", "question", "log_data"]):
                return result_dict
            
    except Exception as e:
        print(f" ERROR in LLM Pre-Processor: {e}. Falling back.")

    return {"intent": "unknown", "question": full_text, "log_data": ""}

# WebSocket test endpoint
@monitoring_feedback_router_orchestrator.websocket("/test-ws")
async def test_websocket(websocket: WebSocket):
    set_agent_folder("orchestrator")
    await websocket.accept()
    await websocket.send_text("Monitoring Agent WebSocket connection successful!")
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Monitoring Agent Echo: {data}")
    except WebSocketDisconnect:
        logger.info("Test WebSocket disconnected")


# Main WebSocket endpoint
@monitoring_feedback_router_orchestrator.websocket("/ws")
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
            logger.info(f"WebSocket context set for session: {session_id}")

            if message_data.get("type") == "user_message_with_files":
                await process_user_message_ws(message_data, websocket, user_id)
            elif message_data.get("type") == "clear_agents":
                await manager.clear_agents()
                logger.info("Agents cleared via WebSocket request")
            elif message_data.get("type") == "session_cleanup":
                await handle_session_cleanup_ws(message_data, websocket)
            else:
                logger.info(f"Echoing message: {data}")
                await manager.send_personal_message(json.dumps({"type": "echo", "message": data}), websocket)
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected from WebSocket")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
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


# async def process_user_message_ws(message_data: dict, websocket: WebSocket, user_id: str):
#     """Process user message with files and send real-time updates via WebSocket"""
#     try:
#         session_id = message_data.get("session_id", str(uuid4()))
#         user_message = message_data.get("text", "")
#         files_data = message_data.get("files", [])
        
#         logger.info(f"Processing WebSocket message for session: {session_id}")

#         # --- UPDATED: Handle session and history management ---
#         if shared_state.prev_session_id != session_id:
#             logger.info(f"New session detected: {session_id}")
#             await manager.send_session_update(session_id, "cleanup", "Setting up new monitoring session...")
#             shared_state.prev_session_id = session_id
#             # NEW: Initialize history for new session
#             shared_state.chat_histories[session_id] = []
#             await manager.send_session_update(session_id, "ready", "Monitoring session ready")

#         # NEW: Retrieve chat history for the current session
#         chat_history = shared_state.chat_histories.get(session_id, [])
#         user_turn = {"role": "user", "content": user_message} # For history

#         # --- UPDATED: Pre-processing now includes history context ---
#         pre_processed_input = classify_and_separate_input(user_message, has_files=bool(files_data), chat_history=chat_history)
#         intent = pre_processed_input["intent"]
#         actual_user_question = pre_processed_input["question"]
#         pasted_log_data = pre_processed_input["log_data"]
        
#         logger.info(f"LLM classified intent as: '{intent}'")

#         # --- NEW: INTENT-BASED ROUTING WITH FOLLOW-UP ---
#         if intent == "follow-up":
#             logger.info("Handling 'follow-up' intent.")
#             last_analysis_context = None
#             for turn in reversed(chat_history):
#                 if turn['role'] == 'assistant' and turn['content'].get('analysis_detail'):
#                     last_analysis_context = turn['content']['analysis_detail'][0] # Get the first (and only) analysis object
#                     break
            
#             if not last_analysis_context:
#                 response_msg = "I seem to have lost the context of our last analysis. Please start a new analysis by providing logs."
#                 await manager.send_agent_response("Monitoring Agent", response_msg, session_id)
#                 return

#             follow_up_answer = generate_follow_up_rest(last_analysis_context, chat_history, actual_user_question)
            
#             assistant_turn = {"role": "assistant", "content": {"responses": follow_up_answer, "analysis_detail": []}}
#             shared_state.chat_histories[session_id].append(user_turn)
#             shared_state.chat_histories[session_id].append(assistant_turn)

#             await manager.send_agent_response("Monitoring Agent", follow_up_answer, session_id)
#             return

#         elif intent == "greeting":
#             logger.info("Intent is 'greeting'. Sending standard response.")
#             await manager.send_agent_response("Monitoring Agent", STANDARD_GREETING_RESPONSE, session_id)
#             return

#         elif intent == "unknown":
#             logger.info("Intent is 'unknown'. Sending clarification request.")
#             response_msg = "I'm sorry, I couldn't understand your request. Please rephrase or specify if you want me to analyze logs."
#             await manager.send_agent_response("Monitoring Agent", response_msg, session_id)
#             return
        
#         # --- ORIGINAL ANALYSIS FLOW (INTENT IS "ANALYZE") ---
#         # NEW: If starting a new analysis, clear the old history for this session.
#         logger.info("Handling 'analyze' intent. Clearing previous chat history for this session.")
#         shared_state.chat_histories[session_id] = []
#         final_results = []
        
#         if files_data:
#             # --- PATH 1: FILE UPLOAD ---
#             logger.info(f"Processing {len(files_data)} uploaded file(s) for session {session_id}")
#             await manager.send_file_processing_update(session_id, [f["name"] for f in files_data])
            
#             input_directory = f"{esett.FILES}/{user_id}/{AGENT_NAME}/{session_id}/input"
#             file_names = []
#             os.makedirs(input_directory, exist_ok=True)
            
#             full_log_content = ""
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
            
#             if full_log_content.strip():
#                 logger.info("Starting log analysis for FILE... Calling 'process_latest_issue'")
#                 final_results = process_latest_issue(input_data=full_log_content, user_message=actual_user_question)
#             else:
#                  await manager.send_agent_response("Monitoring Agent", "The uploaded files appear to be empty.", session_id)
#                  return

#         elif pasted_log_data.strip():
#             # --- PATH 2: PASTED TEXT ---
#             await manager.broadcast({ "type": "activity_update", "activity": { "id": str(uuid4()), "type": "processing", "session_id": session_id, "message": "Analyzing input with AI pre-processor...", "time": datetime.utcnow().strftime("%H:%M:%S") }})
            
#             logger.info("Starting log analysis for PASTED TEXT... Calling 'process_pasted_logs_batch'")
#             final_results = process_pasted_logs_batch(input_data=pasted_log_data, user_message=actual_user_question)

#         else:
#             # No files and no text
#             error_msg = "Please either upload a log file or paste log text into the message box."
#             await manager.send_agent_response("Monitoring Agent", error_msg, session_id)
#             return

#         # --- Common final response logic for both paths ---
#         await manager.broadcast({ "type": "activity_update", "activity": { "id": str(uuid4()), "type": "processing", "session_id": session_id, "message": "Analyzing logs for issues and patterns...", "time": datetime.utcnow().strftime("%H:%M:%S") }})

#         if not final_results:
#              bot_response_message = "I've analyzed the logs, but I couldn't find any actionable issues."
#         elif len(final_results) == 1:
#             bot_response_message = final_results[0].get('issue_description', 'Analysis complete. Here is the result.')
#         else:
#             bot_response_message = f"I analyzed the logs and found {len(final_results)} distinct issues. Here is the breakdown:"

#         await manager.send_agent_response("Monitoring Agent", bot_response_message, session_id)

#         if final_results:
#             await manager.broadcast({"type": "analysis_detail", "session_id": session_id, "detail": final_results})

#         # NEW: Save successful analysis to history
#         assistant_turn_content = {"responses": bot_response_message, "analysis_detail": final_results}
#         shared_state.chat_histories[session_id].append(user_turn)
#         shared_state.chat_histories[session_id].append({"role": "assistant", "content": assistant_turn_content})

#         await manager.broadcast({ "type": "activity_update", "activity": { "id": str(uuid4()), "type": "complete", "session_id": session_id, "message": f"Log analysis completed for: '{actual_user_question[:50]}...'", "time": datetime.utcnow().strftime("%H:%M:%S") }})

#         logger.info(f"Log analysis processing complete for session {session_id}")

#     except Exception as e:
#         error_msg = f"An error occurred during log analysis: {str(e)}"
#         logger.error(error_msg, exc_info=True)
#         await manager.send_agent_response("Error Agent", error_msg, session_id)

async def process_user_message_ws(message_data: dict, websocket: WebSocket, user_id: str):
    """Process user message with files and send real-time updates via WebSocket"""
    try:
        session_id = message_data.get("session_id", str(uuid4()))
        files_data = message_data.get("files", [])
        # NEW: accept orchestrator-provided messages
        incoming_messages = message_data.get("messages", [])
        user_message = message_data.get("text", "")
        conversation_context = message_data.get("conversation_context")
        task_intent = message_data.get("task_intent")
        pipeline_context = message_data.get("pipeline_context")
        logger.info(f"Processing WebSocket message for session: {session_id}")
        # --- Session + history reset ---
        if shared_state.prev_session_id != session_id:
            logger.info(f"New session detected: {session_id}")
            shared_state.prev_session_id = session_id
            shared_state.chat_histories[session_id] = []
            await manager.send_session_update(session_id, "ready", "Monitoring session ready")
        chat_history = shared_state.chat_histories.get(session_id, [])
        # --- Normalize input ---
        if incoming_messages:
            # Convert orchestrator messages → simple role-content dicts
            history_msgs = []
            for m in incoming_messages:
                if isinstance(m, dict):
                    history_msgs.append({"role": m.get("role", "user"), "content": m.get("content", "")})
            chat_history.extend(history_msgs)
            user_message = history_msgs[-1]["content"] if history_msgs else user_message
        elif conversation_context or task_intent or pipeline_context:
            user_message = build_agent_input_text(
                conversation_context=conversation_context,
                task_intent=task_intent,
                pipeline_context=pipeline_context,
                pipeline_sections=("requirements", "development", "testing"),
            )
        user_turn = {"role": "user", "content": user_message}
        # --- Pre-process with context ---
        pre_processed_input = classify_and_separate_input(
            user_message, has_files=bool(files_data), chat_history=chat_history
        )
        intent, actual_user_question, pasted_log_data = (
            pre_processed_input["intent"],
            pre_processed_input["question"],
            pre_processed_input["log_data"],
        )
        logger.info(f"LLM classified intent as: '{intent}'")
        # === Intent routing ===
        if intent == "follow-up":
            logger.info("Handling 'follow-up' intent.")
            last_analysis_context = next(
                (turn["content"]["analysis_detail"][0]
                 for turn in reversed(chat_history)
                 if turn["role"] == "assistant" and turn["content"].get("analysis_detail")),
                None,
            )
            if not last_analysis_context:
                response_msg = "I lost the context of our last analysis. Please provide new logs."
            else:
                response_msg = generate_follow_up_rest(last_analysis_context, chat_history, actual_user_question)
            assistant_turn = {"role": "assistant", "content": {"responses": response_msg, "analysis_detail": []}}
            chat_history.append(user_turn)
            chat_history.append(assistant_turn)
            await manager.send_agent_response("Monitoring Agent", response_msg, session_id)
            return
        elif intent == "greeting":
            await manager.send_agent_response("Monitoring Agent", STANDARD_GREETING_RESPONSE, session_id)
            return
        elif intent == "unknown":
            response_msg = "I couldn't understand your request. Please rephrase or paste/upload logs."
            await manager.send_agent_response("Monitoring Agent", response_msg, session_id)
            return
        # === Analysis flow ===
        shared_state.chat_histories[session_id] = []  # reset for new analysis
        final_results = []
        if files_data:
            full_log_content = ""
            input_directory = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input"
            os.makedirs(input_directory, exist_ok=True)
            for f in files_data:
                fname = f["name"]
                file_path = os.path.join(input_directory, fname)
                with open(file_path, "wb") as fd:
                    fd.write(base64.b64decode(f["content"]))
                full_log_content += base64.b64decode(f["content"]).decode("utf-8", errors="ignore")
            final_results = process_latest_issue(input_data=full_log_content, user_message=actual_user_question)
        elif pasted_log_data.strip():
            final_results = process_pasted_logs_batch(input_data=pasted_log_data, user_message=actual_user_question)
        else:
            await manager.send_agent_response("Monitoring Agent", "Please paste logs or upload a file.", session_id)
            return
        bot_response_message = (
            final_results[0].get("issue_description", "Analysis complete.")
            if len(final_results) == 1
            else (f"I found {len(final_results)} issues." if final_results else "No actionable issues found.")
        )
        # Save history
        assistant_turn_content = {"responses": bot_response_message, "analysis_detail": final_results}
        shared_state.chat_histories[session_id].append(user_turn)
        shared_state.chat_histories[session_id].append({"role": "assistant", "content": assistant_turn_content})
        await manager.send_agent_response("Monitoring Agent", bot_response_message, session_id)
    except Exception as e:
        err = f"Error during monitoring analysis: {e}"
        logger.error(err, exc_info=True)
        await manager.send_agent_response("Monitoring Agent", err, session_id)

 


async def handle_session_cleanup_ws(message_data: dict, websocket: WebSocket):
    """Handle session cleanup via WebSocket"""
    try:
        session_id_to_clean = message_data.get("session_id")
        if session_id_to_clean:
            logger.info(f"Executing session cleanup for session: {session_id_to_clean}")
            
            shared_state.prev_session_id = ""
            # NEW: Also clear the chat history for the cleaned session
            if session_id_to_clean in shared_state.chat_histories:
                del shared_state.chat_histories[session_id_to_clean]
                logger.info(f"Chat history cleared for session: {session_id_to_clean}")

            await manager.send_session_update(session_id_to_clean, "cleaned", "Monitoring session cleanup completed")
            logger.info(f"Session cleanup completed for: {session_id_to_clean}")
        else:
            logger.warning("No session_id provided for cleanup request")
            await manager.send_personal_message("Error: No session_id provided for cleanup", websocket)
    except Exception as e:
        error_msg = f"Session cleanup error: {str(e)}"
        logger.error(error_msg)
        await manager.send_personal_message(error_msg, websocket)


# REST endpoint (UPDATED with full follow-up logic)
@monitoring_feedback_router_orchestrator.post("/chat/")
async def chat(
    session_id: str = Form(...),
    user_message: str = Form(None),
    conversation_context: str = Form(None),
    task_intent: str = Form(None),
    pipeline_context: str = Form(None),
    user_id: str = Form(...),
    uploaded_files: Optional[List[UploadFile]] = File(None),
    messages: Optional[str] = Form(None)  # orchestrator may send JSON string of messages
):
    """REST endpoint with full history support"""
    if shared_state.prev_session_id != session_id:
        shared_state.prev_session_id = session_id
        shared_state.chat_histories[session_id] = []
    chat_history = shared_state.chat_histories.get(session_id, [])
    # Build history
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
            pipeline_sections=("requirements", "development", "testing"),
        )
        state_messages.append({"role": "user", "content": final_msg})
    # Use last user turn as current
    user_turn = state_messages[-1] if state_messages else {"role": "user", "content": ""}
    actual_user_message = user_turn["content"]
    pre_processed_input = classify_and_separate_input(
        actual_user_message, has_files=bool(uploaded_files), chat_history=chat_history
    )
    intent, actual_user_question, pasted_log_data = (
        pre_processed_input["intent"],
        pre_processed_input["question"],
        pre_processed_input["log_data"],
    )
    assistant_turn_content = {}
    if intent == "follow-up":
        last_analysis_context = next(
            (turn["content"]["analysis_detail"][0]
             for turn in reversed(chat_history)
             if turn["role"] == "assistant" and turn["content"].get("analysis_detail")),
            None,
        )
        if not last_analysis_context:
            assistant_turn_content = {"responses": "I've lost the last analysis context. Please start again.", "analysis_detail": []}
        else:
            follow_up_answer = generate_follow_up_rest(last_analysis_context, chat_history, actual_user_question)
            assistant_turn_content = {"responses": follow_up_answer, "analysis_detail": []}
    elif intent == "greeting":
        assistant_turn_content = {"responses": STANDARD_GREETING_RESPONSE, "analysis_detail": []}
    elif intent == "unknown":
        assistant_turn_content = {"responses": "I couldn't understand. Please paste logs or upload files.", "analysis_detail": []}
    elif intent == "analyze":
        shared_state.chat_histories[session_id] = []  # reset
        final_results = []
        if uploaded_files:
            full_log_content = "".join([(await f.read()).decode("utf-8") for f in uploaded_files])
            final_results = process_latest_issue(input_data=full_log_content, user_message=actual_user_question)
        elif pasted_log_data.strip():
            final_results = process_pasted_logs_batch(input_data=pasted_log_data, user_message=actual_user_question)
        bot_response_message = (
            final_results[0].get("issue_description", "Analysis complete.")
            if len(final_results) == 1
            else (f"I found {len(final_results)} issues." if final_results else "No actionable issues.")
        )
        assistant_turn_content = {"responses": bot_response_message, "analysis_detail": final_results}
    # Save history
    chat_history.append(user_turn)
    chat_history.append({"role": "assistant", "content": assistant_turn_content})
    return {
        "conversation_id": session_id,
        "responses": assistant_turn_content.get("responses", ""),
        "analysis_detail": assistant_turn_content.get("analysis_detail", []),
    }
 

 
@monitoring_feedback_router_orchestrator.get("/download/{filename}")
async def download_generated_file(filename: str):
    set_agent_folder("orchestrator")
    """
    Download endpoint. Since our agent doesn't create files to download,
    we return a helpful error message.
    """
    raise HTTPException(
        status_code=404,
        detail=f"File not found. The {AGENT_NAME} does not currently generate downloadable files."
    )


@monitoring_feedback_router_orchestrator.get("/sessions")
async def get_sessions():
    set_agent_folder("orchestrator")
    """Returns the current agent's identifier and the last known session ID."""
    return {
        "agent_name": AGENT_NAME,
        "current_session": shared_state.prev_session_id
    }
