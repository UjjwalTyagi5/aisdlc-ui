import uuid
import os
import httpx
import google.generativeai as genai
from datetime import datetime
from typing import Dict, Any, List, Annotated
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from dotenv import load_dotenv
from fastapi import APIRouter, Form, Request, Response, Cookie
from fastapi.responses import JSONResponse
from fastapi import FastAPI
import json

orchestrator_router = APIRouter()
 
# Load environment variables
from config.env import GOOGLE_API_KEY
genai.configure(api_key=GOOGLE_API_KEY)
 
# Instantiate Gemini llm
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
 
SERVER_EPOCH = str(uuid.uuid4())
 
# --- Remove SESSION_FILE and manual load/save ---
 
# ---- Define Orchestrator State ----
class OrchestratorState(Dict[str, Any]):
    user_input: str
    stage: str
    messages: List[BaseMessage] # List of messages as context instead of raw string
    session_id: str
    history: List[str]
    artifact_map: Dict[str, List[str]]

example_output = {
  "requirements": [
    "The user wants to create a BRD"
  ],
  "design_architecture": [
    "The user wants to create a Design Architecture"
  ],
  "development": [],
  "testing": [],
  "deployment": [],
  "monitoring_feedback": []
} 
async def decide_stage(state: OrchestratorState) -> OrchestratorState:
    state["loop_count"] = state.get("loop_count", 0) + 1
    print(f"[DEBUG] Loop {state['loop_count']}: Deciding next stage...")
    if state["loop_count"] > 5:
        print("⚠️ Max loop count reached. Forcing stage='done'")
        state["stage"] = "done"
        return state
 
    user_input = state["user_input"]
    context_messages: List[BaseMessage] = state.get("messages", [])
 
    # Prepare context prompt text from messages for stage decision (flatten messages)
    # context = "\n".join([msg.content for msg in context_messages])
    context = "\n".join([f"[{msg.__class__.__name__}] {msg.content}" for msg in context_messages])
 

    decision_prompt = f"""
 
    You are an advanced Orchestrator Agent responsible for routing tasks to specialized agents in a multi-agent automation framework.

    Your job is to decide the next best stage to execute based on the user’s request and the accumulated conversation context.

    Goals:
    Correctly determine which specialized agent should handle the next step.

    Handle multi-task or multi-artifact requests step by step, without repeating any stage unnecessarily.

    Use the conversation context to detect if a stage has already been executed.

    Only mark as done when all explicitly requested outputs are complete.

    If the last agent reply looks like an error or exception (e.g., "KeyError", "Traceback", "Exception", "failed"), return error to pause until the user responds.

    Available Agents and Their Responsibilities:
    requirements: Handles BRD, PDD, requirements docs, user stories, MOM (Minutes of Meeting), saving, modifying these docs or goal/analysis gathering.

    design_architecture: Handles system design, architecture diagrams, technical/solution designs, and API specifications.

    development: Handles code generation, module implementation, or detailed coding tasks.

    testing: Handles test plans, test cases, validation tasks, and QA workflows.

    deployment: Handles release workflows, deployment steps, infrastructure documentation, and production rollout.

    monitoring_feedback: Handles post-deployment monitoring, log reviews, incident analysis, and collecting feedback.

    done: Use when all explicitly requested outputs are completed.

    Decision Rules:
    Always base next stage decisions primarily on the latest HumanMessage (user input).

    Treat AIMessage outputs (agent replies) only as context/status, not as new requests.

    Analyze the user query for explicit keywords, goals, or artifacts requested.

    Analyze the conversation context to see which outputs are already present.

    If the original user query contains multiple tasks, pick only the next immediate stage and rely on future iterations to handle the rest.

    If the user request involves multiple artifacts, process them in SDLC order: requirements → design_architecture → development → testing → deployment → monitoring_feedback (Skip any stage that is not needed or asked by the user or is already completed.)

    Never repeat a stage unless the user explicitly requests an update or revision.

    If the user repeats a request (even if it was already completed earlier), treat it as a new task and rerun the appropriate stage once.

    Prioritize user intent over strict SDLC order. Example: If the user wants only a test plan and enough context exists, jump directly to testing.

    Mark the process done only when all requested artifacts are complete.

    Inputs:
    User Query (latest HumanMessage): "{user_input}"

    Current Conversation Context: "{context}"

    Output Format:
    Return a JSON object listing all requested artifacts grouped by stage. For each artifact, write a simple sentence that reflects the user's intent, such as "The user wants to create a BRD".

    Only include stages and artifacts explicitly mentioned or clearly inferred from the user input.

    Each value in the list should be a plain string describing the user's intent for that artifact.

    Example Output:
    json 
    {example_output}

    NOTE : Make sure that you provide the output in above json struture only.And no other Extra text should be present. For example " Gemini JSON output: "something like this should
            not be present only provide the json.
    """ 
    decision = await llm.ainvoke(decision_prompt) 
    print("[DEBUG] Gemini JSON output:", decision.content)
    try:
        # Strip Markdown formatting
        cleaned = decision.content.strip().replace("```json", "").replace("```", "").strip()
        print(cleaned)
        # Parse JSON
        parsed_output = json.loads(cleaned)
        print(parsed_output)
        state["artifact_map"] = parsed_output
        state["stage"] = "decide"
    except Exception as e:
        print(f"⚠️ Failed to parse JSON: {e}")
        state["artifact_map"] = {}
        state["stage"] = "error"

    return state

