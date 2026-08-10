import os
import sys
import json
import base64
import asyncio
import logging
import uuid
from typing import List, Dict, Optional, Tuple, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse

import aiofiles
import google.generativeai as genai
from dotenv import load_dotenv

# langgraph/langchain imports
from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
 
# Your internal imports (adjust paths as needed)
from config import sdlcSettings
from config.ws_helper import set_session_id, broadcast_log, set_user_id, get_user_id, get_session_id
from config.agent_context import set_agent_folder
from config.connection_manager import manager
from config.websocket_utils import set_websocket_context
from config.agent_context import set_agent_folder
from agents_orchestrator.deployment_agent.config import shared

# Pipeline functions imported from your pipeline app module
from agents_orchestrator.deployment_agent.agents.pipeline_app import (
    extract_codebase_zip, extract_testing_zip, read_codebase_files, read_testing_files,
    combine_content, generate_risk_analysis, generate_deployment_validation,
    generate_dockerfile, generate_readme, generate_docker_instructions,
    write_all_outputs, print_pipeline_summary
)

#############################
# Configuration & Setup
#############################

esett = sdlcSettings()
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("Missing GOOGLE_API_KEY / GEMINI_API_KEY in environment")
genai.configure(api_key=API_KEY)

GEMINI_MODEL = "gemini-2.5-flash"

logger = logging.getLogger("deployment_agent")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

deployment_router = APIRouter()

#############################
# Helper Functions
#############################

def required_files_for_task(task: str) -> List[str]:
    if task in ("risk_analysis", "deployment_validation", "full_pipeline", "create_deployment_files"):
        return ["codebase", "testing"]
    if task in ("dockerfile", "readme"):
        return ["codebase"]
    if task == "docker_instructions":
        return []
    return []

def has_required_files(pipeline_files: Dict[str, Optional[str]], needed: List[str]) -> Tuple[bool, List[str]]:
    missing = []
    if "codebase" in needed and not pipeline_files.get("codebase_zip"):
        missing.append("codebase")
    if "testing" in needed and not pipeline_files.get("testing_zip"):
        missing.append("testing")
    return (len(missing) == 0), missing

