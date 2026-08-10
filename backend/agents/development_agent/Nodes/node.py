import os
import shutil
import asyncio
import aiofiles
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, HumanMessage, AnyMessage
from langchain_google_genai import ChatGoogleGenerativeAI
import google.generativeai as genai
from pathlib import Path
from typing import List, Annotated
from langgraph.graph.message import add_messages
from config.ws_helper import set_session_id, broadcast_log, get_user_id, get_session_id
from typing import TypedDict, Literal, Optional
from agents.development_agent.prompts.code_generation_prompt import code_writer_system_prompt
from agents.development_agent.prompts.router_prompt import router_system_prompt
from agents.development_agent.prompts.general_prompt import general_system_prompt
from agents.development_agent.prompts.file_code_prompt import code_upload_system_prompt
from agents.development_agent.util import extract_zip_maintain_structure, upload_folder
from config.connection_manager import manager
from dotenv import load_dotenv
from config.websocket_utils import set_websocket_context
import sys, inspect

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
superparentdir = os.path.dirname(parentdir)
supersuperparentdir = os.path.dirname(superparentdir)
from config.agent_context import get_agent_folder, set_agent_folder
from config import sdlcSettings
esett = sdlcSettings()
load_dotenv()



genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    temperature=0,
    max_tokens=None,
    timeout=None,
    max_retries=1,
)

class CodeAgentState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    task_type: Optional[Literal["code_generation", "code_update", "file_agent"]]
    output: Optional[str]
    saved_files: Optional[str]

async def route_node(state: CodeAgentState):
    """Async router node to determine task type"""
    broadcast_log(manager, "Routing user request...", level="INFO")

    user_input = state["messages"]
    saved_files = state.get("saved_files", [])

    # Set a safe default so the conditional edge never hits a KeyError
    state["task_type"] = "general"

    if saved_files:
        state["task_type"] = "file_agent"
        broadcast_log(manager, "Detected file upload - routing to file agent", level="INFO")
    else:
        try:
            full_prompt = f"{router_system_prompt}\n\nUser input:\n{user_input}"
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: llm.invoke(full_prompt).content.strip().lower())
            state["task_type"] = "general" if "general" in result else "code_generation"
        except Exception as e:
            broadcast_log(manager, f"Router LLM failed, defaulting to general: {e}", level="WARNING")
            state["task_type"] = "general"
        broadcast_log(manager, f"Routed to: {state['task_type']}", level="INFO")

    return state

async def code_writer_node(state: CodeAgentState):
    """Async code writer node"""
    broadcast_log(manager, "Analyzing the user query for code generation.", level="INFO")
    
    user_input = state["messages"]
    full_prompt = f"{code_writer_system_prompt}\n\nUser requirement:\n{user_input}"
    
    # Use asyncio executor for LLM calls
    loop = asyncio.get_event_loop()
    
    result = await loop.run_in_executor(None, lambda: llm.invoke(full_prompt))
    
    state["messages"] = result
    broadcast_log(manager, "Code generation completed", level="INFO")
    
    return state

async def general_node(state: CodeAgentState):
    """Async general query node"""
    broadcast_log(manager, "Processing general query...", level="INFO")
    
    user_input = state["messages"]
    full_prompt = f"""{general_system_prompt}
   
    User query
    {user_input}
    """
    
    # Use asyncio executor for LLM calls
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: llm.invoke(full_prompt))
    
    state["output"] = result.content
    state["messages"] = result
    broadcast_log(manager, "General query processing completed", level="INFO")
    
    return state

