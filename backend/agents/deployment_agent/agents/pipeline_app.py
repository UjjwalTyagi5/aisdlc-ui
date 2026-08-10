import os
import sys
import inspect
import asyncio
import aiofiles
import aiohttp
import shutil
import zipfile
import pickle
import textwrap
import json
import logging
from typing_extensions import TypedDict,Optional, List
# from typing import Optional, List
from io import BytesIO

from dotenv import load_dotenv
import google.generativeai as genai

from langgraph.graph import StateGraph, END, START
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage, AIMessage
from langchain_core.tools import tool

from config.ws_helper import set_session_id, broadcast_log, get_user_id, get_session_id
from config.connection_manager import manager

# Environment & config setup
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
superparentdir = os.path.dirname(parentdir)
supersuperparentdir = os.path.dirname(superparentdir)
from config.env import AGENTIC_BASE_URL
from config.agent_context import get_agent_folder, set_agent_folder
from config import sdlcSettings
esett = sdlcSettings()

load_dotenv()
genai.configure(api_key=os.environ.get('GOOGLE_API_KEY', ''))
config = genai.types.GenerationConfig(temperature=.15)
model = genai.GenerativeModel(model_name="gemini-2.5-flash")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('pipeline.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class PipelineState(TypedDict):
    codebase_extracted: bool
    testing_extracted: bool
    codebase_content: str
    testing_content: str
    combined_content: str
    risk_report: str
    deploy_report: str
    dockerfile_content: str
    readme_content: str
    docker_instructions: str
    errors: List[str]
    input_directory: Optional[str]
    output_directory: Optional[str]
    codebase_zip_path: Optional[str]
    testing_zip_path: Optional[str]
    session_id: Optional[str]

class AgentState(TypedDict):
    messages: List[BaseMessage]

# --- Utility functions ---

async def broadcast_file_generated(session_id: str, filename: str, file_path: str):
    try:
        user_id = get_user_id()
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_url = f"{AGENTIC_BASE_URL}/generated/{user_id}/{get_agent_folder()}/{session_id}/output/{filename}"
        await manager.broadcast({
            "type": "file_generated",
            "session_id": session_id,
            "filename": filename,
            "url": file_url,
            "file_size": file_size,
            "message": f"Generated file: {filename}"
        })
        logger.debug(f"Broadcasted file generation: {filename} ({file_size} bytes)")
    except Exception as e:
        logger.error(f"Failed to broadcast file generation: {e}")

def safe_file_read(file_path: str, max_size_mb: int = 10) -> str:
    try:
        size = os.path.getsize(file_path)
        if size > max_size_mb * 1024 * 1024:
            logger.warning(f"File too large: {file_path} ({size} bytes)")
            return f"# File too large to process: {os.path.basename(file_path)} ({size/(1024*1024):.1f}MB)"
        encodings = ['utf-8', 'utf-16', 'latin-1', 'cp1252']
        for enc in encodings:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            logger.warning(f"Fallback encoding used for: {file_path}")
            return f.read()
    except PermissionError:
        logger.error(f"Permission denied: {file_path}")
        return f"# Permission denied: {os.path.basename(file_path)}"
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return f"# File not found: {os.path.basename(file_path)}"
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return f"# Error reading {os.path.basename(file_path)}: {e}"

def validate_zip_file(zip_path: str) -> bool:
    if not os.path.exists(zip_path):
        logger.error(f"Zip file missing: {zip_path}")
        return False
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            bad = zip_ref.testzip()
            if bad:
                logger.error(f"Corrupt zip entry: {bad} in {zip_path}")
                return False
        return True
    except zipfile.BadZipFile:
        logger.error(f"Invalid zip file: {zip_path}")
        return False
    except Exception as e:
        logger.error(f"Error validating zip {zip_path}: {e}")
        return False

async def unzip_file(zip_path: str, extract_to: str) -> bool:
    try:
        if os.path.exists(extract_to):
            shutil.rmtree(extract_to)
        os.makedirs(extract_to, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        exists_any = any(files for _, _, files in os.walk(extract_to))
        return exists_any
    except PermissionError as e:
        logger.error(f"Permission error unzipping {zip_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error unzipping {zip_path}: {e}")
        return False

def combine_code_and_testing(codebase: str, testing: str) -> str:
    combined = "# COMPREHENSIVE PROJECT ANALYSIS\n\n"
    if codebase.strip():
        combined += "## CODEBASE ANALYSIS\nThis section contains source code and config files.\n\n" + codebase + "\n\n"
    else:
        combined += "## CODEBASE ANALYSIS\n⚠️ No codebase content available\n\n"
    if testing.strip():
        combined += "## TESTING REPORTS ANALYSIS\nThis section contains test reports and QA data.\n\n" + testing
    else:
        combined += "## TESTING REPORTS ANALYSIS\n⚠️ No testing reports available\n\n"
    return combined

# === TOOLS ===

@tool
async def extract_codebase_zip(state: PipelineState) -> PipelineState:
    """
    Extract the codebase zip file and update the state with extraction status.
    """
    user_id = get_user_id()
    session_id = get_session_id()
    zip_file = state.get("codebase_zip_path") or f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/sample_codebase.zip"
    extract_dir = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/temp/codebase"
    broadcast_log(manager, f"Extracting codebase zip {zip_file}", level="INFO")
    logger.info(f"Extracting codebase zip {zip_file}")

    if not validate_zip_file(zip_file):
        state["codebase_extracted"] = False
        err = f"Invalid or missing codebase zip: {zip_file}"
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
        return state

    ok = await unzip_file(zip_file, extract_dir)
    if ok:
        state["codebase_extracted"] = True
        msg = f"Codebase extracted to {extract_dir}"
        logger.info(msg)
        broadcast_log(manager, msg, level="INFO")
    else:
        state["codebase_extracted"] = False
        err = f"Extraction failed or empty codebase zip: {zip_file}"
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
    return state

@tool
async def extract_testing_zip(state: PipelineState) -> PipelineState:
    """
    Extract the testing report zip file and update the state accordingly.
    """
    user_id = get_user_id()
    session_id = get_session_id()
    zip_file = state.get("testing_zip_path") or f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/input/sample_test_reports.zip"
    extract_dir = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/temp/testing"
    broadcast_log(manager, f"Extracting testing zip {zip_file}", level="INFO")
    logger.info(f"Extracting testing zip {zip_file}")

    if not validate_zip_file(zip_file):
        state["testing_extracted"] = False
        err = f"Invalid or missing testing zip: {zip_file}"
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
        return state

    ok = await unzip_file(zip_file, extract_dir)
    if ok:
        state["testing_extracted"] = True
        msg = f"Testing reports extracted to {extract_dir}"
        logger.info(msg)
        broadcast_log(manager, msg, level="INFO")
    else:
        state["testing_extracted"] = False
        err = f"Extraction failed or empty testing zip: {zip_file}"
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
    return state

@tool
async def read_codebase_files(state: PipelineState) -> PipelineState:
    """
    Read relevant source code and config files from the extracted codebase directory.
    """
    content_lines = []
    files_processed = 0
    user_id = get_user_id()
    session_id = get_session_id()

    if not state.get("codebase_extracted"):
        msg = "Codebase not extracted, skipping reading."
        logger.warning(msg)
        broadcast_log(manager, msg, level="WARNING")
        state["codebase_content"] = "# Codebase extraction failed - no content"
        return state

    dirpath = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/temp/codebase"
    broadcast_log(manager, "Reading codebase files", level="INFO")
    logger.info("Reading codebase files")

    code_exts = {
        '.py', '.js', '.ts', '.java', '.cpp', '.c', '.go',
        '.rs', '.rb', '.php', '.cs', '.kt'
    }
    config_keywords = ['requirements', 'package', 'makefile', 'dockerfile', 'config', 'env', 'yaml', 'yml', 'json']
    filenames_allow = {'readme', 'license', 'changelog', 'contributing'}

    for root, dirs, files in os.walk(dirpath):
        dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules', '.venv', 'venv'}]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            lowername = file.lower()
            should_process = ext in code_exts or any(kw in lowername for kw in config_keywords) or lowername in filenames_allow

            if should_process:
                try:
                    path = os.path.join(root, file)
                    content = safe_file_read(path)
                    if content:
                        relpath = os.path.relpath(path, dirpath)
                        content_lines.append(f"\n\n{'='*50}\n# Codebase File: {relpath}\n{'='*50}\n")
                        content_lines.append(content)
                        files_processed += 1
                except Exception as e:
                    err = f"Error reading file {file}: {e}"
                    logger.error(err)
                    state.setdefault("errors", []).append(err)

    if files_processed == 0:
        warn = "No relevant codebase files found"
        logger.warning(warn)
        broadcast_log(manager, warn, level="WARNING")
        state.setdefault("errors", []).append(warn)

    state["codebase_content"] = "\n".join(content_lines)
    logger.info(f"Codebase files read: {files_processed}")
    broadcast_log(manager, f"Codebase reading completed: {files_processed} files", level="INFO")
    return state

@tool
async def read_testing_files(state: PipelineState) -> PipelineState:
    """
    Read testing report files from the extracted testing directory and add their content to the state.
    """
    content_lines = []
    files_processed = 0
    user_id = get_user_id()
    session_id = get_session_id()

    if not state.get("testing_extracted"):
        msg = "Testing reports not extracted, skipping reading."
        logger.warning(msg)
        broadcast_log(manager, msg, level="WARNING")
        state["testing_content"] = "# Testing report extraction failed - no content"
        return state

    dirpath = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/temp/testing"
    broadcast_log(manager, "Reading testing report files", level="INFO")
    logger.info("Reading testing report files")

    report_exts = {'.txt', '.md', '.json', '.xml', '.html', '.log', '.csv'}
    binary_exts = {'.pdf', '.docx', '.xlsx'}

    for root, dirs, files in os.walk(dirpath):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            lowername = file.lower()
            should_process = (ext in report_exts) or ('test' in lowername) or ('report' in lowername)
            if not should_process:
                continue
            path = os.path.join(root, file)
            relpath = os.path.relpath(path, dirpath)
            if ext in binary_exts:
                content_lines.append(f"\n\n{'='*50}\n# Testing File: {relpath}\n{'='*50}\n")
                content_lines.append(f"# Binary file detected - manual review required: {file}")
                files_processed += 1
                continue
            try:
                content = safe_file_read(path)
                if content:
                    content_lines.append(f"\n\n{'='*50}\n# Testing File: {relpath}\n{'='*50}\n")
                    content_lines.append(content)
                    files_processed += 1
            except Exception as e:
                err = f"Error reading testing file {file}: {e}"
                logger.error(err)
                state.setdefault("errors", []).append(err)

    if files_processed == 0:
        warn = "No relevant testing report files found"
        logger.warning(warn)
        broadcast_log(manager, warn, level="WARNING")
        state.setdefault("errors", []).append(warn)

    state["testing_content"] = "\n".join(content_lines)
    logger.info(f"Testing files read: {files_processed}")
    broadcast_log(manager, f"Testing files reading completed: {files_processed} files", level="INFO")
    return state

@tool
async def combine_content(state: PipelineState) -> PipelineState:
    """
    Combine extracted codebase and testing contents into a single string for analysis.
    """
    logger.info("Combining codebase and testing content...")
    broadcast_log(manager, "Combining content for analysis", level="INFO")
    combined = combine_code_and_testing(state.get("codebase_content", ""), state.get("testing_content", ""))
    state["combined_content"] = combined
    return state

@tool
async def generate_risk_analysis(state: PipelineState) -> PipelineState:
    """
    Generate a comprehensive risk analysis report from combined codebase and testing content using AI.
    """
    prompt = ("You are a senior security analyst and DevOps expert. Analyze the provided codebase and testing reports to generate a comprehensive risk analysis report...")
    content = state.get("combined_content", "")
    if not content.strip():
        msg = "No content available for risk analysis"
        broadcast_log(manager, msg, level="WARNING")
        state["risk_report"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, model.generate_content, [prompt] + [content])
        state["risk_report"] = result.text if hasattr(result, "text") else str(result)
    except Exception as e:
        err = f"Error generating risk analysis: {e}"
        logger.error(err)
        state["risk_report"] = f"❌ {err}"
        state.setdefault("errors", []).append(err)
    return state

@tool
async def generate_deployment_validation(state: PipelineState) -> PipelineState:
    """
    Generate a deployment validation report from combined codebase and testing content using AI.
    """
    prompt = ("You are a deployment validation specialist and DevOps engineer. Based on the codebase and testing reports...")
    content = state.get("combined_content", "")
    if not content.strip():
        msg = "No content available for deployment validation"
        broadcast_log(manager, msg, level="WARNING")
        state["deploy_report"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, model.generate_content, [prompt] + [content])
        state["deploy_report"] = result.text if hasattr(result, "text") else str(result)
    except Exception as e:
        err = f"Error generating deployment validation: {e}"
        logger.error(err)
        state["deploy_report"] = f"❌ {err}"
        state.setdefault("errors", []).append(err)
    return state

@tool
async def generate_dockerfile(state: PipelineState) -> PipelineState:
    """
    Generate an optimized Dockerfile based on the analyzed codebase content.
    """
    prompt = ("You are a containerization expert and DevOps engineer. Based on the codebase analysis, create an optimized Dockerfile...")
    content = state.get("codebase_content", "")
    if not content.strip():
        msg = "No codebase content available for Dockerfile generation"
        broadcast_log(manager, msg, level="WARNING")
        state["dockerfile_content"] = f"# ⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, model.generate_content, [prompt] + [content])
        state["dockerfile_content"] = result.text if hasattr(result, "text") else str(result)
    except Exception as e:
        err = f"Error generating Dockerfile: {e}"
        logger.error(err)
        state["dockerfile_content"] = f"# ❌ {err}"
        state.setdefault("errors", []).append(err)
    return state

@tool
async def generate_readme(state: PipelineState) -> PipelineState:
    """
    Generate a comprehensive README.md based on the analyzed codebase content.
    """
    prompt = ("You are a technical documentation specialist. Based on the codebase analysis, create a comprehensive README.md...")
    content = state.get("codebase_content", "")
    if not content.strip():
        msg = "No codebase content available for README generation"
        broadcast_log(manager, msg, level="WARNING")
        state["readme_content"] = f"# ⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, model.generate_content, [prompt] + [content])
        state["readme_content"] = result.text if hasattr(result, "text") else str(result)
    except Exception as e:
        err = f"Error generating README: {e}"
        logger.error(err)
        state["readme_content"] = f"# ❌ {err}"
        state.setdefault("errors", []).append(err)
    return state

@tool
async def generate_docker_instructions(state: PipelineState) -> PipelineState:
    """
    Generate detailed Docker build and deployment instructions using codebase and Dockerfile content.
    """
    prompt = ("You are a senior DevOps engineer specializing in containerization. Create comprehensive Docker build and deployment instructions...")
    codebase = state.get("codebase_content", "")
    dockerfile_content = state.get("dockerfile_content", "")
    if not codebase.strip() or not dockerfile_content.strip():
        msg = "Insufficient content for Docker instructions generation"
        broadcast_log(manager, msg, level="WARNING")
        state["docker_instructions"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    shorthand_context = f"CODEBASE ANALYSIS:\n{codebase}\n\nDOCKERFILE CONTENT:\n{dockerfile_content}\n\nRISK ANALYSIS SUMMARY:\n{state.get('risk_report','')[:1000]}...\n\nPlease generate comprehensive Docker instructions."

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, model.generate_content, [prompt] + [shorthand_context])
        state["docker_instructions"] = result.text if hasattr(result, "text") else str(result)
    except Exception as e:
        err = f"Error generating Docker instructions: {e}"
        logger.error(err)
        state["docker_instructions"] = f"❌ {err}"
        state.setdefault("errors", []).append(err)
    return state

@tool
async def write_all_outputs(state: PipelineState) -> PipelineState:
    """
    Write all generated artifacts (reports, Dockerfile, README, etc.) to disk and broadcast file info.
    Only writes files that were successfully generated.
    """
    user_id = get_user_id()
    session_id = get_session_id()
    output_dir = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/output"
    temp_codebase_dir = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/output"

    files_written = []
    files_skipped = []

    try:
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(temp_codebase_dir, exist_ok=True)
        broadcast_log(manager, f"Output directory created: {output_dir}", level="INFO")

        # Risk analysis report - only write if successfully generated
        risk_content = state.get("risk_report", "")
        if risk_content and not risk_content.startswith(('⚠', '❌')):
            rf = os.path.join(output_dir, "risk_analysis_report.md")
            async with aiofiles.open(rf, "w", encoding="utf-8") as f:
                await f.write("# Risk Analysis Report\n\n*Generated by AI Pipeline Analysis*\n\n")
                await f.write(risk_content)
            files_written.append(rf)
            await broadcast_file_generated(session_id, os.path.basename(rf), rf)
        else:
            files_skipped.append("risk_analysis_report.md")

        # Deployment validation report - only write if successfully generated
        deploy_content = state.get("deploy_report", "")
        if deploy_content and not deploy_content.startswith(('⚠', '❌')):
            df = os.path.join(output_dir, "deployment_validation_report.md")
            async with aiofiles.open(df, "w", encoding="utf-8") as f:
                await f.write("# Deployment Validation Report\n\n*Generated by AI Pipeline Analysis*\n\n")
                await f.write(deploy_content)
            files_written.append(df)
            await broadcast_file_generated(session_id, os.path.basename(df), df)
        else:
            files_skipped.append("deployment_validation_report.md")

        # Docker instructions - only write if successfully generated
        docker_inst_content = state.get("docker_instructions", "")
        if docker_inst_content and not docker_inst_content.startswith(('⚠', '❌')):
            di = os.path.join(output_dir, "docker_build_instructions.md")
            async with aiofiles.open(di, "w", encoding="utf-8") as f:
                await f.write("# Docker Build and Deployment Instructions\n\n*Generated by AI Pipeline Analysis*\n\n")
                await f.write(docker_inst_content)
            files_written.append(di)
            await broadcast_file_generated(session_id, os.path.basename(di), di)
        else:
            files_skipped.append("docker_build_instructions.md")

        # Dockerfile - only write if successfully generated and codebase was extracted
        dockerfile_content = state.get("dockerfile_content", "")
        if dockerfile_content and not dockerfile_content.startswith(('⚠', '❌')) and state.get("codebase_extracted", False):
            dpf = os.path.join(temp_codebase_dir, "Dockerfile")
            async with aiofiles.open(dpf, "w", encoding="utf-8") as f:
                await f.write("# Generated Dockerfile\n# Created by AI Pipeline Analysis\n\n")
                await f.write(dockerfile_content)
            files_written.append(dpf)
            await broadcast_file_generated(session_id, os.path.basename(dpf), dpf)
        else:
            files_skipped.append("Dockerfile")
            if not state.get("codebase_extracted", False):
                msg = "Skipped Dockerfile writing - codebase not extracted"
                logger.warning(msg)
                broadcast_log(manager, msg, level="WARNING")

        # README - only write if successfully generated and codebase was extracted
        readme_content = state.get("readme_content", "")
        if readme_content and not readme_content.startswith(('⚠', '❌')) and state.get("codebase_extracted", False):
            rdp = os.path.join(temp_codebase_dir, "README.md")
            async with aiofiles.open(rdp, "w", encoding="utf-8") as f:
                await f.write(readme_content)
            files_written.append(rdp)
            await broadcast_file_generated(session_id, os.path.basename(rdp), rdp)
        else:
            files_skipped.append("README.md")
            if not state.get("codebase_extracted", False):
                msg = "Skipped README writing - codebase not extracted"
                logger.warning(msg)
                broadcast_log(manager, msg, level="WARNING")

        # Errors log - only write if there are actual errors
        errors = state.get("errors", [])
        if errors:
            ef = os.path.join(output_dir, "pipeline_errors.log")
            async with aiofiles.open(ef, "w", encoding="utf-8") as f:
                await f.write("# Pipeline Execution Errors\n\n")
                await f.write(f"Total errors: {len(errors)}\n\n")
                for i, e in enumerate(errors, 1):
                    await f.write(f"{i}. {e}\n")
            files_written.append(ef)
            await broadcast_file_generated(session_id, os.path.basename(ef), ef)

        # Log summary
        success_msg = f"Output writing complete: {len(files_written)} files written successfully."
        if files_skipped:
            success_msg += f" {len(files_skipped)} files skipped due to generation failures: {', '.join(files_skipped)}"
        
        logger.info(success_msg)
        broadcast_log(manager, success_msg, level="INFO")

    except Exception as e:
        err = f"Critical error during output writing: {e}"
        logger.error(err)
        broadcast_log(manager, err, level="ERROR")
        state.setdefault("errors", []).append(err)

    return state

@tool
async def print_pipeline_summary(state: PipelineState) -> PipelineState:
    """
    Print a summary of pipeline execution status showing only actually created files.
    """
    try:
        user_id = get_user_id()
        session_id = get_session_id()
        broadcast_log(manager, "Generating pipeline summary", level="INFO")

        print("\n" + "="*80)
        print("🚀 DEPLOYMENT PIPELINE EXECUTION SUMMARY")
        print("="*80)
        
        # Extraction Status
        print(f"\n📦 Extraction Status:")
        print(f"  Codebase: {'✅ Success' if state.get('codebase_extracted') else '❌ Failed'}")
        print(f"  Testing: {'✅ Success' if state.get('testing_extracted') else '❌ Failed'}")
        
        # Content Analysis
        codebase_len = len(state.get('codebase_content', ''))
        testing_len = len(state.get('testing_content', ''))
        print(f"\n📊 Content Analysis:")
        print(f"  Codebase content: {codebase_len:,} characters")
        print(f"  Testing content: {testing_len:,} characters")
        
        # Check what was actually generated (excluding error messages)
        generation_status = {}
        
        # Risk Analysis
        risk_content = state.get("risk_report", "")
        generation_status["Risk Analysis"] = {
            "generated": bool(risk_content and not risk_content.startswith(('⚠', '❌'))),
            "length": len(risk_content),
            "key": "risk_report"
        }
        
        # Deployment Validation  
        deploy_content = state.get("deploy_report", "")
        generation_status["Deployment Validation"] = {
            "generated": bool(deploy_content and not deploy_content.startswith(('⚠', '❌'))),
            "length": len(deploy_content),
            "key": "deploy_report"
        }
        
        # Dockerfile
        dockerfile_content = state.get("dockerfile_content", "")
        generation_status["Dockerfile"] = {
            "generated": bool(dockerfile_content and not dockerfile_content.startswith(('⚠', '❌'))),
            "length": len(dockerfile_content),
            "key": "dockerfile_content"
        }
        
        # README
        readme_content = state.get("readme_content", "")
        generation_status["README"] = {
            "generated": bool(readme_content and not readme_content.startswith(('⚠', '❌'))),
            "length": len(readme_content),
            "key": "readme_content"
        }
        
        # Docker Instructions
        docker_inst_content = state.get("docker_instructions", "")
        generation_status["Docker Instructions"] = {
            "generated": bool(docker_inst_content and not docker_inst_content.startswith(('⚠', '❌'))),
            "length": len(docker_inst_content),
            "key": "docker_instructions"
        }
        
        print(f"\n📋 Generated Reports:")
        successful_generations = 0
        total_generations = len(generation_status)
        
        for name, status in generation_status.items():
            if status["generated"]:
                print(f"  {name}: ✅ Generated ({status['length']:,} chars)")
                successful_generations += 1
            else:
                print(f"  {name}: ❌ Failed")

        # Check actual files on disk
        print(f"\n💾 Output Files:")
        output_dir = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/output"
        temp_codebase_dir = f"{esett.FILES}/{user_id}/{get_agent_folder()}/{session_id}/output"
        
        file_checks = [
            ("risk_analysis_report.md", os.path.join(output_dir, "risk_analysis_report.md")),
            ("deployment_validation_report.md", os.path.join(output_dir, "deployment_validation_report.md")),
            ("docker_build_instructions.md", os.path.join(output_dir, "docker_build_instructions.md")),
            ("Dockerfile", os.path.join(temp_codebase_dir, "Dockerfile")),
            ("README.md", os.path.join(temp_codebase_dir, "README.md")),
        ]
        
        files_created = 0
        total_possible_files = len(file_checks)
        
        for filename, filepath in file_checks:
            if os.path.exists(filepath):
                size = os.path.getsize(filepath)
                print(f"  {filename}: ✅ Created ({size:,} bytes)")
                files_created += 1
            else:
                print(f"  {filename}: ❌ Missing")

        # Error log
        errors = state.get("errors", [])
        error_log_path = os.path.join(output_dir, "pipeline_errors.log")
        if errors and os.path.exists(error_log_path):
            error_size = os.path.getsize(error_log_path)
            print(f"  pipeline_errors.log: ⚠️ Created ({error_size:,} bytes)")
            files_created += 1
            total_possible_files += 1

        # Summary Statistics
        print(f"\n📈 Summary Statistics:")
        print(f"  Content Generation: {successful_generations}/{total_generations} successful")
        print(f"  Files Created: {files_created}/{total_possible_files}")
        generation_percentage = (successful_generations / total_generations) * 100 if total_generations > 0 else 0
        file_percentage = (files_created / total_possible_files) * 100 if total_possible_files > 0 else 0
        print(f"  Generation Success Rate: {generation_percentage:.1f}%")
        print(f"  File Creation Rate: {file_percentage:.1f}%")

        # Errors Summary
        if errors:
            print(f"\n⚠️ Errors Encountered: {len(errors)} (Showing first 3):")
            for i, error in enumerate(errors[:3], 1):
                print(f"  {i}. {error}")
            if len(errors) > 3:
                print(f"  ...and {len(errors)-3} more (see pipeline_errors.log)")
            broadcast_log(manager, f"Pipeline completed with {len(errors)} errors", level="WARNING")
        else:
            print(f"\n✅ No errors encountered")
            broadcast_log(manager, "Pipeline completed successfully with no errors", level="INFO")

        print("\n" + "="*80)
        
        # Final Status Message
        if successful_generations == total_generations and files_created >= (total_possible_files - 1):  # -1 for error log
            print("🎉 Pipeline execution completed successfully!")
            print("✅ All deployment files generated and saved!")
            final_status = "success"
        elif successful_generations > 0 or files_created > 0:
            print("⚠️ Pipeline execution completed with partial success!")
            print("🔄 Some deployment files were generated. Review errors for issues.")
            final_status = "partial"
        else:
            print("❌ Pipeline execution failed!")
            print("🔍 No deployment files were generated. Check errors above.")
            final_status = "failed"
            
        print("="*80)
        
        # Broadcast final status
        broadcast_log(manager, f"Pipeline execution summary completed - Status: {final_status}", level="INFO")

    except Exception as e:
        error_msg = f"Error generating pipeline summary: {e}"
        logger.error(error_msg)
        broadcast_log(manager, error_msg, level="ERROR")
        print(f"❌ {error_msg}")

    return state

@tool
async def pipeline_agent(state: AgentState):
    """
    Main agent function to invoke the LLM model on the provided messages.
    """
    global model
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, model.generate_content, state['messages'])
        return {"messages": [response]}
    except Exception as e:
        logger.error(f"Agent exception: {e}")
        return {"messages": [AIMessage(content=f"error: {e}")]}

@tool
async def pipeline_tools(state: AgentState):
    """
    Execute all requested tools sequentially based on the last message tool calls.
    """
    last_message = state['messages'][-1]
    results = []
    for tc in getattr(last_message, "tool_calls", []):
        broadcast_log(manager, f"Invoking tool {tc['name']} args {tc['args']}", level="LOGS")
        tool_to_call = next(t for t in pipeline_tools_list if t.name == tc['name'])
        try:
            result = await tool_to_call.ainvoke(tc['args'])
        except Exception as e:
            logger.error(f"Error executing tool {tc['name']}: {e}")
            result = f"Error executing tool {tc['name']}: {e}"
        results.append(ToolMessage(content=result, tool_call_id=tc['id'], name=tc['name']))
        logger.info(f"Tool {tc['name']} completed")
    return {"messages": results}

def should_continue(state: AgentState) -> str:
    """
    Decide whether to continue running tools or end the workflow based on the presence of tool calls.
    """
    return "tools" if state["messages"][-1].tool_calls else False

pipeline_tools_list = [
    extract_codebase_zip, extract_testing_zip, read_codebase_files, read_testing_files,
    combine_content, generate_risk_analysis, generate_deployment_validation,
    generate_dockerfile, generate_readme, generate_docker_instructions,
    write_all_outputs, print_pipeline_summary
]

workflow = StateGraph(AgentState)
workflow.add_node("agent", pipeline_agent)
workflow.add_node("tools", pipeline_tools)
workflow.add_edge("tools", "agent")
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", False: END})

app = workflow.compile()

async def run_workflow_async(messages, config=None):
    if config is None:
        config = {"configurable": {"thread_id": "default"}}
    result = None
    async for event in app.astream({"messages": messages}, config):
        result = event
    return result

def run_workflow_sync(messages, config=None):
    if config is None:
        config = {"configurable": {"thread_id": "default"}}
    return asyncio.run(run_workflow_async(messages, config=config))