async def call_requirements_agent(user_message: str, session_id: str = "orchestrator-session", user_id: str = "system", file_path: str = None) -> str:
    url = "http://localhost:8000/sdlc/agent/requirement/chat/"
    async with httpx.AsyncClient(timeout=180.0) as client:
        data = {
            "session_id": session_id,
            "user_message": user_message,
            "user_id": user_id,
        }
        files = None
        if file_path:
            file_to_upload = open(file_path, "rb")
            files = {"uploaded_files": (os.path.basename(file_path), file_to_upload, "application/octet-stream")}
        response = await client.post(url, data=data, files=files)
        if file_path:
            file_to_upload.close()
        response.raise_for_status()
        json_response = response.json()
        return json_response.get("responses", "No response from agent.")
    
async def call_design_agent(user_message: str, session_id: str = "orchestrator-session", user_id: str = "system", file_path: str = None) -> str:
    url = "http://localhost:8000/sdlc/agent/design/chat/"
    async with httpx.AsyncClient(timeout=1800.0) as client:
        data = {
            "session_id": session_id,
            "user_message": user_message,
            "user_id": user_id,
        }
        files = None
        if file_path:
            file_to_upload = open(file_path, "rb")
            files = {"uploaded_files": (os.path.basename(file_path), file_to_upload, "application/octet-stream")}
        response = await client.post(url, data=data, files=files)
        if file_path:
            file_to_upload.close()
        response.raise_for_status()
        json_response = response.json()
        return json_response.get("responses", "No response from design agent.")

async def call_development_agent(user_message: str, session_id: str = "orchestrator-session", user_id: str = "system", file_path: str = None) -> str:
    url = "http://localhost:8000/sdlc/agent/development/chat/"
    async with httpx.AsyncClient(timeout=180.0) as client:
        data = {
            "session_id": session_id,
            "user_message": user_message,
            "user_id": user_id,
        }
        files = None
        if file_path:
            file_to_upload = open(file_path, "rb")
            files = {"uploaded_files": (os.path.basename(file_path), file_to_upload, "application/octet-stream")}
        response = await client.post(url, data=data, files=files)
        if file_path:
            file_to_upload.close()
        response.raise_for_status()
        json_response = response.json()
        return json_response.get("responses", "No response from development agent.")

async def call_testing_agent(
    user_message: str,
    session_id: str = "orchestrator-session",
    user_id: str = "system",
    file_path: str = None
) -> str:
    url = "http://localhost:8000/sdlc/agent/testing/chat/"
    async with httpx.AsyncClient(timeout=180.0) as client:
        data = {
            "session_id": session_id,
            "user_message": user_message,
            "user_id": user_id,
        }
        files = None
        if file_path:
            file_to_upload = open(file_path, "rb")
            files = {"uploaded_files": (os.path.basename(file_path), file_to_upload, "application/octet-stream")}
        response = await client.post(url, data=data, files=files)
        if file_path:
            file_to_upload.close()
        response.raise_for_status()
        json_response = response.json()
        return json_response.get("responses", "No response from testing agent.")

async def call_deployment_agent(
    user_message: str,
    session_id: str = "orchestrator-session",
    user_id: str = "system",
    file_path: str = None
) -> str:
    url = "http://localhost:8000/sdlc/agent/deployment/chat/"
    async with httpx.AsyncClient(timeout=180.0) as client:
        data = {
            "session_id": session_id,
            "user_message": user_message,
            "user_id": user_id,
        }
        files = None
        if file_path:
            file_to_upload = open(file_path, "rb")
            files = {"uploaded_files": (os.path.basename(file_path), file_to_upload, "application/octet-stream")}
        response = await client.post(url, data=data, files=files)
        if file_path:
            file_to_upload.close()
        response.raise_for_status()
        json_response = response.json()
        return json_response.get("responses", "No response from deployment agent.")