def safe_json_parse(txt: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(txt)
    except Exception:
        return fallback

async def llm_route(user_message: str, pipeline_files: Dict[str, Optional[str]], default_task="full_pipeline") -> Dict[str, Any]:
    """Enhanced routing logic to properly handle deployment file creation requests"""
    
    has_codebase = pipeline_files.get("codebase_zip") is not None
    has_testing = pipeline_files.get("testing_zip") is not None
    
    # Normalize user message for better matching
    message_lower = user_message.lower().strip()
    
    # Enhanced routing logic
    task = "full_pipeline"  # default
    required_files = ["codebase", "testing"]
    assistant_message = "Running full deployment pipeline to generate all deployment files."
    
    # Check for specific deployment file creation requests
    deployment_keywords = [
        "create deployment files", "generate deployment files", 
        "deployment files", "all deployment files", "full deployment"
    ]
    
    if any(keyword in message_lower for keyword in deployment_keywords):
        task = "full_pipeline"
        required_files = ["codebase", "testing"]
        assistant_message = "Creating all deployment files: risk analysis, deployment validation, Dockerfile, README, and Docker instructions."
    
    elif "dockerfile" in message_lower and "readme" not in message_lower:
        task = "dockerfile"
        required_files = ["codebase"]
        assistant_message = "Generating Dockerfile only."
        
    elif "readme" in message_lower and "dockerfile" not in message_lower:
        task = "readme"
        required_files = ["codebase"]
        assistant_message = "Generating README.md only."
        
    elif "deploy" in message_lower or "validation" in message_lower:
        task = "deployment_validation"
        required_files = ["codebase", "testing"]
        assistant_message = "Running deployment validation analysis."
        
    elif "risk" in message_lower and "analysis" in message_lower:
        task = "risk_analysis"
        required_files = ["codebase", "testing"]
        assistant_message = "Running risk analysis."
        
    elif "docker instructions" in message_lower:
        task = "docker_instructions"
        required_files = []
        assistant_message = "Generating Docker deployment instructions."
    
    # Check file availability and adjust message
    valid, missing = has_required_files(pipeline_files, required_files)
    if not valid:
        assistant_message = f"Cannot proceed - missing required files: {', '.join(missing)}. Please upload the missing files."

    return {
        "task": task,
        "required_files": required_files,
        "assistant_message": assistant_message,
    }

async def llm_summarize_results(state: Dict[str, Any]) -> str:
    """Generate a comprehensive summary of what was actually created"""
    parts = []
    files_created = []
    
    # Check what was actually generated
    if state.get("risk_report") and not state.get("risk_report", "").startswith(('⚠', '❌')):
        parts.append("✅ Risk analysis report generated")
        files_created.append("risk_analysis_report.md")
    
    if state.get("deploy_report") and not state.get("deploy_report", "").startswith(('⚠', '❌')):
        parts.append("✅ Deployment validation report generated")
        files_created.append("deployment_validation_report.md")
    
    if state.get("dockerfile_content") and not state.get("dockerfile_content", "").startswith(('⚠', '❌')):
        parts.append("✅ Dockerfile created")
        files_created.append("Dockerfile")
    
    if state.get("readme_content") and not state.get("readme_content", "").startswith(('⚠', '❌')):
        parts.append("✅ README.md generated")
        files_created.append("README.md")
    
    if state.get("docker_instructions") and not state.get("docker_instructions", "").startswith(('⚠', '❌')):
        parts.append("✅ Docker deployment instructions created")
        files_created.append("docker_build_instructions.md")
    
    errors = state.get("errors") or []
    
    summary = f"Deployment file generation completed!\n\n"
    if parts:
        summary += "\n".join(parts) + "\n\n"
        summary += f"Files created: {', '.join(files_created)}\n\n"
    
    if errors:
        summary += f"⚠️ Note: {len(errors)} warnings/errors occurred during processing:\n"
        for i, error in enumerate(errors[:3], 1):  # Show first 3 errors
            summary += f"  {i}. {error}\n"
        if len(errors) > 3:
            summary += f"  ...and {len(errors)-3} more (see pipeline_errors.log)\n"
    else:
        summary += "✅ No errors detected - all files generated successfully!"

    return summary

def build_step_plan(task: str) -> List[Tuple[str, Any]]:
    """Define comprehensive step plans for each task"""
    
    if task == "risk_analysis":
        return [
            ("Extracting codebase archive", extract_codebase_zip),
            ("Extracting testing reports", extract_testing_zip),
            ("Reading codebase files", read_codebase_files),
            ("Reading testing files", read_testing_files),
            ("Combining content for analysis", combine_content),
            ("Generating risk analysis report", generate_risk_analysis),
            ("Writing output files", write_all_outputs),
            ("Generating execution summary", print_pipeline_summary),
        ]
    elif task == "deployment_validation":
        return [
            ("Extracting codebase archive", extract_codebase_zip),
            ("Extracting testing reports", extract_testing_zip),
            ("Reading codebase files", read_codebase_files),
            ("Reading testing files", read_testing_files),
            ("Combining content for analysis", combine_content),
            ("Generating deployment validation report", generate_deployment_validation),
            ("Writing output files", write_all_outputs),
            ("Generating execution summary", print_pipeline_summary),
        ]
    elif task == "dockerfile":
        return [
            ("Extracting codebase archive", extract_codebase_zip),
            ("Reading codebase files", read_codebase_files),
            ("Combining content for analysis", combine_content),
            ("Generating Dockerfile", generate_dockerfile),
            ("Writing output files", write_all_outputs),
            ("Generating execution summary", print_pipeline_summary),
        ]
    elif task == "readme":
        return [
            ("Extracting codebase archive", extract_codebase_zip),
            ("Reading codebase files", read_codebase_files),
            ("Combining content for analysis", combine_content),
            ("Generating README.md", generate_readme),
            ("Writing output files", write_all_outputs),
            ("Generating execution summary", print_pipeline_summary),
        ]
    elif task == "docker_instructions":
        return [
            ("Combining available content", combine_content),
            ("Generating Docker deployment instructions", generate_docker_instructions),
            ("Writing output files", write_all_outputs),
            ("Generating execution summary", print_pipeline_summary),
        ]
    else:
        # Full pipeline - create ALL deployment files
        return [
            ("Extracting codebase archive", extract_codebase_zip),
            ("Extracting testing reports", extract_testing_zip),
            ("Reading codebase files", read_codebase_files),
            ("Reading testing files", read_testing_files),
            ("Combining content for analysis", combine_content),
            ("Generating risk analysis report", generate_risk_analysis),
            ("Generating deployment validation report", generate_deployment_validation),
            ("Generating Dockerfile", generate_dockerfile),
            ("Generating README.md", generate_readme),
            ("Generating Docker deployment instructions", generate_docker_instructions),
            ("Writing all output files", write_all_outputs),
            ("Generating execution summary", print_pipeline_summary),
        ]


# Define your WebSocket endpoints and REST endpoints now:

@deployment_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    set_agent_folder("deployment_agent")
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            session_id = message.get("session_id", str(uuid.uuid4()))
            user_id = message.get("user_id")
            set_session_id(session_id)
            set_user_id(user_id)
            set_websocket_context(manager, session_id)
            logger.info(f"Set context for session {session_id}")

            if message.get("type") == "user_message_with_files":
                await handle_user_message(message, websocket, user_id, session_id)
            elif message.get("type") == "clear_agents":
                await manager.clear_agents()
            elif message.get("type") == "session_cleanup":
                await handle_session_cleanup(message)
            else:
                await manager.send_personal_message(json.dumps({"type": "echo", "message": data}), websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Websocket error: {e}")
        manager.disconnect(websocket)

async def handle_user_message(message: Dict[str, Any], websocket: WebSocket, user_id: str, session_id: str):
    """Enhanced message handler with better error handling and state management"""
    try:
        # Save files from base64 parts to disk
        files = message.get("files", [])
        input_dir = os.path.join(esett.FILES, user_id, get_agent_folder(), session_id, "input")
        output_dir = os.path.join(esett.FILES, user_id, get_agent_folder(), session_id, "output")

        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        pipeline_files = {"codebase_zip": None, "testing_zip": None}

        # Process uploaded files
        for f in files:
            fname = f.get("name")
            content = f.get("content")
            if not fname or not content:
                continue
            try:
                binary = base64.b64decode(content)
                save_path = os.path.join(input_dir, f"{os.path.splitext(fname)[0]}_{session_id}{os.path.splitext(fname)[1]}")
                with open(save_path, "wb") as fd:
                    fd.write(binary)
                
                # Better file type detection
                fname_lower = fname.lower()
                if "codebase" in fname_lower or "source" in fname_lower or "src" in fname_lower:
                    pipeline_files["codebase_zip"] = save_path
                elif "test" in fname_lower or "report" in fname_lower:
                    pipeline_files["testing_zip"] = save_path
                    
                await manager.send_agent_response("Agent", f"✅ File saved: {fname}", session_id)
            except Exception as e:
                error_msg = f"❌ Error saving file {fname}: {e}"
                await manager.send_agent_response("Agent", error_msg, session_id)
                logger.error(error_msg)
                return

        user_text = message.get("text", "")
        routed = await llm_route(user_text, pipeline_files)
        task = routed["task"]
        needs = routed["required_files"]
        assistant_message = routed["assistant_message"]

        # Handle smalltalk/clarification
        if task in ["smalltalk", "clarify"]:
            await manager.send_agent_response("Agent", assistant_message, session_id)
            return

        # Check required files
        ok, missing = has_required_files(pipeline_files, needs)
        if not ok:
            await manager.send_agent_response("Agent",
                f"{assistant_message} Please upload missing files: {', '.join(missing)}", session_id)
            return

        # Initialize pipeline state
        pipeline_state = {
            "codebase_extracted": False,
            "testing_extracted": False,
            "codebase_content": "",
            "testing_content": "",
            "combined_content": "",
            "risk_report": "",
            "deploy_report": "",
            "dockerfile_content": "",
            "readme_content": "",
            "docker_instructions": "",
            "errors": [],
            "input_directory": input_dir,
            "output_directory": output_dir,
            "codebase_zip_path": pipeline_files["codebase_zip"],
            "testing_zip_path": pipeline_files["testing_zip"],
            "session_id": session_id,
        }

        # Send initial confirmation
        await manager.send_agent_response("Agent", assistant_message, session_id)

        steps = build_step_plan(task)
        try:
            for step_name, step_tool in steps:
                await manager.send_agent_response("Agent", f"🔄 {step_name}...", session_id)
                
                # FIXED: Proper tool invocation
                try:
                    result = await step_tool.ainvoke({"state": pipeline_state})
                    
                    # Update pipeline state with results
                    if isinstance(result, dict):
                        pipeline_state.update(result)
                    else:
                        logger.warning(f"Tool {step_tool.name} returned non-dict result: {type(result)}")
                        
                except Exception as tool_error:
                    error_msg = f"❌ Error in {step_name}: {tool_error}"
                    logger.error(error_msg)
                    pipeline_state.setdefault("errors", []).append(error_msg)
                    await manager.send_agent_response("Agent", error_msg, session_id)

            # Generate final summary
            summary = await llm_summarize_results(pipeline_state)
            await manager.send_agent_response("Agent", f"🎉 {summary}", session_id)
            
        except Exception as e:
            error_msg = f"❌ Pipeline execution error: {e}"
            logger.error(error_msg)
            await manager.send_agent_response("Agent", error_msg, session_id)

        # Send completion notification
        await manager.broadcast({
            "type": "activity_update",
            "activity": {
                "id": str(uuid.uuid4()),
                "type": "complete",
                "session_id": session_id,
                "message": "Deployment file generation completed",
                "time": "just now"
            }
        })

    except Exception as e:
        error_msg = f"❌ Critical error in message handling: {e}"
        logger.error(error_msg)
        await manager.send_agent_response("Agent", error_msg, session_id)

async def handle_session_cleanup(message):
    """Implement session cleanup logic"""
    try:
        session_id = message.get("session_id")
        if session_id:
            # Add cleanup logic here
            logger.info(f"Cleaning up session: {session_id}")
    except Exception as e:
        logger.error(f"Error in session cleanup: {e}")

@deployment_router.post("/chat/")
async def chat(
    session_id: str = Form(...),
    user_message: str = Form(...),
    user_id: str = Form(...),
    uploaded_files: List[UploadFile] = File(default_factory=list)
):
    """Enhanced REST endpoint with improved error handling"""
    try:
        set_session_id(session_id)
        set_user_id(user_id)
        set_websocket_context(manager, session_id)

        input_dir = os.path.join(esett.FILES, user_id, get_agent_folder(), session_id, "input")
        output_dir = os.path.join(esett.FILES, user_id, get_agent_folder(), session_id, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        pipeline_files = {"codebase_zip": None, "testing_zip": None}

        # Process uploaded files
        for uf in uploaded_files:
            safe_name = uf.filename
            save_path = os.path.join(input_dir, safe_name)
            async with aiofiles.open(save_path, "wb") as f:
                await f.write(await uf.read())
                
            # Better file type detection
            fname_lower = uf.filename.lower()
            if "codebase" in fname_lower or "source" in fname_lower or "src" in fname_lower:
                pipeline_files["codebase_zip"] = save_path
            elif "test" in fname_lower or "report" in fname_lower:
                pipeline_files["testing_zip"] = save_path

        routed = await llm_route(user_message, pipeline_files)
        task = routed["task"]
        needs = routed["required_files"]
        assistant_message = routed["assistant_message"]

        if task in ["smalltalk", "clarify"]:
            return {"conversation_id": session_id, "responses": assistant_message, "output_filename": ""}

        ok, missing = has_required_files(pipeline_files, needs)
        if not ok:
            return {"conversation_id": session_id, "responses": f"{assistant_message} Please upload missing files: {', '.join(missing)}", "output_filename": ""}

        # Initialize pipeline state
        pipeline_state = {
            "codebase_extracted": False,
            "testing_extracted": False,
            "codebase_content": "",
            "testing_content": "",
            "combined_content": "",
            "risk_report": "",
            "deploy_report": "",
            "dockerfile_content": "",
            "readme_content": "",
            "docker_instructions": "",
            "errors": [],
            "input_directory": input_dir,
            "output_directory": output_dir,
            "codebase_zip_path": pipeline_files["codebase_zip"],
            "testing_zip_path": pipeline_files["testing_zip"],
            "session_id": session_id,
        }

        steps = build_step_plan(task)
        try:
            for _, step_tool in steps:
                # FIXED: Proper tool invocation for REST endpoint
                result = await step_tool.ainvoke({"state": pipeline_state})
                if isinstance(result, dict):
                    pipeline_state.update(result)
                    
            summary = await llm_summarize_results(pipeline_state)
            
            # Create summary file
            output_file = os.path.join(output_dir, "deployment_summary.txt")
            async with aiofiles.open(output_file, "w", encoding='utf-8') as f:
                await f.write(summary)
                
            return {"conversation_id": session_id, "responses": summary, "output_filename": output_file}
            
        except Exception as e:
            error_msg = f"Pipeline execution failed: {e}"
            logger.error(error_msg)
            return {"conversation_id": session_id, "responses": error_msg, "output_filename": ""}

    except Exception as e:
        error_msg = f"Critical error in REST endpoint: {e}"
        logger.error(error_msg)
        return {"conversation_id": session_id, "responses": error_msg, "output_filename": ""}


@deployment_router.get("/download/{filename}")
async def download_file(filename: str, session_id: str, user_id: str):
    set_agent_folder("deployment_agent")
    base_path = os.path.abspath(os.path.join(esett.FILES, user_id, get_agent_folder(), session_id))
    candidates = [
        os.path.join(base_path, "output", filename),
        os.path.join(base_path, filename),
    ]
    for filepath in candidates:
        if os.path.isfile(filepath):
            # Security: file must be inside base_path
            if os.path.commonpath([base_path, filepath]) != base_path:
                raise HTTPException(status_code=403, detail="Unauthorized access")
            return FileResponse(filepath, filename=filename)
    raise HTTPException(status_code=404, detail="File not found")


@deployment_router.get("/sessions")
async def sessions_info():
    set_agent_folder("deployment_agent")
    return {
        "current_session": "deployment_agent",
        "supported_files": [
            "codebase (zip)",
            "testing reports (zip)", 
            "source code archives",
            "Any zip archive with source or test files"
        ],
        "generated_outputs": [
            "risk_analysis_report.md",
            "deployment_validation_report.md", 
            "Dockerfile",
            "README.md",
            "docker_build_instructions.md",
            "pipeline_errors.log (if errors occur)"
        ],
        "supported_commands": [
            "create deployment files",
            "generate all deployment files", 
            "dockerfile only",
            "readme only",
            "risk analysis",
            "deployment validation",
            "docker instructions"
        ]
    }


@deployment_router.get("/status/{session_id}")
async def status_check(session_id: str, user_id: str):
    set_agent_folder("deployment_agent")
    """Enhanced status check that verifies actual file existence"""
    base_path = os.path.join(esett.FILES, user_id, get_agent_folder(), session_id)
    output_dir = os.path.join(base_path, "output")
    codebase_dir = os.path.join(base_path, "temp", "codebase")

    # Check output files
    output_files = {
        "risk_analysis_report.md": os.path.join(output_dir, "risk_analysis_report.md"),
        "deployment_validation_report.md": os.path.join(output_dir, "deployment_validation_report.md"),
        "docker_build_instructions.md": os.path.join(output_dir, "docker_build_instructions.md"),
        "pipeline_errors.log": os.path.join(output_dir, "pipeline_errors.log"),
    }
    
    # Check codebase files  
    codebase_files = {
        "Dockerfile": os.path.join(codebase_dir, "Dockerfile"),
        "README.md": os.path.join(codebase_dir, "README.md"),
    }

    files = {}
    
    # Check output files
    for fname, fpath in output_files.items():
        exists = os.path.isfile(fpath)
        size = os.path.getsize(fpath) if exists else 0
        files[fname] = {
            "exists": exists,
            "location": "output",
            "size": size,
            "path": fpath
        }

    # Check codebase files
    for fname, fpath in codebase_files.items():
        exists = os.path.isfile(fpath)
        size = os.path.getsize(fpath) if exists else 0
        files[fname] = {
            "exists": exists,
            "location": "codebase", 
            "size": size,
            "path": fpath
        }

    # Calculate completion stats
    total_files = len(files)
    completed_files = sum(1 for f in files.values() if f["exists"])
    all_present = completed_files == total_files

    return {
        "session_id": session_id,
        "files": files,
        "analysis_complete": all_present,
        "total_files": total_files,
        "completed_files": completed_files,
        "completion_percentage": round((completed_files / total_files) * 100, 1) if total_files > 0 else 0
    }