async def code_file_agent(state: CodeAgentState):
    """Async code file processing agent"""
    broadcast_log(manager, "Processing uploaded code files...", level="INFO")
    session_id = get_session_id()
    user_id = get_user_id()
    
    fileuploaded = state["saved_files"]
    _, ext = os.path.splitext(fileuploaded)
    
    # Process file based on extension
    if ext == ".py":
        broadcast_log(manager, "Python file detected", level="INFO")
        original_file_path = fileuploaded
        target_dir = os.path.dirname(fileuploaded)
         
    elif ext == ".zip":
        broadcast_log(manager, "Unzipping the Zip file...", level="INFO")
        # Run file operations in executor to avoid blocking
        loop = asyncio.get_event_loop()
        saved_files, target_dir = await loop.run_in_executor(
            None, extract_zip_maintain_structure, fileuploaded, f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/unzipped"
        )
        original_zip_path = fileuploaded
        broadcast_log(manager, "File unzipped successfully", level="INFO")

    code_extensions = ['.py', '.md']
    
    # Upload the entire folder's contents
    broadcast_log(manager, "Uploading code files to Gemini...", level="INFO")
    loop = asyncio.get_event_loop()
    uploaded_code_files = await loop.run_in_executor(
        None, upload_folder, target_dir, code_extensions
    )
    
    broadcast_log(manager, "Analyzing the code...", level="INFO")
    model = genai.GenerativeModel(model_name='gemini-2.5-flash')
    
    user_input = state["messages"]
    full_prompt = f"""{code_upload_system_prompt}

    User query
    {user_input}
    """
    
    prompt = [full_prompt, *uploaded_code_files]
    
    # Generate content using executor
    response = await loop.run_in_executor(None, model.generate_content, prompt)
    
    state = {"messages": [AIMessage(content=response.text)]}
    broadcast_log(manager, "Code analysis completed", level="INFO")
    
    # Clean up uploaded files
    broadcast_log(manager, "Cleaning up uploaded files...", level="INFO")
    for uploaded_file in uploaded_code_files:
        try:
            await loop.run_in_executor(None, genai.delete_file, uploaded_file.name)
        except Exception as e:
            broadcast_log(manager, f"Failed to delete file {uploaded_file.display_name}. Error: {e}", level="WARNING")

    # Move files to processed directories
    broadcast_log(manager, "Moving files to processed directories...", level="INFO")
    try:
        # Create processed directories if they don't exist
        await loop.run_in_executor(None, os.makedirs,f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/processed/zips", True)
        await loop.run_in_executor(None, os.makedirs, f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/processed/projects", True)
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        original_file_path = Path(fileuploaded)
        
        if ext == ".zip":
            # Move original zip file to processed folder
            processed_zip_path =f"{timestamp}_{original_file_path.name}"
            await loop.run_in_executor(None, shutil.move, str(original_zip_path), str(processed_zip_path))
            
            # Move unzipped folder to processed projects
            project_name = original_file_path.stem
            processed_project_path =  f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/processed/projects/{timestamp}_{project_name}"
            await loop.run_in_executor(None, shutil.move, str(target_dir), str(processed_project_path))
            
        elif ext == ".py":
            # Move Python file to processed folder
            processed_py_path = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/processed/zips/{timestamp}_{original_file_path.name}"
            await loop.run_in_executor(None, shutil.move, str(original_file_path), str(processed_py_path))
            
        broadcast_log(manager, "Files moved to processed directories successfully", level="INFO")
        
    except Exception as e:
        broadcast_log(manager, f"Error moving files: {e}", level="ERROR")
    
    state["saved_files"] = None
    return state

# Sync wrapper functions for LangGraph compatibility
def route_node_sync(state: CodeAgentState):
    """Synchronous wrapper for route_node"""
    try:
        # Check if we're already in an async context
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(route_node(state))
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            return asyncio.run(route_node(state))
    except Exception as e:
        broadcast_log(manager, f"Error in route_node: {e}", level="ERROR")
        return state

def code_writer_node_sync(state: CodeAgentState):
    """Synchronous wrapper for code_writer_node"""
    try:
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(code_writer_node(state))
        except RuntimeError:
            return asyncio.run(code_writer_node(state))
    except Exception as e:
        broadcast_log(manager, f"Error in code_writer_node: {e}", level="ERROR")
        return state

def general_node_sync(state: CodeAgentState):
    """Synchronous wrapper for general_node"""
    try:
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(general_node(state))
        except RuntimeError:
            return asyncio.run(general_node(state))
    except Exception as e:
        broadcast_log(manager, f"Error in general_node: {e}", level="ERROR")
        return state

def code_file_agent_sync(state: CodeAgentState):
    """Synchronous wrapper for code_file_agent"""
    try:
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(code_file_agent(state))
        except RuntimeError:
            return asyncio.run(code_file_agent(state))
    except Exception as e:
        broadcast_log(manager, f"Error in code_file_agent: {e}", level="ERROR")
        return state

# Build the graph with sync wrappers
builder = StateGraph(CodeAgentState)
builder.add_node("router", route_node_sync)
builder.add_node("code_writer", code_writer_node_sync)
builder.add_node("general", general_node_sync)
builder.add_node("file_agent", code_file_agent_sync)

builder.add_conditional_edges("router", lambda state: state["task_type"], {
    "code_generation": "code_writer",
    "general": "general",
    "file_agent": "file_agent"
})

# End the graph after code update or generation
builder.set_entry_point("router")
builder.add_edge("code_writer", END)
builder.add_edge("general", END)
builder.add_edge("file_agent", END)

graph = builder.compile(checkpointer=MemorySaver())

# Async workflow execution functions (similar to your working code)
async def run_dev_workflow_async(messages, config=None):
    """Async wrapper that runs the development workflow"""
    if config is None:
        config = {"configurable": {"thread_id": "default"}}
    
    broadcast_log(manager, "Starting async development workflow execution...", level="INFO")
    
    result = None
    async for event in graph.astream({"messages": messages}, config):
        broadcast_log(manager, f"Workflow event: {list(event.keys())}", level="DEBUG")
        result = event
    
    broadcast_log(manager, "Async development workflow execution completed", level="INFO")
    return result

def run_dev_workflow_sync(messages, config=None):
    """Synchronous development workflow execution using async API"""
    if config is None:
        config = {"configurable": {"thread_id": "default"}}
    
    broadcast_log(manager, "Starting development workflow execution...", level="INFO")
    
    # Use asyncio.run to run the async workflow
    result = asyncio.run(run_dev_workflow_async(messages, config))
    
    broadcast_log(manager, "Development workflow execution completed", level="INFO")
    return result

# Usage examples:
async def main_dev_async():
    """Example async usage"""
    messages = [HumanMessage(content="Generate a Python script for data processing")]
    await run_dev_workflow_async(messages)

def main_dev_sync():
    """Example sync usage"""
    messages = [HumanMessage(content="Generate a Python script for data processing")]
    return run_dev_workflow_sync(messages)