async def call_monitoring_agent(
    user_message: str,
    session_id: str = "orchestrator-session",
    user_id: str = "system",
    file_path: str = None
) -> str:
    url = "http://localhost:8000/sdlc/agent/monitoring/chat/"
    async with httpx.AsyncClient(timeout=180.0) as client:
        data = {
            "session_id": session_id,
            "user_message": user_message,
            "user_id": user_id,
        }
        files = None
        if file_path:
            file_to_upload = open(file_path, "rb")
            files = {"uploaded_files": (os.path.basename(file_path), file_to_upload, "application/octet-stream")}
        response = await client.post(url, data=data, files=files)
        if file_path:
            file_to_upload.close()
        response.raise_for_status()
        json_response = response.json()
        return json_response.get("responses", "No response from monitoring agent.") 
    

from langchain_core.messages import AIMessage

from langchain_core.messages import AIMessage, HumanMessage

async def orchestrate_agents(state: OrchestratorState) -> OrchestratorState:
    artifact_map = state.get("artifact_map", {})
    user_message = state["user_input"]
    session_id = state.get("session_id", "orchestrator-session")
    user_id = "system"
    demo_file_path = "./demo_input.txt"
    # Convert user input to LangChain message
    user_message_obj = HumanMessage(content=user_message)

    # Combine history messages with new user message for full context
    full_conversation = state.get("messages", []) + [user_message_obj]

    # Flatten messages to text
    combined_text = "\n\n".join(msg.content for msg in full_conversation if hasattr(msg, 'content'))

    combined_outputs = []

    # Helper to enrich input with first intent string from artifact_map
    def enrich_input(stage_key: str) -> str:
        artifact_intents = artifact_map.get(stage_key, [])
        first_intent = artifact_intents[0] if artifact_intents else ""
        return f"{combined_text}\n\n{first_intent}" if first_intent else combined_text

    # Call agents conditionally based on non-empty artifact list
    if artifact_map.get("requirements", []) != []:
        enriched_input = enrich_input("requirements")
        response = await call_requirements_agent(enriched_input, session_id, file_path=demo_file_path)
        combined_outputs.append("📝 Requirements Agent:\n" + response)

    if artifact_map.get("design_architecture", []) != []:
        enriched_input = enrich_input("design_architecture")
        response = await call_design_agent(enriched_input, session_id, file_path=demo_file_path)
        combined_outputs.append("📐 Design Agent:\n" + response)

    if artifact_map.get("development", []) != []:
        enriched_input = enrich_input("development")
        response = await call_development_agent(enriched_input, session_id, file_path=demo_file_path)
        combined_outputs.append("💻 Development Agent:\n" + response)

    if artifact_map.get("testing", []) != []:
        enriched_input = enrich_input("testing")
        response = await call_testing_agent(enriched_input, session_id, file_path=demo_file_path)
        combined_outputs.append("🧪 Testing Agent:\n" + response)

    if artifact_map.get("monitoring_feedback", []) != []:
        enriched_input = enrich_input("monitoring_feedback")
        response = await call_monitoring_agent(enriched_input, session_id, file_path=demo_file_path)
        combined_outputs.append("📊 Monitoring Agent:\n" + response)

    # Combine all responses
    final_output = "\n\n".join(combined_outputs)
    state["messages"].append(AIMessage(content=final_output))
    state["history"].append(final_output)
    state["stage"] = "done"

    return state



# Define the orchestrator graph
workflow = StateGraph(OrchestratorState)

# Add nodes
workflow = StateGraph(OrchestratorState)
workflow.add_node("decide", decide_stage)
workflow.add_node("orchestrate", orchestrate_agents)

workflow.set_entry_point("decide")
workflow.add_edge("decide", "orchestrate")
workflow.add_edge("orchestrate", END)

orchestrator = workflow.compile(checkpointer=MemorySaver()) 

async def run_orchestration(user_query: str, session_id: str = None) -> Dict[str, Any]:
    if session_id is None:
        session_id = str(uuid.uuid4())
 
    initial_state = {
        "user_input": user_query,
        "stage": "",
        "messages": [],
        "session_id": session_id,
        "history": [],
        # "completed_stages": set()
        "artifact_map":{}
        
    }
    # The fix:
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
 
 
 
# ---- FastAPI setup with router and cookie session management (simplified) ----
@orchestrator_router.post("/chat/")
async def orchestrator_chat(
    request: Request,
    response: Response,
    user_query: str = Form(...),
    session_id: str = Cookie(None),
):
    # Compose the session_id with server epoch for uniqueness per boot session
    if session_id is None:
        session_id = f"{SERVER_EPOCH}-{uuid.uuid4()}"
 
    response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=3600 * 24 * 7)
    result = await run_orchestration(user_query, session_id=session_id)
    return JSONResponse(content=result)
 
app = FastAPI()
app.include_router(orchestrator_router, prefix="/orchestrator")
 
# ---- Replace quick test block with uvicorn server run ----

 