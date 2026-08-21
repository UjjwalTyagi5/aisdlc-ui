import os
import shutil
import asyncio
import aiofiles
from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage, HumanMessage, AnyMessage
from config.checkpoint import build_checkpointer
import google.generativeai as genai
from pathlib import Path
from typing import List, Annotated
from langgraph.graph.message import add_messages
from config.ws_helper import set_session_id, broadcast_log, get_user_id, get_session_id
from typing import TypedDict, Literal, Optional
from agents_orchestrator.development_agent.prompts.code_generation_prompt import code_writer_system_prompt
from agents_orchestrator.development_agent.prompts.router_prompt import router_system_prompt
from agents_orchestrator.development_agent.prompts.general_prompt import general_system_prompt
from agents_orchestrator.development_agent.prompts.file_code_prompt import code_upload_system_prompt
from agents_orchestrator.development_agent.util import extract_zip_maintain_structure, upload_folder
from config.connection_manager import manager
from dotenv import load_dotenv
from config.websocket_utils import set_websocket_context
import sys, inspect

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
superparentdir = os.path.dirname(parentdir)
supersuperparentdir = os.path.dirname(superparentdir)
from config import sdlcSettings
esett = sdlcSettings()
load_dotenv()

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


async def _resolve_llm(state) -> object:
    """Build a per-run ChatLiteLLM from the tenant's BYOK-resolved model. Fail-closed."""
    from shared.services.model_resolver import (
        resolve_model_for_run, NoModelConfiguredError, ModelNotEnabledError)
    resolved = await resolve_model_for_run(
        state.get("tenant_id", ""), state.get("model_id"), offering_id=state.get("offering_id"))
    # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
    from langchain_litellm import ChatLiteLLM
    return ChatLiteLLM(
        model=resolved.model,
        custom_llm_provider=resolved.litellm_provider,
        api_base=resolved.base_url,
        api_key=resolved.api_key,
        temperature=0,
        max_retries=1,
    )


class CodeAgentState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    task_type: Optional[Literal["code_generation", "code_update", "file_agent"]]
    output: Optional[str]
    saved_files: Optional[str]
    tenant_id: str
    model_id: Optional[str]
    offering_id: Optional[str]

async def route_node(state: CodeAgentState):
   """Async router node to determine task type"""
   broadcast_log(manager, "Routing user request...", level="INFO")
   user_input = state["messages"]
   saved_files = state.get("saved_files", "")  # ✅ FIXED: Default to empty string, not list
   print(f"DEBUG: saved_files = '{saved_files}'")
   print(f"DEBUG: type(saved_files) = {type(saved_files)}")
   if saved_files and saved_files != "":  # ✅ FIXED: Check for non-empty string
       _, ext = os.path.splitext(saved_files)
       print(f"DEBUG: File extension = '{ext}'")
       document_extensions = ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv']
       if ext.lower() in document_extensions:
           state["task_type"] = "document_to_code"
           print("DEBUG: ✅ ROUTING TO document_to_code")
           broadcast_log(manager, f"Document {ext} detected - routing to document-to-code agent", level="INFO")
       else:
           state["task_type"] = "file_agent"
           print("DEBUG: ✅ ROUTING TO file_agent")
           broadcast_log(manager, "Code file detected - routing to file agent", level="INFO")
   else:
       print("DEBUG: No files - routing to text processing")
       from shared.services.model_resolver import NoModelConfiguredError, ModelNotEnabledError
       try:
           _llm = await _resolve_llm(state)
       except (NoModelConfiguredError, ModelNotEnabledError):
           state["task_type"] = "general"
           state["messages"] = [AIMessage(content=(
               "No usable model is configured for your organization. "
               "An administrator must add and verify a model provider in Org Settings → Model Providers."))]
           return state
       full_prompt = f"{router_system_prompt}\n\nUser input:\n{user_input}"
       loop = asyncio.get_event_loop()
       result = await loop.run_in_executor(None, lambda: _llm.invoke(full_prompt).content.strip().lower())
       state["task_type"] = "general" if "general" in result else "code_generation"
       broadcast_log(manager, f"Text-only request routed to: {state['task_type']}", level="INFO")
   return state

async def code_writer_node(state: CodeAgentState):
    """Async code writer node"""
    broadcast_log(manager, "Analyzing the user query for code generation.", level="INFO")

    from shared.services.model_resolver import NoModelConfiguredError, ModelNotEnabledError
    try:
        _llm = await _resolve_llm(state)
    except (NoModelConfiguredError, ModelNotEnabledError):
        state["messages"] = [AIMessage(content=(
            "No usable model is configured for your organization. "
            "An administrator must add and verify a model provider in Org Settings → Model Providers."))]
        return state

    user_input = state["messages"]
    full_prompt = f"{code_writer_system_prompt}\n\nUser requirement:\n{user_input}"

    # Use asyncio executor for LLM calls
    loop = asyncio.get_event_loop()

    result = await loop.run_in_executor(None, lambda: _llm.invoke(full_prompt))

    state["messages"] = result
    broadcast_log(manager, "Code generation completed", level="INFO")

    return state

async def general_node(state: CodeAgentState):
    """Async general query node"""
    broadcast_log(manager, "Processing general query...", level="INFO")

    from shared.services.model_resolver import NoModelConfiguredError, ModelNotEnabledError
    try:
        _llm = await _resolve_llm(state)
    except (NoModelConfiguredError, ModelNotEnabledError):
        msg = AIMessage(content=(
            "No usable model is configured for your organization. "
            "An administrator must add and verify a model provider in Org Settings → Model Providers."))
        state["output"] = msg.content
        state["messages"] = [msg]
        return state

    user_input = state["messages"]
    full_prompt = f"""{general_system_prompt}

    User query
    {user_input}
    """

    # Use asyncio executor for LLM calls
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _llm.invoke(full_prompt))

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
            None, extract_zip_maintain_structure, fileuploaded, f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input/unzipped"
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
        await loop.run_in_executor(None, os.makedirs,f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input/processed/zips", True)
        await loop.run_in_executor(None, os.makedirs, f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input/processed/projects", True)
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        original_file_path = Path(fileuploaded)
        
        if ext == ".zip":
            # Move original zip file to processed folder
            processed_zip_path =f"{timestamp}_{original_file_path.name}"
            await loop.run_in_executor(None, shutil.move, str(original_zip_path), str(processed_zip_path))
            
            # Move unzipped folder to processed projects
            project_name = original_file_path.stem
            processed_project_path =  f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input/processed/projects/{timestamp}_{project_name}"
            await loop.run_in_executor(None, shutil.move, str(target_dir), str(processed_project_path))
            
        elif ext == ".py":
            # Move Python file to processed folder
            processed_py_path = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input/processed/zips/{timestamp}_{original_file_path.name}"
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

async def document_to_code_node(state: CodeAgentState):
    """Process documents with text extraction fallback"""
    broadcast_log(manager, "Processing document for code generation...", level="INFO")
    from shared.services.model_resolver import NoModelConfiguredError, ModelNotEnabledError
    try:
        _llm = await _resolve_llm(state)
    except (NoModelConfiguredError, ModelNotEnabledError):
        state["messages"] = [AIMessage(content=(
            "No usable model is configured for your organization. "
            "An administrator must add and verify a model provider in Org Settings → Model Providers."))]
        return state
    fileuploaded = state["saved_files"]
    user_input = state["messages"]
    if not fileuploaded or not os.path.exists(fileuploaded):
        state["messages"] = [AIMessage(content="File not found")]
        return state
    _, ext = os.path.splitext(fileuploaded)
    ext = ext.lower()
    loop = asyncio.get_event_loop()
    try:
        # PDF - Direct upload to Gemini (works)
        if ext == ".pdf":
            uploaded_doc = await loop.run_in_executor(
                None, 
                lambda: genai.upload_file(path=fileuploaded, mime_type="application/pdf")
            )
            model = genai.GenerativeModel(model_name='gemini-2.5-flash')
            prompt = f"Analyze this document and generate Python code. User request: {user_input}"
            response = await loop.run_in_executor(None, model.generate_content, [prompt, uploaded_doc])
            state["messages"] = [AIMessage(content=response.text)]
            await loop.run_in_executor(None, genai.delete_file, uploaded_doc.name)
        # DOCX - Extract text and send to LLM
        elif ext == ".docx":
            try:
                import docx2txt
                text_content = docx2txt.process(fileuploaded)
            except:
                # Fallback method
                from docx import Document
                doc = Document(fileuploaded)
                text_content = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            prompt = f"""Extracted text from DOCX document:
{text_content}
User Request: {user_input}
Generate complete Python code based on the requirements in this document."""
            response = await loop.run_in_executor(None, lambda: _llm.invoke(prompt))
            state["messages"] = [AIMessage(content=response.content)]
        # Excel - Convert to CSV and send to LLM  
        elif ext in [".xlsx", ".xls"]:
            import pandas as pd
            df = pd.read_excel(fileuploaded)
            csv_content = df.to_csv(index=False)
            prompt = f"""Extracted data from Excel file:
{csv_content}
User Request: {user_input}
Generate complete Python code based on this data and requirements."""
            response = await loop.run_in_executor(None, lambda: _llm.invoke(prompt))
            state["messages"] = [AIMessage(content=response.content)]
        # Other formats - Try direct upload
        else:
            mime_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg", 
                ".jpeg": "image/jpeg",
                ".svg": "image/svg+xml"
            }
            mime_type = mime_types.get(ext, "application/octet-stream")
            uploaded_doc = await loop.run_in_executor(
                None, 
                lambda: genai.upload_file(path=fileuploaded, mime_type=mime_type)
            )
            model = genai.GenerativeModel(model_name='gemini-2.5-flash')
            prompt = f"Analyze this file and generate Python code. User request: {user_input}"
            response = await loop.run_in_executor(None, model.generate_content, [prompt, uploaded_doc])
            state["messages"] = [AIMessage(content=response.text)]
            await loop.run_in_executor(None, genai.delete_file, uploaded_doc.name)
    except Exception as e:
        error_msg = f"Error processing {ext} file: {str(e)}"
        print(f"DEBUG DOC NODE: {error_msg}")
        state["messages"] = [AIMessage(content=error_msg)]
    return state  
 
def document_to_code_node_sync(state: CodeAgentState):
    """Sync wrapper for document_to_code_node"""
    try:
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(document_to_code_node(state))
        except RuntimeError:
            return asyncio.run(document_to_code_node(state))
    except Exception as e:
        broadcast_log(manager, f"Error in document_to_code_node: {e}", level="ERROR")
        return state

# Build the graph with sync wrappers
builder = StateGraph(CodeAgentState)
builder.add_node("router", route_node_sync)
builder.add_node("code_writer", code_writer_node_sync)
builder.add_node("general", general_node_sync)
builder.add_node("file_agent", code_file_agent_sync)
builder.add_node("document_to_code", document_to_code_node_sync) 

builder.add_conditional_edges("router", lambda state: state["task_type"], {
    "code_generation": "code_writer",
    "general": "general",
    "file_agent": "file_agent",
    "document_to_code": "document_to_code" 
})

# End the graph after code update or generation
builder.set_entry_point("router")
builder.add_edge("code_writer", END)
builder.add_edge("general", END)
builder.add_edge("file_agent", END)
builder.add_edge("document_to_code", END) 

graph = builder.compile(checkpointer=build_checkpointer("development_node"))

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
