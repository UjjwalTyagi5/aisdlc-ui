from config.env import AGENTIC_BASE_URL
import base64
import json
import os
import re
import subprocess
import sys
import inspect
import asyncio
import contextvars
import aiofiles
import shutil
import zipfile
import logging
from typing import Dict, List, Optional, Tuple
from typing_extensions import TypedDict

import httpx

from langgraph.graph import StateGraph, END, START
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage, AIMessage
from langchain_core.tools import tool

from config.ws_helper import broadcast_log, get_user_id, get_session_id
from config.connection_manager import manager
from shared.errors import classify_error

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
superparentdir = os.path.dirname(parentdir)
supersuperparentdir = os.path.dirname(superparentdir)
from config import sdlcSettings
esett = sdlcSettings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("pipeline.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# ── State ─────────────────────────────────────────────────────────────────────

class PipelineState(TypedDict, total=False):
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
    # Enriched context injected from prior agents (Requirements + Design + Development)
    session_context: str
    # Structured deployment request from user
    deployment_request: Optional[Dict]
    # New artifacts
    deployment_plan: str
    docker_compose: str
    cicd_pipeline_azure: str
    cicd_pipeline_github: str
    rollback_plan: str
    deployment_decision: str
    # Azure DevOps deploy mode
    ado_project: str
    ado_repo: str
    ado_branch: str
    ado_repo_url: str
    ado_clone_dir: str
    tech_stack: str
    pipeline_run_id: str
    pipeline_run_url: str
    push_succeeded: bool
    # CI provider selection. `deploy_via` is chosen upstream (deploy/prepare detects it
    # from the repo, or the user overrides it) and read at execution time by
    # trigger_deployment_pipeline. Absent means Azure Pipelines, which is what every
    # existing ADO run relies on.
    deploy_via: str  # "azure_pipelines" | "github_actions" | "argocd"
    pipeline_provider: str  # which provider actually ran the trigger
    gha_owner: str
    gha_repo: str
    pipeline_trigger_status: str  # "triggered" | "failed"
    # Phase 9.1 — file generation tracking
    created_files: List[str]
    existing_skipped: List[str]
    existing_pipeline_yaml: Optional[str]
    deployment_file_paths: List[str]
    deployment_manifest: str
    # Phase 9.2 — commit/push status
    commit_sha: Optional[str]
    push_status: str  # "no_changes" | "pushed" | "failed"
    # Phase 9.3 — testing-gate inputs + outcome
    orchestrator_driven: bool
    testing_artifacts: Optional[Dict]
    testing_gate: str  # "skipped_standalone" | "passed" | "warning_*"
    status: str  # "blocked" | "" (cleared = continue)
    block_reason: Optional[str]
    # P3.6 B6 — BYOK model resolution (tenant's own provider/key, no platform key)
    tenant_id: str
    model_id: Optional[str]


class AgentState(TypedDict):
    messages: List[BaseMessage]
    tenant_id: str


# ── Prompts ───────────────────────────────────────────────────────────────────

_RISK_ANALYSIS_SYSTEM = """\
You are a senior security analyst and DevOps expert specialising in deployment risk assessment.

Produce a thorough Deployment Risk Analysis Report from the provided codebase and test reports.

Structure your report with exactly these sections:

## Executive Summary
## Security Vulnerabilities
## Code Quality Risks
## Infrastructure & Configuration Risks
## Test Coverage Gaps
## Dependency & Supply Chain Risks
## Operational Risks
## Risk Matrix
(Table: Risk | Category | Severity | Likelihood | Mitigation Strategy)
## Deployment Recommendation
State clearly: APPROVED / CONDITIONAL APPROVAL / BLOCKED — with justification.
"""

_DEPLOYMENT_VALIDATION_SYSTEM = """\
You are a deployment validation specialist and senior DevOps engineer.

Produce a structured Deployment Validation Report from the codebase and test results.

Structure your report with exactly these sections:

## Pre-Deployment Checklist
## Environment Configuration Validation
## Database Migration Readiness
## API Contract Compliance
## Test Coverage Sign-off
## Security Hardening Status
## Performance & Scalability Assessment
## Rollback & Recovery Plan
## Final Validation Decision
State clearly: PASS / CONDITIONAL PASS / FAIL — with justification.
"""

_DOCKERFILE_SYSTEM = """\
You are a containerisation expert and senior DevOps engineer.

Generate a production-grade, optimised Dockerfile for the provided codebase.

Requirements:
- Use multi-stage builds where appropriate
- Minimise image size (prefer slim/alpine base images)
- Run as non-root user
- Follow security best practices (no secrets in layers, minimal attack surface)
- Include HEALTHCHECK instruction
- Add meaningful LABELs (version, maintainer, description)
- Output ONLY valid Dockerfile syntax — no markdown fences, no explanation outside comments
"""

_README_SYSTEM = """\
You are a senior technical writer and DevOps engineer.

Generate a comprehensive, developer-friendly README.md for the provided codebase.

Include:
## Project Overview
## Architecture
## Prerequisites
## Installation
## Configuration (environment variables, config files)
## Running Locally
## Running with Docker
## Running Tests
## Deployment
## API Reference (if applicable)
## Contributing
## License

Use clear markdown formatting. Be concise and actionable.
"""

_DOCKER_INSTRUCTIONS_SYSTEM = """\
You are a senior DevOps engineer specialising in containerisation.

Generate comprehensive Docker build and deployment instructions from the provided codebase and Dockerfile.

Include:
## Prerequisites
## Building the Image
## Running the Container (with all required env vars)
## Docker Compose Setup
## Environment Variables Reference
## Health Checks
## Logging & Monitoring
## Production Deployment Checklist
## Troubleshooting Common Issues

Be precise with commands — every code block must be copy-pasteable.
"""

_DEPLOYMENT_PLAN_SYSTEM = """\
You are a senior DevOps orchestrator responsible for planning enterprise deployments.

Given the project codebase, SDLC context, and deployment parameters, produce a complete Deployment Plan with actual shell commands.

Structure your report with exactly these sections:

## Deployment Overview
(Target environment, strategy, estimated duration)

## Pre-Deployment Checklist
(Numbered action items with commands — health checks, env var validation, DB backup if schema exists)

## Service Start Sequence
(Ordered steps with Docker/compose commands — each step must be a copy-pasteable command)

## Environment Variable Setup
(Table: Variable | Required | Default | Description)

## Database Migration Steps
(If a database schema exists: migration commands in order. If none: state "No migrations required.")

## Health Check Verification
(Commands to confirm each service is healthy post-deployment)

## Expected Startup Time
(Estimated time to fully healthy state for each service)

## Rollback Trigger Conditions
(Specific observable signals that should trigger rollback — e.g. health check fails after 3 retries)

Be precise. Every code block must be copy-pasteable.
"""

_DOCKER_COMPOSE_SYSTEM = """\
You are a containerisation expert specialising in Docker Compose for production environments.

Generate a production-grade docker-compose.yml for the provided codebase.

Requirements:
- Detect services from the tech stack and database schema (FastAPI → uvicorn app; PostgreSQL in schema → postgres service; Redis in ADRs → redis service)
- Use named volumes for persistence (not bind mounts)
- Include health checks for every service (test, interval, timeout, retries)
- Use depends_on with service_healthy conditions
- Add a dedicated bridge network
- Environment variables via ${VAR_NAME} references (never hardcode secrets)
- Non-root user in app service
- Restart policy: unless-stopped

Output ONLY valid docker-compose.yml syntax — no markdown fences, no explanation outside YAML comments.
Start with: version: '3.9'
"""

_CICD_PIPELINE_SYSTEM = """\
You are a CI/CD pipeline architect with expertise in both Azure DevOps and GitHub Actions.

Generate BOTH an Azure Pipelines YAML and a GitHub Actions workflow YAML for the provided codebase.

Requirements for both pipelines:
- Trigger on push to the branch identified in the development artifacts (default: main)
- Stages in order: install → lint → test → docker-build → docker-push → deploy
- Cache dependencies for faster runs
- Use secrets for credentials (never hardcode)
- Fail fast: stop on first failure

Format your response as:
---AZURE_PIPELINES---
(complete azure-pipelines.yml here)
---GITHUB_ACTIONS---
(complete .github/workflows/deploy.yml here)

Output only the YAML files separated by those exact delimiters. No other text.
"""

_ROLLBACK_PLAN_SYSTEM = """\
You are a senior site reliability engineer specialising in deployment rollback procedures.

Given the deployment strategy and codebase, generate a precise step-by-step rollback plan.

Structure your report with exactly these sections:

## Rollback Overview
(Strategy, estimated rollback time)

## Rollback Triggers
(Exact conditions that should initiate rollback)

## Rollback Steps
(Numbered, with copy-pasteable commands for the specific strategy:
- rolling → scale down, redeploy previous image tag
- blue-green → switch traffic back to blue environment
- canary → reduce canary traffic weight to 0%, redirect all to stable
- recreate → stop current deployment, deploy previous version)

## Verification Steps
(Commands to confirm rollback was successful and system is healthy)

## Post-Rollback Actions
(Incident report, ticket update, communication steps)

## Prevention for Next Deploy
(What to fix before retrying)

Every command must be copy-pasteable. Use placeholder values (e.g. IMAGE_TAG_PREVIOUS) where values differ per deployment.
"""


# ── LLM helper ────────────────────────────────────────────────────────────────

def _generate_with_claude(system_prompt: str, user_content: str, max_tokens: int = 8096) -> str:
    """Synchronous LLM generation via LiteLLM against the run's BYOK-resolved model.

    P3.6 (B6): no platform LLM key. The model + provider + key are read from the
    run's tenant-resolved BYOK model in the model_resolver contextvar. Fails CLOSED
    (raises RuntimeError) when nothing is resolved — there is NO platform fallback.

    Called from async nodes via ``loop.run_in_executor`` under a copied context
    (see ``_run_generation``), so the contextvar set by ``_ensure_resolved_model``
    is visible inside this executor thread.
    """
    from shared.services.model_resolver import get_resolved_model

    resolved = get_resolved_model()
    if resolved is None:
        raise RuntimeError(
            "No BYOK model resolved for this deployment run. An administrator must "
            "configure and verify a model provider in Org Settings -> Model Providers."
        )
    # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
    import litellm
    response = litellm.completion(
        model=resolved.model,
        custom_llm_provider=resolved.litellm_provider,
        api_key=resolved.api_key,
        api_base=resolved.base_url,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


async def _ensure_resolved_model(state: PipelineState) -> bool:
    """Resolve the tenant's BYOK model once per run and stash it on the contextvar.

    Idempotent: a no-op when a model is already resolved for this context. On
    resolver failure (no/disabled model) it sets ``status='blocked'`` + a
    user-facing ``block_reason`` and returns False so the caller short-circuits
    the pipeline (fail closed — never a platform key).
    """
    from shared.services.model_resolver import (
        resolve_model_for_run, set_resolved_model, get_resolved_model,
        NoModelConfiguredError, ModelNotEnabledError,
    )
    if get_resolved_model() is not None:
        return True
    try:
        resolved = await resolve_model_for_run(state.get("tenant_id", "") or "", state.get("model_id"))
        set_resolved_model(resolved)
        return True
    except (NoModelConfiguredError, ModelNotEnabledError) as exc:
        msg = (
            "No usable model is configured for your organization. An administrator "
            "must add and verify a model provider in Org Settings -> Model Providers."
        )
        logger.warning("Deployment model resolution failed (tenant=%s): %s",
                       state.get("tenant_id", ""), type(exc).__name__)
        state["status"] = "blocked"
        state["block_reason"] = msg
        state.setdefault("errors", []).append(msg)
        broadcast_log(manager, msg, level="ERROR")
        return False


async def _run_generation(state: PipelineState, system_prompt: str, user_content: str,
                          max_tokens: int = 8096) -> str:
    """Resolve the BYOK model (fail-closed) then run sync generation in an executor.

    The resolved model lives on a contextvar set on THIS async context; the
    executor thread is handed a COPY of the context so ``_generate_with_claude``
    sees the same resolved model. Raises RuntimeError when the run has no usable
    model (after marking the pipeline blocked), so the node's own try/except
    records it and the step loop short-circuits.
    """
    if not await _ensure_resolved_model(state):
        raise RuntimeError(state.get("block_reason") or "No BYOK model resolved for this deployment run.")
    loop = asyncio.get_event_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(
        None, lambda: ctx.run(_generate_with_claude, system_prompt, user_content, max_tokens)
    )


def _context_header(session_context: str) -> str:
    if not session_context:
        return ""
    return f"\n\n---\n## Prior SDLC Context (from Requirements, Design & Development agents)\n\n{session_context}\n---\n"


# ── File utilities ────────────────────────────────────────────────────────────

async def broadcast_file_generated(session_id: str, filename: str, file_path: str) -> None:
    try:
        user_id = get_user_id()
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_url = f"{AGENTIC_BASE_URL}/generated/{user_id}/orchestrator/{session_id}/output/{filename}"
        await manager.broadcast({
            "type": "file_generated",
            "session_id": session_id,
            "filename": filename,
            "url": file_url,
            "file_size": file_size,
            "message": f"Generated file: {filename}",
        })
    except Exception as exc:
        logger.error("Failed to broadcast file generation: %s", exc)


def safe_file_read(file_path: str, max_size_mb: int = 10) -> str:
    try:
        size = os.path.getsize(file_path)
        if size > max_size_mb * 1024 * 1024:
            return f"# File too large to process: {os.path.basename(file_path)} ({size/(1024*1024):.1f}MB)"
        for enc in ("utf-8", "utf-16", "latin-1", "cp1252"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except PermissionError:
        return f"# Permission denied: {os.path.basename(file_path)}"
    except FileNotFoundError:
        return f"# File not found: {os.path.basename(file_path)}"
    except Exception as exc:
        return f"# Error reading {os.path.basename(file_path)}: {exc}"


def validate_zip_file(zip_path: str) -> bool:
    if not os.path.exists(zip_path):
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            return bad is None
    except zipfile.BadZipFile:
        return False
    except Exception:
        return False


async def unzip_file(zip_path: str, extract_to: str) -> bool:
    try:
        if os.path.exists(extract_to):
            shutil.rmtree(extract_to)
        os.makedirs(extract_to, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_to)
        return any(files for _, _, files in os.walk(extract_to))
    except Exception as exc:
        logger.error("Error unzipping %s: %s", zip_path, exc)
        return False


def combine_code_and_testing(codebase: str, testing: str) -> str:
    combined = "# COMPREHENSIVE PROJECT ANALYSIS\n\n"
    combined += "## CODEBASE ANALYSIS\n" + (codebase if codebase.strip() else "⚠️ No codebase content available") + "\n\n"
    combined += "## TESTING REPORTS ANALYSIS\n" + (testing if testing.strip() else "⚠️ No testing reports available") + "\n\n"
    return combined


# ── Pipeline tools ────────────────────────────────────────────────────────────

@tool
async def extract_codebase_zip(state: PipelineState) -> PipelineState:
    """Extract the codebase zip file and update extraction status."""
    user_id = get_user_id()
    session_id = get_session_id()
    zip_file = state.get("codebase_zip_path") or f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input/sample_codebase.zip"
    extract_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/temp/codebase"
    broadcast_log(manager, f"Extracting codebase zip: {zip_file}", level="INFO")

    if not validate_zip_file(zip_file):
        err = f"Invalid or missing codebase zip: {zip_file}"
        state["codebase_extracted"] = False
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
        return state

    ok = await unzip_file(zip_file, extract_dir)
    if ok:
        state["codebase_extracted"] = True
        broadcast_log(manager, f"Codebase extracted to {extract_dir}", level="INFO")
    else:
        state["codebase_extracted"] = False
        err = f"Extraction failed or empty zip: {zip_file}"
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
    return state


@tool
async def extract_testing_zip(state: PipelineState) -> PipelineState:
    """Extract the testing report zip and update extraction status."""
    user_id = get_user_id()
    session_id = get_session_id()
    zip_file = state.get("testing_zip_path") or f"{esett.FILES}/{user_id}/orchestrator/{session_id}/input/sample_test_reports.zip"
    extract_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/temp/testing"
    broadcast_log(manager, f"Extracting testing zip: {zip_file}", level="INFO")

    if not validate_zip_file(zip_file):
        err = f"Invalid or missing testing zip: {zip_file}"
        state["testing_extracted"] = False
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
        return state

    ok = await unzip_file(zip_file, extract_dir)
    if ok:
        state["testing_extracted"] = True
        broadcast_log(manager, f"Testing reports extracted to {extract_dir}", level="INFO")
    else:
        state["testing_extracted"] = False
        err = f"Extraction failed or empty testing zip: {zip_file}"
        state.setdefault("errors", []).append(err)
        broadcast_log(manager, err, level="ERROR")
    return state


@tool
async def read_codebase_files(state: PipelineState) -> PipelineState:
    """Read source code and config files from the extracted codebase directory."""
    user_id = get_user_id()
    session_id = get_session_id()

    if not state.get("codebase_extracted"):
        broadcast_log(manager, "Codebase not extracted — skipping read.", level="WARNING")
        state["codebase_content"] = "# Codebase extraction failed — no content"
        return state

    dirpath = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/temp/codebase"
    broadcast_log(manager, "Reading codebase files", level="INFO")

    code_exts = {".py", ".js", ".ts", ".java", ".cpp", ".c", ".go", ".rs", ".rb", ".php", ".cs", ".kt"}
    config_keywords = {"requirements", "package", "makefile", "dockerfile", "config", "env", "yaml", "yml", "json"}

    content_lines: List[str] = []
    files_processed = 0

    for root, dirs, files in os.walk(dirpath):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv", "venv"}]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            lowername = file.lower()
            if ext in code_exts or any(kw in lowername for kw in config_keywords):
                path = os.path.join(root, file)
                content = safe_file_read(path)
                if content:
                    relpath = os.path.relpath(path, dirpath)
                    content_lines.append(f"\n\n{'='*50}\n# File: {relpath}\n{'='*50}\n{content}")
                    files_processed += 1

    if files_processed == 0:
        warn = "No relevant codebase files found"
        broadcast_log(manager, warn, level="WARNING")
        state.setdefault("errors", []).append(warn)

    state["codebase_content"] = "\n".join(content_lines)
    broadcast_log(manager, f"Codebase reading complete: {files_processed} files", level="INFO")
    return state


@tool
async def read_testing_files(state: PipelineState) -> PipelineState:
    """Read testing report files from the extracted testing directory."""
    user_id = get_user_id()
    session_id = get_session_id()

    if not state.get("testing_extracted"):
        broadcast_log(manager, "Testing reports not extracted — skipping read.", level="WARNING")
        state["testing_content"] = "# Testing report extraction failed — no content"
        return state

    dirpath = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/temp/testing"
    broadcast_log(manager, "Reading testing report files", level="INFO")

    report_exts = {".txt", ".md", ".json", ".xml", ".html", ".log", ".csv"}
    binary_exts = {".pdf", ".docx", ".xlsx"}

    content_lines: List[str] = []
    files_processed = 0

    for root, dirs, files in os.walk(dirpath):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            path = os.path.join(root, file)
            relpath = os.path.relpath(path, dirpath)
            if ext in binary_exts or "test" in file.lower() or "report" in file.lower():
                content_lines.append(f"\n\n{'='*50}\n# Testing File: {relpath}\n{'='*50}")
                if ext in binary_exts:
                    content_lines.append(f"# Binary file — manual review required: {file}")
                    files_processed += 1
                    continue
            if ext not in report_exts:
                continue
            content = safe_file_read(path)
            if content:
                content_lines.append(content)
                files_processed += 1

    if files_processed == 0:
        warn = "No relevant testing report files found"
        broadcast_log(manager, warn, level="WARNING")
        state.setdefault("errors", []).append(warn)

    state["testing_content"] = "\n".join(content_lines)
    broadcast_log(manager, f"Testing files reading complete: {files_processed} files", level="INFO")
    return state


@tool
async def combine_content(state: PipelineState) -> PipelineState:
    """Combine codebase and testing content into a single string for analysis."""
    broadcast_log(manager, "Combining content for analysis", level="INFO")
    state["combined_content"] = combine_code_and_testing(
        state.get("codebase_content", ""), state.get("testing_content", "")
    )
    return state


@tool
async def generate_risk_analysis(state: PipelineState) -> PipelineState:
    """Generate a comprehensive risk analysis report using Claude."""
    content = state.get("combined_content", "")
    if not content.strip():
        msg = "No content available for risk analysis"
        broadcast_log(manager, msg, level="WARNING")
        state["risk_report"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating risk analysis report with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    user_content = f"{ctx}\n\nAnalyse the following project content:\n\n{content}"

    try:
        state["risk_report"] = await _run_generation(state, _RISK_ANALYSIS_SYSTEM, user_content)
        broadcast_log(manager, "Risk analysis complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "risk analysis generation")
        logger.error("Risk analysis error: %s", exc)
        state["risk_report"] = f"❌ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def generate_deployment_validation(state: PipelineState) -> PipelineState:
    """Generate a deployment validation report using Claude."""
    content = state.get("combined_content", "")
    if not content.strip():
        msg = "No content available for deployment validation"
        broadcast_log(manager, msg, level="WARNING")
        state["deploy_report"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating deployment validation report with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    user_content = f"{ctx}\n\nValidate deployment readiness for the following project:\n\n{content}"

    try:
        state["deploy_report"] = await _run_generation(state, _DEPLOYMENT_VALIDATION_SYSTEM, user_content)
        broadcast_log(manager, "Deployment validation complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "deployment validation generation")
        logger.error("Deployment validation error: %s", exc)
        state["deploy_report"] = f"❌ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def generate_dockerfile(state: PipelineState) -> PipelineState:
    """Generate an optimised Dockerfile from the codebase using Claude."""
    content = state.get("codebase_content", "")
    if not content.strip():
        msg = "No codebase content available for Dockerfile generation"
        broadcast_log(manager, msg, level="WARNING")
        state["dockerfile_content"] = f"# ⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating Dockerfile with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    user_content = f"{ctx}\n\nGenerate a Dockerfile for the following codebase:\n\n{content}"

    try:
        state["dockerfile_content"] = await _run_generation(state, _DOCKERFILE_SYSTEM, user_content)
        broadcast_log(manager, "Dockerfile generation complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "Dockerfile generation")
        logger.error("Dockerfile error: %s", exc)
        state["dockerfile_content"] = f"# ❌ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def generate_readme(state: PipelineState) -> PipelineState:
    """Generate a comprehensive README.md from the codebase using Claude."""
    content = state.get("codebase_content", "")
    if not content.strip():
        msg = "No codebase content available for README generation"
        broadcast_log(manager, msg, level="WARNING")
        state["readme_content"] = f"# ⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating README.md with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    user_content = f"{ctx}\n\nGenerate a README.md for the following codebase:\n\n{content}"

    try:
        state["readme_content"] = await _run_generation(state, _README_SYSTEM, user_content)
        broadcast_log(manager, "README generation complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "README generation")
        logger.error("README error: %s", exc)
        state["readme_content"] = f"# ❌ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def generate_docker_instructions(state: PipelineState) -> PipelineState:
    """Generate Docker build and deployment instructions using Claude."""
    codebase = state.get("codebase_content", "")
    dockerfile = state.get("dockerfile_content", "")

    if not codebase.strip():
        msg = "No codebase content available for Docker instructions"
        broadcast_log(manager, msg, level="WARNING")
        state["docker_instructions"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating Docker instructions with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    risk_summary = state.get("risk_report", "")[:1000]
    user_content = (
        f"{ctx}\n\n"
        f"## Codebase\n{codebase}\n\n"
        f"## Dockerfile\n{dockerfile or '(not yet generated)'}\n\n"
        f"## Risk Analysis Summary\n{risk_summary}"
    )

    try:
        state["docker_instructions"] = await _run_generation(state, _DOCKER_INSTRUCTIONS_SYSTEM, user_content)
        broadcast_log(manager, "Docker instructions complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "Docker instructions generation")
        logger.error("Docker instructions error: %s", exc)
        state["docker_instructions"] = f"❌ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def generate_deployment_plan(state: PipelineState) -> PipelineState:
    """Generate a step-by-step deployment execution plan using Claude (runs first to set plan context)."""
    content = state.get("combined_content", "")
    if not content.strip():
        msg = "No content available for deployment plan generation"
        broadcast_log(manager, msg, level="WARNING")
        state["deployment_plan"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating deployment plan with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    dep_req = state.get("deployment_request") or {}
    env = dep_req.get("target_environment", "staging")
    strategy = dep_req.get("deployment_strategy", "rolling")
    notes = dep_req.get("notes", "")
    req_header = f"\nDeployment Parameters:\n- Target Environment: {env}\n- Strategy: {strategy}\n"
    if notes:
        req_header += f"- Notes: {notes}\n"
    user_content = f"{ctx}{req_header}\n\nGenerate a deployment plan for the following project:\n\n{content}"

    try:
        state["deployment_plan"] = await _run_generation(state, _DEPLOYMENT_PLAN_SYSTEM, user_content)
        broadcast_log(manager, "Deployment plan generation complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "deployment plan generation")
        logger.error("Deployment plan error: %s", exc)
        state["deployment_plan"] = f"⚠️ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def generate_docker_compose(state: PipelineState) -> PipelineState:
    """Generate a production-ready docker-compose.yml using Claude."""
    codebase = state.get("codebase_content", "")
    if not codebase.strip():
        msg = "No codebase content available for Docker Compose generation"
        broadcast_log(manager, msg, level="WARNING")
        state["docker_compose"] = f"# ⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating docker-compose.yml with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    dep_req = state.get("deployment_request") or {}
    env_vars = dep_req.get("env_vars", {})
    env_vars_note = f"\nUser-supplied env vars: {json.dumps(env_vars)}" if env_vars else ""
    dockerfile = state.get("dockerfile_content", "")
    user_content = (
        f"{ctx}{env_vars_note}\n\n"
        f"## Codebase\n{codebase}\n\n"
        f"## Dockerfile (for reference)\n{dockerfile or '(not yet generated)'}"
    )

    try:
        state["docker_compose"] = await _run_generation(state, _DOCKER_COMPOSE_SYSTEM, user_content)
        broadcast_log(manager, "Docker Compose generation complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "Docker Compose generation")
        logger.error("Docker Compose error: %s", exc)
        state["docker_compose"] = f"# ⚠️ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def generate_cicd_pipeline(state: PipelineState) -> PipelineState:
    """Generate both Azure Pipelines YAML and GitHub Actions workflow using Claude."""
    codebase = state.get("codebase_content", "")
    if not codebase.strip():
        msg = "No codebase content available for CI/CD pipeline generation"
        broadcast_log(manager, msg, level="WARNING")
        state["cicd_pipeline_azure"] = f"# ⚠️ {msg}"
        state["cicd_pipeline_github"] = f"# ⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating CI/CD pipelines with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    dep_req = state.get("deployment_request") or {}
    strategy = dep_req.get("deployment_strategy", "rolling")
    user_content = (
        f"{ctx}\n"
        f"Deployment strategy: {strategy}\n\n"
        f"## Codebase\n{codebase}"
    )

    try:
        raw = await _run_generation(state, _CICD_PIPELINE_SYSTEM, user_content)
        if "---GITHUB_ACTIONS---" in raw:
            parts = raw.split("---GITHUB_ACTIONS---", 1)
            azure_part = parts[0].replace("---AZURE_PIPELINES---", "").strip()
            github_part = parts[1].strip()
        else:
            azure_part = raw
            github_part = "# GitHub Actions pipeline not generated — see azure-pipelines.yml"
        state["cicd_pipeline_azure"] = azure_part
        state["cicd_pipeline_github"] = github_part
        broadcast_log(manager, "CI/CD pipeline generation complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "CI/CD pipeline generation")
        logger.error("CI/CD pipeline error: %s", exc)
        state["cicd_pipeline_azure"] = f"# ⚠️ {err}"
        state["cicd_pipeline_github"] = f"# ⚠️ {err}"
        state.setdefault("errors", []).append(err)
    return state


@tool
async def extract_deployment_decision(state: PipelineState) -> PipelineState:
    """Extract deployment decision (APPROVED/CONDITIONAL/BLOCKED) from risk and validation reports via regex."""
    risk = state.get("risk_report", "")
    validation = state.get("deploy_report", "")
    combined = f"{risk}\n{validation}"

    if re.search(r'\bBLOCKED\b', combined, re.IGNORECASE):
        decision = "BLOCKED"
    elif re.search(r'\bCONDITIONAL\b', combined, re.IGNORECASE):
        decision = "CONDITIONAL"
    elif re.search(r'\bAPPROVED\b', combined, re.IGNORECASE):
        decision = "APPROVED"
    else:
        decision = "UNKNOWN"

    state["deployment_decision"] = decision
    broadcast_log(manager, f"Deployment decision: {decision}", level="INFO")
    return state


@tool
async def generate_rollback_plan(state: PipelineState) -> PipelineState:
    """Generate a strategy-specific rollback procedure using Claude."""
    content = state.get("combined_content", "")
    if not content.strip():
        msg = "No content available for rollback plan generation"
        broadcast_log(manager, msg, level="WARNING")
        state["rollback_plan"] = f"⚠️ {msg}"
        state.setdefault("errors", []).append(msg)
        return state

    broadcast_log(manager, "Generating rollback plan with Claude...", level="INFO")
    ctx = _context_header(state.get("session_context", ""))
    dep_req = state.get("deployment_request") or {}
    strategy = dep_req.get("deployment_strategy", "rolling")
    env = dep_req.get("target_environment", "staging")
    deploy_plan_summary = state.get("deployment_plan", "")[:1000]
    user_content = (
        f"{ctx}\n"
        f"Deployment strategy: {strategy}\n"
        f"Target environment: {env}\n\n"
        f"## Deployment Plan Summary\n{deploy_plan_summary}\n\n"
        f"## Project Content\n{content[:3000]}"
    )

    try:
        state["rollback_plan"] = await _run_generation(state, _ROLLBACK_PLAN_SYSTEM, user_content)
        broadcast_log(manager, "Rollback plan generation complete", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "rollback plan generation")
        logger.error("Rollback plan error: %s", exc)
        state["rollback_plan"] = f"⚠️ {err}"
        state.setdefault("errors", []).append(err)
    return state


# ── Azure DevOps deploy-mode helpers + tools ──────────────────────────────────

def _run_git(args: List[str], cwd: Optional[str] = None) -> Tuple[bool, str]:
    """Run a git command synchronously, return (ok, output)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout)
        return True, result.stdout
    except Exception as exc:
        return False, str(exc)


def _strip_md_fences(text: str) -> str:
    text = re.sub(r'^```\w*\n', '', text.strip())
    text = re.sub(r'\n```\s*$', '', text)
    return text.strip()


@tool
async def resolve_ado_target(state: PipelineState) -> PipelineState:
    """Determine ADO project, repo, and branch from session context or DeploymentRequest."""
    dep_req = state.get("deployment_request") or {}
    project = dep_req.get("ado_project") or ""
    repo = dep_req.get("ado_repo") or ""
    branch = dep_req.get("ado_branch") or dep_req.get("branch") or ""
    repo_url = dep_req.get("ado_repo_url") or dep_req.get("repo_url") or ""

    ctx = state.get("session_context", "")
    if not repo_url:
        m = re.search(r'REPO_URL[:\s]+(\S+)', ctx, re.IGNORECASE)
        if m:
            repo_url = m.group(1).strip().rstrip('",;')
    if not branch:
        m = re.search(r'BRANCH_NAME[:\s]+(\S+)', ctx, re.IGNORECASE)
        if m:
            branch = m.group(1).strip().rstrip('",;')

    if repo_url:
        m = re.search(r'dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/?#]+)', repo_url)
        if m:
            project = project or m.group(2)
            repo = repo or m.group(3)

    if not branch:
        branch = "main"

    if not repo_url and project and repo:
        from shared.services.ado_repos import resolve_auth
        _org_url, _ = await resolve_auth(state.get("tenant_id", ""))
        org = _org_url.rstrip("/").split("/")[-1]
        if org:
            repo_url = f"https://dev.azure.com/{org}/{project}/_git/{repo}"

    if not repo_url or not project or not repo:
        # Phase 9.6 — block with a clear ask instead of falling through to a
        # cryptic clone failure. The downstream short-circuit in the API
        # handlers (Phase 9.3 step loop) skips clone/push/trigger when
        # status=='blocked'; Phase 9.5 summary renders the reason verbatim.
        missing = []
        if not project:
            missing.append("ado_project")
        if not repo:
            missing.append("ado_repo")
        if not repo_url:
            missing.append("ado_repo_url")
        msg = (
            f"Could not resolve ADO target — missing: {', '.join(missing)}. "
            f"Please supply project, repo, and branch via the deployment request "
            f"(or upload the codebase for a local-only deployment)."
        )
        state["status"] = "blocked"
        state["block_reason"] = msg
        state.setdefault("errors", []).append(msg)
        broadcast_log(manager, msg, level="WARNING")
        return state

    state["ado_project"] = project
    state["ado_repo"] = repo
    state["ado_branch"] = branch
    state["ado_repo_url"] = repo_url
    broadcast_log(manager, f"ADO target → project={project} repo={repo} branch={branch}", level="INFO")
    return state


@tool
async def clone_azure_repo(state: PipelineState) -> PipelineState:
    """Clone the Azure Repos repository to a local directory using PAT auth."""
    user_id = get_user_id()
    session_id = get_session_id()
    repo_url = state.get("ado_repo_url", "")
    branch = state.get("ado_branch", "main")

    if not repo_url:
        state["status"] = "blocked"
        state["block_reason"] = "No ADO repo URL — cannot clone (run resolve_ado_target first)."
        state.setdefault("errors", []).append(state["block_reason"])
        return state

    from shared.services.ado_repos import resolve_auth
    _, _pat = await resolve_auth(state.get("tenant_id", ""))

    if not _pat:
        state["status"] = "blocked"
        state["block_reason"] = (
            "Azure DevOps is not connected for your organization. Open the "
            "Integrations page and connect Azure DevOps with your PAT."
        )
        state.setdefault("errors", []).append(state["block_reason"])
        return state

    # Phase 9.7 hotfix — strip any pre-existing `user@` from the URL before
    # injecting our PAT auth. Dev agent persists repo_url like
    # https://<org>@dev.azure.com/<org>/<project>/_git/<repo>; the naive
    # `replace("https://", "https://user:PAT@")` produces a double-@ URL
    # ("https://user:PAT@<org>@dev.azure.com/...") that ADO rejects with
    # 403, masquerading as a permissions error.
    from urllib.parse import quote
    bare_url = re.sub(r'^https://[^@/]+@', 'https://', repo_url)
    safe_pat = quote(_pat, safe="")
    auth_url = bare_url.replace("https://", f"https://user:{safe_pat}@", 1)
    clone_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/temp/ado_repo"
    # Phase 9.7 — defensive cleanup. Stale clone dir from a prior run can
    # confuse `git clone` and produce 'destination path already exists'.
    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)
    os.makedirs(os.path.dirname(clone_dir), exist_ok=True)

    broadcast_log(manager, f"Cloning {repo_url} (branch: {branch})...", level="INFO")
    loop = asyncio.get_event_loop()
    ok, output = await loop.run_in_executor(
        None, _run_git, ["clone", "--depth", "1", "--branch", branch, auth_url, clone_dir], None
    )
    if not ok:
        ok, output = await loop.run_in_executor(
            None, _run_git, ["clone", "--depth", "1", auth_url, clone_dir], None
        )
    if not ok:
        # Phase 9.7 — pattern-match common ADO failures for friendlier errors.
        # Scrub PAT from output before surfacing to user.
        scrubbed = output.replace(_pat, "***")
        low = scrubbed.lower()
        if "authentication failed" in low or "401" in low or "unauthorized" in low:
            friendly = (
                "ADO authentication failed (HTTP 401). Your PAT is missing, "
                "expired, or doesn't have Code Read permission on this repo."
            )
        elif "403" in low or "forbidden" in low:
            friendly = (
                "ADO denied access (HTTP 403). Your PAT exists but lacks Code "
                "Read permission on this project/repo."
            )
        elif "not found" in low or "404" in low or "could not find" in low:
            friendly = (
                f"ADO repo not found (HTTP 404). Verify project='{state.get('ado_project')}' "
                f"and repo='{state.get('ado_repo')}' exist in the configured org."
            )
        elif "remote branch" in low or f"branch {branch}" in low:
            friendly = (
                f"Branch '{branch}' not found in the repo. The fallback default-branch "
                f"clone also failed — see scrubbed output below."
            )
        else:
            friendly = f"git clone failed: {scrubbed[:400]}"
        state["status"] = "blocked"
        state["block_reason"] = friendly
        state.setdefault("errors", []).append(friendly)
        broadcast_log(manager, friendly, level="ERROR")
        return state

    state["ado_clone_dir"] = clone_dir
    state["codebase_extracted"] = True
    broadcast_log(manager, f"Cloned to {clone_dir}", level="INFO")
    return state


@tool
async def detect_tech_stack(state: PipelineState) -> PipelineState:
    """Detect tech stack and read codebase content from cloned ADO repo."""
    clone_dir = state.get("ado_clone_dir", "")
    if not clone_dir or not os.path.isdir(clone_dir):
        state.setdefault("errors", []).append("No cloned repo to analyze")
        return state

    files_root = set(os.listdir(clone_dir))
    detected = "generic"

    if "package.json" in files_root:
        detected = "node"
        try:
            with open(os.path.join(clone_dir, "package.json")) as f:
                pkg = f.read().lower()
            if "express" in pkg:
                detected = "node-express"
            elif "next" in pkg:
                detected = "node-next"
        except Exception:
            pass
    elif any(f in files_root for f in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile")):
        detected = "python"
        for f in ("requirements.txt", "pyproject.toml"):
            p = os.path.join(clone_dir, f)
            if os.path.exists(p):
                content = safe_file_read(p, 1).lower()
                if "fastapi" in content:
                    detected = "python-fastapi"
                    break
                if "flask" in content:
                    detected = "python-flask"
                    break
                if "django" in content:
                    detected = "python-django"
                    break
    elif "pom.xml" in files_root:
        pom = safe_file_read(os.path.join(clone_dir, "pom.xml"), 1).lower()
        detected = "java-spring" if "spring" in pom else "java"
    elif "build.gradle" in files_root or "build.gradle.kts" in files_root:
        detected = "java-gradle"
    elif "go.mod" in files_root:
        detected = "go"
    elif any(f.endswith(".csproj") for f in files_root) or _find_dotnet_project(clone_dir)[0]:
        detected = "dotnet"

    state["tech_stack"] = detected
    broadcast_log(manager, f"Detected tech stack: {detected}", level="INFO")

    code_exts = {".py", ".js", ".ts", ".java", ".go", ".rs", ".rb", ".php", ".cs", ".kt", ".tsx", ".jsx"}
    config_keywords = {"requirements", "package", "makefile", "dockerfile", "config", "env", "yaml", "yml", "json", "toml", "pom"}

    content_lines: List[str] = []
    files_processed = 0
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv", "venv", "target", "dist", "build", ".next"}]
        for file in files:
            if files_processed >= 30:
                break
            ext = os.path.splitext(file)[1].lower()
            lowername = file.lower()
            if ext in code_exts or any(kw in lowername for kw in config_keywords):
                path = os.path.join(root, file)
                content = safe_file_read(path)
                if content and not content.startswith("# File too large"):
                    relpath = os.path.relpath(path, clone_dir)
                    content_lines.append(f"\n\n{'='*50}\n# File: {relpath}\n{'='*50}\n{content}")
                    files_processed += 1
        if files_processed >= 30:
            break

    state["codebase_content"] = "\n".join(content_lines)
    broadcast_log(manager, f"Read {files_processed} files from cloned repo", level="INFO")
    return state


@tool
async def verify_testing_gate(state: PipelineState) -> PipelineState:
    """Phase 9.3 — block orchestrator-driven deployment when testing didn't pass.

    Pass conditions (continue with deployment):
      - testing_artifacts.status in {"executed", "pipeline_completed"}
        AND test_execution.failed == 0 AND test_execution.errors == 0
      - OR state["orchestrator_driven"] is False (standalone-ADO mode skips gate)

    Block conditions (set status=blocked, skip remaining steps):
      - testing_artifacts is None / missing
      - status in {"failed", "errored", "plan_only", "unknown"}
      - test_execution.failed > 0 OR test_execution.errors > 0

    Sets state["status"] = "blocked" + state["block_reason"] when blocking,
    so downstream summary (Phase 9.5) and HANDOFF gate (Phase 9.4) can see
    the decision. Cleared dict signals the rest of the chain to skip.
    """
    if not state.get("orchestrator_driven"):
        # Standalone-ADO mode — user explicitly asked to deploy from ADO
        # without going through the orchestrator. Skip the gate.
        state["testing_gate"] = "skipped_standalone"
        return state

    artifacts = state.get("testing_artifacts")
    if not artifacts:
        state["block_reason"] = (
            "No testing artifact found in this session. The Testing Agent has "
            "not run successfully yet."
        )
        state["testing_gate"] = "warning_no_artifact"
        broadcast_log(manager, state["block_reason"], level="WARNING")
        return state

    status = (artifacts.get("status") or "").strip().lower()
    if status in {"failed", "errored", "plan_only", "unknown", ""}:
        state["block_reason"] = (
            f"Testing status is '{status or 'unknown'}'. Deployment will continue because "
            "the user explicitly requested deployment, but this should be reviewed."
        )
        state["testing_gate"] = f"warning_status_{status or 'unknown'}"
        broadcast_log(manager, state["block_reason"], level="WARNING")
        return state

    te = artifacts.get("test_execution") or {}
    failed = int(te.get("failed") or 0)
    errors = int(te.get("errors") or 0)
    total = int(te.get("total") or 0)
    passed = int(te.get("passed") or 0)
    if failed or errors:
        state["block_reason"] = (
            f"Testing reported {failed} failed and {errors} errored test(s) "
            f"out of {total}. Deployment will continue because the user explicitly "
            "requested deployment, but these failures should be reviewed."
        )
        state["testing_gate"] = "warning_failures"
        broadcast_log(manager, state["block_reason"], level="WARNING")
        return state

    state["testing_gate"] = "passed"
    broadcast_log(
        manager,
        f"Testing gate passed: {passed}/{total} tests passed (status={status}).",
        level="INFO",
    )
    return state


def _detect_existing_pipeline_yaml(clone_dir: str) -> str | None:
    """Return the relative path of the first existing Azure pipeline YAML found.

    Checks azure-pipelines.yml at root, plus common variants used in real
    repos (.azure-pipelines/*.yml, pipelines/*.yml). Returns None if none
    found. The presence of any one is enough to skip generation.
    """
    if os.path.isfile(os.path.join(clone_dir, "azure-pipelines.yml")):
        return "azure-pipelines.yml"
    for sub in (".azure-pipelines", "pipelines"):
        sub_dir = os.path.join(clone_dir, sub)
        if os.path.isdir(sub_dir):
            for fname in sorted(os.listdir(sub_dir)):
                if fname.endswith((".yml", ".yaml")):
                    return os.path.join(sub, fname)
    return None


def _find_dotnet_project(clone_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (deployment_root, project_path_from_deployment_root) for a .NET app.

    Carelon-style repos place the deployable app under a folder such as
    `.net application/RadAuthPortal/RadAuthPortal.csproj`, with Docker build
    context at `.net application`. This helper keeps that shape dynamic enough
    for other .NET repos while preserving the proven Carelon output.
    """
    candidates: List[str] = []
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "bin", "obj", "node_modules"}]
        for file in files:
            if file.endswith(".csproj") and not file.endswith(".Tests.csproj"):
                candidates.append(os.path.join(root, file))

    if not candidates:
        return None, None

    def _rank(path: str) -> Tuple[int, int]:
        lower = path.lower()
        score = 0
        if ".tests" in lower or "\\tests\\" in lower or "/tests/" in lower:
            score += 100
        if "radauthportal" not in lower:
            score += 10
        return score, len(path)

    project_path = sorted(candidates, key=_rank)[0]
    project_dir = os.path.dirname(project_path)
    deployment_root = clone_dir if project_dir == clone_dir else os.path.dirname(project_dir)
    if deployment_root == clone_dir or not os.path.relpath(project_path, deployment_root).count(os.sep):
        deployment_root = clone_dir

    project_rel = os.path.relpath(project_path, deployment_root).replace("\\", "/")
    return deployment_root, project_rel


def _repo_rel(clone_dir: str, path: str) -> str:
    return os.path.relpath(path, clone_dir).replace("\\", "/")


def _write_managed_file(path: str, content: str, rel_path: str, state: PipelineState) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = ""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        except Exception:
            existing = ""
    if existing == content:
        state.setdefault("existing_skipped", []).append(rel_path)
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    state.setdefault("created_files", []).append(rel_path if not existing else f"{rel_path} (updated)")


def _dotnet_dockerfile(project_rel: str) -> str:
    project_dir = os.path.dirname(project_rel).replace("\\", "/")
    project_file = os.path.basename(project_rel)
    dll_name = os.path.splitext(project_file)[0] + ".dll"
    return f"""# syntax=docker/dockerfile:1.7
# ASP.NET Core MVC on .NET 10
# Multi-stage build: SDK to compile, ASP.NET runtime to serve.

ARG DOTNET_VERSION=10.0

FROM mcr.microsoft.com/dotnet/sdk:${{DOTNET_VERSION}} AS build
WORKDIR /src

COPY {project_rel} {project_dir}/
RUN dotnet restore {project_rel}

COPY . .
RUN dotnet publish {project_rel} \\
    -c Release \\
    -o /app/publish \\
    --no-restore \\
    /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:${{DOTNET_VERSION}} AS runtime
WORKDIR /app

RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl \\
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/publish .

RUN mkdir -p /app/App_Data && chown -R app:app /app

USER app

ENV ASPNETCORE_URLS=http://+:8080 \\
    ASPNETCORE_ENVIRONMENT=Production \\
    DOTNET_RUNNING_IN_CONTAINER=true \\
    DOTNET_NOLOGO=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \\
    CMD curl -fsS http://localhost:8080/Cases || exit 1

ENTRYPOINT ["dotnet", "{dll_name}"]
"""


def _dotnet_dockerignore() -> str:
    return """**/bin
**/obj
**/.vs
**/.vscode
**/.idea
**/*.user
**/*.suo
**/node_modules
.git
.gitignore
.dockerignore
Dockerfile
docker-compose*.yml
azure-pipelines.yml
sonar-project.properties
infra/
k8s/
argocd/
DEPLOY.md
README.md
**/App_Data/*.db
**/App_Data/*.db-journal
"""


def _dotnet_azure_pipeline(branch: str, deployment_root_rel: str, project_rel_from_repo: str) -> str:
    branch = branch or "main"
    root = deployment_root_rel.replace("\\", "/")
    dockerfile_path = f"{root}/Dockerfile" if root != "." else "Dockerfile"
    build_context = root
    return f"""# Carelon - build + push image to ACR. Deployment is handled by ArgoCD.
#
# Required ADO service connection:
#   ACR-RadAuth  (Docker Registry -> Azure Container Registry)

trigger:
  branches:
    include:
      - main
      - {branch}
  paths:
    exclude:
      - .net application/Manifest/**
      - README.md

variables:
  imageName: carelon
  imageTag: $(Build.BuildId)

pool:
  vmImage: ubuntu-latest

steps:
  - task: UseDotNet@2
    displayName: Install .NET 10 SDK
    inputs:
      packageType: sdk
      version: '10.0.x'

  - script: dotnet restore "{project_rel_from_repo}"
    displayName: dotnet restore

  - script: dotnet build "{project_rel_from_repo}" -c Release --no-restore
    displayName: dotnet build

  - task: Docker@2
    displayName: Build & push image to ACR
    inputs:
      command: buildAndPush
      containerRegistry: ACR-RadAuth
      repository: $(imageName)
      Dockerfile: "{dockerfile_path}"
      buildContext: "{build_context}"
      tags: |
        $(imageTag)
        latest
"""


def _dotnet_namespace_manifest() -> str:
    return """apiVersion: v1
kind: Namespace
metadata:
  name: carelon
"""


def _dotnet_deploy_manifest() -> str:
    return """---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: sa-carelon
  namespace: carelon
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: carelon-data
  namespace: carelon
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: managed-csi
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Service
metadata:
  name: carelon
  namespace: carelon
  labels:
    app: carelon
spec:
  type: LoadBalancer
  ports:
    - name: http
      port: 80
      targetPort: 8080
  selector:
    app: carelon
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: carelon
  namespace: carelon
  labels:
    app: carelon
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: carelon
  template:
    metadata:
      labels:
        app: carelon
    spec:
      serviceAccountName: sa-carelon
      securityContext:
        runAsNonRoot: true
        runAsUser: 1654
        fsGroup: 1654
      containers:
        - name: carelon
          image: acradodepdotnet.azurecr.io/carelon:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 8080
          env:
            - name: ASPNETCORE_ENVIRONMENT
              value: Production
            - name: ConnectionStrings__DefaultConnection
              value: "Data Source=/app/App_Data/radauth.db"
          readinessProbe:
            httpGet:
              path: /Cases
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /Cases
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          volumeMounts:
            - name: app-data
              mountPath: /app/App_Data
      volumes:
        - name: app-data
          persistentVolumeClaim:
            claimName: carelon-data
      restartPolicy: Always
"""


def _generate_dotnet_managed_deployment_files(state: PipelineState, clone_dir: str, branch: str) -> bool:
    deployment_root, project_rel = _find_dotnet_project(clone_dir)
    if not deployment_root or not project_rel:
        return False

    deployment_root_rel = _repo_rel(clone_dir, deployment_root)
    project_rel_from_repo = _repo_rel(clone_dir, os.path.join(deployment_root, project_rel))

    files = {
        os.path.join(deployment_root, "Dockerfile"): _dotnet_dockerfile(project_rel),
        os.path.join(deployment_root, ".dockerignore"): _dotnet_dockerignore(),
        os.path.join(clone_dir, "azure-pipelines.yml"): _dotnet_azure_pipeline(
            branch, deployment_root_rel, project_rel_from_repo
        ),
        os.path.join(clone_dir, "Manifest", "carelon-deploy.yaml"): _dotnet_deploy_manifest(),
        os.path.join(clone_dir, "Manifest", "namespace.yaml"): _dotnet_namespace_manifest(),
    }

    for path, content in files.items():
        _write_managed_file(path, content, _repo_rel(clone_dir, path), state)

    state["dockerfile_content"] = files[os.path.join(deployment_root, "Dockerfile")]
    state["cicd_pipeline_azure"] = files[os.path.join(clone_dir, "azure-pipelines.yml")]
    state["deployment_file_paths"] = [_repo_rel(clone_dir, path) for path in files]
    state["existing_pipeline_yaml"] = "azure-pipelines.yml"
    state["deployment_manifest"] = files[os.path.join(clone_dir, "Manifest", "carelon-deploy.yaml")]
    broadcast_log(
        manager,
        f"Applied deterministic .NET deployment assets under {deployment_root_rel}",
        level="INFO",
    )
    return True


@tool
async def generate_deployment_files(state: PipelineState) -> PipelineState:
    """Generate Dockerfile + azure-pipelines.yml only when missing.

    Phase 9.1 — never overwrite existing deployment files. Detect each
    file in the cloned repo and:
      - if missing → generate via Claude, append to created_files
      - if existing → skip generation, append to existing_skipped
    Tracking lists in state are consumed by the structured summary in
    Phase 9.5 so the user sees exactly what changed.
    """
    clone_dir = state.get("ado_clone_dir", "")
    codebase = state.get("codebase_content", "")
    tech_stack = state.get("tech_stack", "generic")
    branch = state.get("ado_branch", "main")
    if not clone_dir or not codebase:
        state.setdefault("errors", []).append("No codebase content for deployment file generation")
        return state

    state.setdefault("created_files", [])
    state.setdefault("existing_skipped", [])

    if (tech_stack == "dotnet" or _find_dotnet_project(clone_dir)[0]) and _generate_dotnet_managed_deployment_files(state, clone_dir, branch):
        summary = []
        if state["created_files"]:
            summary.append(f"created/updated: {', '.join(state['created_files'])}")
        if state["existing_skipped"]:
            summary.append(f"unchanged: {', '.join(state['existing_skipped'])}")
        broadcast_log(manager, f"Deployment files — {' | '.join(summary) if summary else 'no action'}", level="INFO")
        return state

    ctx = _context_header(state.get("session_context", ""))

    # Dockerfile
    dockerfile_path = os.path.join(clone_dir, "Dockerfile")
    if os.path.isfile(dockerfile_path):
        broadcast_log(manager, "Dockerfile already exists — skipping generation", level="INFO")
        state["existing_skipped"].append("Dockerfile")
    else:
        broadcast_log(manager, f"Generating Dockerfile for {tech_stack}...", level="INFO")
        user_content_dockerfile = (
            f"{ctx}\nTech stack detected: {tech_stack}\n\n"
            f"Generate a Dockerfile for the following codebase:\n\n{codebase}"
        )
        try:
            dockerfile_text = await _run_generation(state, _DOCKERFILE_SYSTEM, user_content_dockerfile)
            dockerfile_text = _strip_md_fences(dockerfile_text)
            state["dockerfile_content"] = dockerfile_text
            with open(dockerfile_path, "w") as f:
                f.write(dockerfile_text)
            state["created_files"].append("Dockerfile")
        except Exception as exc:
            err = classify_error(exc, "Dockerfile generation")
            state.setdefault("errors", []).append(err)
            return state

    # azure-pipelines.yml (also detect common variants like .azure-pipelines/*.yml)
    existing_yaml_rel = _detect_existing_pipeline_yaml(clone_dir)
    if existing_yaml_rel:
        broadcast_log(manager, f"Pipeline YAML already exists at {existing_yaml_rel} — skipping generation", level="INFO")
        state["existing_skipped"].append(existing_yaml_rel)
        # Persist the existing path so trigger_azure_pipeline can wire to it
        state["existing_pipeline_yaml"] = existing_yaml_rel
    else:
        broadcast_log(manager, f"Generating azure-pipelines.yml for {tech_stack}...", level="INFO")
        user_content_pipeline = (
            f"{ctx}\nTech stack: {tech_stack}\nBranch to trigger on: {branch}\n\n"
            f"## Codebase\n{codebase}"
        )
        try:
            raw = await _run_generation(state, _CICD_PIPELINE_SYSTEM, user_content_pipeline)
            if "---GITHUB_ACTIONS---" in raw:
                azure_part = raw.split("---GITHUB_ACTIONS---", 1)[0].replace("---AZURE_PIPELINES---", "").strip()
            else:
                azure_part = raw
            azure_part = _strip_md_fences(azure_part)
            state["cicd_pipeline_azure"] = azure_part
            with open(os.path.join(clone_dir, "azure-pipelines.yml"), "w") as f:
                f.write(azure_part)
            state["created_files"].append("azure-pipelines.yml")
        except Exception as exc:
            err = classify_error(exc, "azure-pipelines.yml generation")
            state.setdefault("errors", []).append(err)
            return state

    summary = []
    if state["created_files"]:
        summary.append(f"created: {', '.join(state['created_files'])}")
    if state["existing_skipped"]:
        summary.append(f"skipped (existing): {', '.join(state['existing_skipped'])}")
    broadcast_log(manager, f"Deployment files — {' | '.join(summary) if summary else 'no action'}", level="INFO")
    return state


@tool
async def commit_and_push_to_ado(state: PipelineState) -> PipelineState:
    """Commit deployment files and push to Azure Repos.

    Phase 9.2 — strengthen the no-changes path. After staging:
      - if `git status --porcelain` empty → skip commit + push entirely,
        set push_status='no_changes', commit_sha=None. Pipeline trigger
        downstream still proceeds against the existing tip.
      - if non-empty → commit, capture commit SHA via `git rev-parse HEAD`,
        push, set push_status='pushed' on success. State fields drive
        the structured summary in Phase 9.5.
    Force-push is never attempted (and never has been).
    """
    clone_dir = state.get("ado_clone_dir", "")
    branch = state.get("ado_branch", "main")
    if not clone_dir:
        state.setdefault("errors", []).append("No cloned dir — cannot push")
        state["push_status"] = "failed"
        return state

    broadcast_log(manager, "Committing and pushing deployment files...", level="INFO")
    loop = asyncio.get_event_loop()

    await loop.run_in_executor(None, _run_git, ["config", "user.email", "deployment-agent@sdlc.local"], clone_dir)
    await loop.run_in_executor(None, _run_git, ["config", "user.name", "Deployment Agent"], clone_dir)
    # Stage only the deployment files we care about; both may or may not exist
    # on disk (Phase 9.1 may have skipped one) — `git add` of a missing path is
    # a no-op so this is safe.
    deployment_paths = state.get("deployment_file_paths") or ["Dockerfile", "azure-pipelines.yml"]
    ok_add, add_output = await loop.run_in_executor(None, _run_git, ["add", "--", *deployment_paths], clone_dir)
    if not ok_add:
        # Pathspec handling on Windows + paths with spaces can be brittle in
        # older Git builds. Fall back to a repository-wide stage in the fresh
        # deployment clone so generated assets are not left untracked.
        broadcast_log(manager, f"Explicit git add failed; retrying git add -A ({add_output[:200]})", level="WARNING")
        ok_add, add_output = await loop.run_in_executor(None, _run_git, ["add", "-A"], clone_dir)
    if not ok_add:
        err = f"git add failed: {add_output[:300]}"
        state.setdefault("errors", []).append(err)
        state["push_status"] = "failed"
        broadcast_log(manager, err, level="ERROR")
        return state

    ok, status_out = await loop.run_in_executor(None, _run_git, ["status", "--porcelain"], clone_dir)
    ok_cached, cached_out = await loop.run_in_executor(None, _run_git, ["diff", "--cached", "--name-only"], clone_dir)
    if status_out.strip() and not (cached_out or "").strip():
        broadcast_log(manager, "Working tree has deployment changes but nothing staged; retrying git add -A", level="WARNING")
        ok_add, add_output = await loop.run_in_executor(None, _run_git, ["add", "-A"], clone_dir)
        if not ok_add:
            err = f"git add -A failed: {add_output[:300]}"
            state.setdefault("errors", []).append(err)
            state["push_status"] = "failed"
            broadcast_log(manager, err, level="ERROR")
            return state
        ok_cached, cached_out = await loop.run_in_executor(None, _run_git, ["diff", "--cached", "--name-only"], clone_dir)

    if not status_out.strip() or not (cached_out or "").strip():
        broadcast_log(manager, "No changes to commit (deployment files already up to date)", level="INFO")
        state["push_status"] = "no_changes"
        state["commit_sha"] = None
        # Mark push_succeeded for any legacy consumer; the pipeline trigger
        # step still runs downstream and works against the existing tip.
        state["push_succeeded"] = True
        return state

    ok, output = await loop.run_in_executor(
        None, _run_git, ["commit", "-m", "Deployment Agent: add missing deployment assets"], clone_dir
    )
    if not ok:
        err = f"git commit failed: {output[:300]}"
        state.setdefault("errors", []).append(err)
        state["push_status"] = "failed"
        broadcast_log(manager, err, level="ERROR")
        return state

    # Capture the commit SHA so the summary can show it (also persisted to
    # the deployment artifact via the existing PATCH path).
    ok_sha, sha_out = await loop.run_in_executor(None, _run_git, ["rev-parse", "HEAD"], clone_dir)
    if ok_sha:
        state["commit_sha"] = sha_out.strip()[:12]

    ok, output = await loop.run_in_executor(
        None, _run_git, ["push", "origin", f"HEAD:{branch}"], clone_dir
    )
    if not ok:
        # Phase 9.7 — friendly classification for the common push failures.
        low = (output or "").lower()
        if "403" in low or "forbidden" in low or "permission" in low:
            err = (
                "Push rejected (HTTP 403). Your PAT has Code Read but not Code "
                "Write permission on this repo, or the branch is protected."
            )
        elif "401" in low or "unauthorized" in low or "authentication failed" in low:
            err = (
                "Push rejected (HTTP 401). Your PAT is missing or expired."
            )
        elif "rejected" in low and "non-fast-forward" in low:
            err = (
                "Push rejected as non-fast-forward — the remote branch has new "
                "commits since the clone. Re-run deployment to pick up the latest."
            )
        elif "protected" in low:
            err = (
                "Push rejected — branch is protected. Open a PR manually or "
                "ask an admin to relax the protection rule."
            )
        else:
            err = f"git push failed: {output[:300]}"
        state.setdefault("errors", []).append(err)
        state["push_status"] = "failed"
        broadcast_log(manager, err, level="ERROR")
        return state

    state["push_succeeded"] = True
    state["push_status"] = "pushed"
    broadcast_log(
        manager,
        f"Pushed deployment files to origin/{branch} (sha {state.get('commit_sha', 'unknown')})",
        level="INFO",
    )
    return state


@tool
async def trigger_azure_pipeline(state: PipelineState) -> PipelineState:
    """Ensure an Azure Pipeline definition exists, then trigger a run."""
    project = state.get("ado_project", "")
    repo = state.get("ado_repo", "")
    branch = state.get("ado_branch", "main")
    if not project or not repo:
        state.setdefault("errors", []).append("Missing ADO project/repo — cannot trigger pipeline")
        state["pipeline_trigger_status"] = "failed"
        return state
    from shared.services.ado_repos import resolve_auth
    _org_url, _pat = await resolve_auth(state.get("tenant_id", ""))

    if not _pat or not _org_url:
        state.setdefault("errors", []).append(
            "Azure DevOps is not connected for your organization — connect it on the Integrations page")
        state["pipeline_trigger_status"] = "failed"
        return state

    org_url = _org_url
    auth = base64.b64encode(f":{_pat}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    pipeline_name = f"{repo}-deployment"
    broadcast_log(manager, f"Triggering Azure Pipeline '{pipeline_name}'...", level="INFO")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            list_url = f"{org_url}/{project}/_apis/pipelines?api-version=7.1-preview.1"
            r = await client.get(list_url, headers=headers)
            pipeline_id = None
            if r.status_code == 200:
                for p in r.json().get("value", []):
                    if p.get("name") == pipeline_name:
                        pipeline_id = p.get("id")
                        break

            if pipeline_id is None:
                repo_meta_url = f"{org_url}/{project}/_apis/git/repositories/{repo}?api-version=7.1-preview.1"
                rr = await client.get(repo_meta_url, headers=headers)
                if rr.status_code != 200:
                    err = f"Could not fetch repo metadata: {rr.status_code} {rr.text[:200]}"
                    state.setdefault("errors", []).append(err)
                    state["pipeline_trigger_status"] = "failed"
                    return state
                repo_id = rr.json().get("id")

                create_payload = {
                    "name": pipeline_name,
                    "folder": "/",
                    "configuration": {
                        "type": "yaml",
                        "path": "/azure-pipelines.yml",
                        "repository": {"id": repo_id, "name": repo, "type": "azureReposGit"},
                    },
                }
                cr = await client.post(list_url, headers=headers, json=create_payload)
                if cr.status_code not in (200, 201):
                    err = f"Pipeline creation failed: {cr.status_code} {cr.text[:300]}"
                    state.setdefault("errors", []).append(err)
                    state["pipeline_trigger_status"] = "failed"
                    return state
                pipeline_id = cr.json().get("id")
                broadcast_log(manager, f"Created pipeline definition (id={pipeline_id})", level="INFO")

            run_url = f"{org_url}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1-preview.1"
            run_payload = {
                "resources": {"repositories": {"self": {"refName": f"refs/heads/{branch}"}}}
            }
            rr = await client.post(run_url, headers=headers, json=run_payload)
            if rr.status_code not in (200, 201):
                err = f"Pipeline trigger failed: {rr.status_code} {rr.text[:300]}"
                state.setdefault("errors", []).append(err)
                state["pipeline_trigger_status"] = "failed"
                return state
            run = rr.json()
            run_id = run.get("id")
            web_url = (run.get("_links", {}).get("web", {}) or {}).get("href") or f"{org_url}/{project}/_build/results?buildId={run_id}"
            state["pipeline_run_id"] = str(run_id)
            state["pipeline_run_url"] = web_url
            state["pipeline_trigger_status"] = "triggered"
            broadcast_log(manager, f"Pipeline run started: {web_url}", level="INFO")
    except Exception as exc:
        err = classify_error(exc, "Azure Pipeline trigger")
        state.setdefault("errors", []).append(err)
        state["pipeline_trigger_status"] = "failed"
        broadcast_log(manager, err, level="ERROR")

    state["pipeline_provider"] = "azure_pipelines"
    return state


@tool
async def trigger_github_actions_workflow(state: PipelineState) -> PipelineState:
    """Dispatch a GitHub Actions workflow and correlate the resulting run.

    Writes the SAME state keys as trigger_azure_pipeline — pipeline_run_id,
    pipeline_run_url, pipeline_trigger_status — so the pipeline summary, artifact
    persistence, and the whole existing frontend deployment surface work unchanged.

    THE 204 PROBLEM: POST .../workflows/{id}/dispatches returns 204 with an empty body
    and no run id. There is no API that returns one. So after dispatching we poll the
    runs list for a workflow_dispatch run on the same branch created at/after the
    moment we dispatched, and correlate on that.

    FAIL-SOFT: if the poll finds nothing (a slow queue, or concurrent runs on the same
    branch), the dispatch still SUCCEEDED — status stays "triggered" and the run URL
    falls back to the repo's Actions page. A dispatch that worked must never read as a
    failure.
    """
    import time as _time

    dep_req = state.get("deployment_request") or {}
    branch = state.get("ado_branch", "main") or "main"
    tenant_id = state.get("tenant_id", "")

    repo = (dep_req.get("gha_repo") or state.get("gha_repo") or state.get("ado_repo") or "").strip()
    owner = (dep_req.get("gha_owner") or state.get("gha_owner") or "").strip()
    workflow = (dep_req.get("gha_workflow") or "deploy.yml").strip()

    if not repo:
        state.setdefault("errors", []).append(
            "No GitHub repository to deploy — set 'gha_repo' in the deployment request."
        )
        state["pipeline_trigger_status"] = "failed"
        state["pipeline_provider"] = "github_actions"
        return state

    # [ASSUMED] A7 — the deploy-target dialog is ADO-only, so with no explicit gha_repo
    # the ADO repo name is reused as the GitHub repo name. A 404 below says so plainly.
    target = f"{owner}/{repo}" if owner and "/" not in repo else repo

    try:
        from config.connector_factory import get_connector_for_session

        # KNOWN BROKEN, LEFT BROKEN ON PURPOSE — do not "fix" this by adding
        # unrestricted=True without reading this first.
        #
        # This names neither a project nor a stage nor `unrestricted`, and the factory
        # documents that combination as permitting NOTHING: it returns
        # ScopedConnector(raw, None), and `permits(None, "write")` is False, so the
        # dispatch below raises ConnectorAccessDenied every time. GitHub Actions
        # dispatch from this graph therefore does not work today.
        #
        # WHY NOT unrestricted=True. That is the fail-open door. Dispatching a deploy
        # workflow is an AGENT action taken on a project's behalf, not the org-level
        # admin work the flag exists for (health probes, tenant setup) — the two other
        # sites fixed alongside this one are that, and this is not. Opening it here
        # would let any run dispatch a deploy with no access check at all.
        #
        # WHY NOT SCOPED EITHER, YET. `PipelineState` carries `tenant_id` but no
        # project_id, and the deployment agent never sets the project_id ContextVar, so
        # there is genuinely nothing to scope against without threading a project
        # through this graph. That is the real fix, and it belongs with the deployment
        # agent's rebuild — it is not in `builtAgents`, so nothing ships on this path.
        #
        # It fails LOUDLY (state["errors"], pipeline_trigger_status="failed", an ERROR
        # broadcast), which is why leaving it is acceptable: it is visibly broken
        # rather than silently dead.
        connector = await get_connector_for_session(
            kind="github_actions", tenant_id=tenant_id
        )
        dispatched_at = _time.time()
        await connector.write_adapter(
            "dispatch_workflow",
            repo=target,
            workflow=workflow,
            ref=branch,
            tenant_id=tenant_id or "default",
        )
        broadcast_log(
            manager,
            f"Dispatched GitHub Actions workflow '{workflow}' on {target}@{branch}",
            level="INFO",
        )

        # Correlate the run the dispatch created.
        run_id, web_url = "", ""
        for _ in range(6):
            await asyncio.sleep(2)
            runs = await connector.read_adapter(
                "list_workflow_runs",
                repo=target,
                branch=branch,
                event="workflow_dispatch",
                per_page=5,
                tenant_id=tenant_id or "default",
            )
            for run in runs or []:
                created = run.get("created_at", "")
                try:
                    from datetime import datetime

                    created_epoch = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, AttributeError):
                    continue
                # 60s of slack absorbs clock skew between us and GitHub.
                if created_epoch >= dispatched_at - 60:
                    run_id = str(run.get("id", ""))
                    web_url = run.get("html_url", "")
                    break
            if run_id:
                break

        state["pipeline_trigger_status"] = "triggered"
        state["pipeline_run_id"] = run_id
        state["pipeline_run_url"] = web_url or f"https://github.com/{target}/actions"
        if run_id:
            broadcast_log(manager, f"Pipeline run started: {state['pipeline_run_url']}", level="INFO")
        else:
            # Informational, NOT an error — the workflow was dispatched successfully.
            broadcast_log(
                manager,
                "Workflow dispatched; the run id could not be correlated yet. "
                f"Track it at {state['pipeline_run_url']}",
                level="INFO",
            )
    except Exception as exc:
        detail = classify_error(exc, "GitHub Actions dispatch")
        if "404" in str(exc):
            detail = (
                f"GitHub repo '{target}' or workflow '{workflow}' not found — set "
                "'gha_repo' / 'gha_workflow' in the deployment request, and confirm the "
                "workflow declares a workflow_dispatch trigger."
            )
        state.setdefault("errors", []).append(detail)
        state["pipeline_trigger_status"] = "failed"
        broadcast_log(manager, detail, level="ERROR")

    state["pipeline_provider"] = "github_actions"
    return state


@tool
async def trigger_deployment_pipeline(state: PipelineState) -> PipelineState:
    """Trigger the deployment pipeline on whichever CI provider this repo uses.

    The provider is only knowable at execution time: _build_step_plan runs before
    resolve_ado_target, so deploy_via does not exist when the plan is built. This
    dispatcher reads it from state instead, which is the only place the answer lives.

    Defaults to Azure Pipelines when deploy_via is absent, so every existing ADO run
    behaves exactly as it did before this tool existed.
    """
    via = (
        state.get("deploy_via")
        or (state.get("deployment_request") or {}).get("deploy_via")
        or "azure_pipelines"
    )
    if via == "github_actions":
        return await trigger_github_actions_workflow.ainvoke({"state": state})
    if via == "argocd":
        # GitOps: the commit IS the deployment — Argo reconciles from the repo, so
        # there is nothing to trigger. Recorded rather than silently skipped.
        state["pipeline_trigger_status"] = "triggered"
        state["pipeline_provider"] = "argocd"
        broadcast_log(
            manager,
            "Argo CD is GitOps-driven — the pushed manifests are the deployment; "
            "no pipeline trigger is required.",
            level="INFO",
        )
        return state
    return await trigger_azure_pipeline.ainvoke({"state": state})


@tool
async def update_work_items_to_deployed(state: PipelineState) -> PipelineState:
    """Move all work items from this pipeline run to their terminal 'Done' state.

    Uses the structured work_items list from requirements_payload so every type
    (Epic, Issue, Task, User Story) is updated through the active PM provider.
    Providers map the canonical 'Done' intent to the available terminal state.
    """
    try:
        from config.connectors.context import get_connector
        req_payload = state.get("requirements_payload") or {}
        try:
            connector = get_connector()
        except RuntimeError as _pna:
            broadcast_log(manager, f"Connector unavailable — skipping work item update. {_pna}", level="WARNING")
            return state

        # Prefer structured work_items from requirements_payload over regex
        work_items = req_payload.get("work_items") or []
        project = req_payload.get("project") or state.get("ado_project") or ""

        # Fallback: regex on context string for sessions predating work_items field
        if not work_items:
            session_ctx = state.get("session_context", "")
            raw_ids = re.findall(r'(?:Story|Work Item|Issue|Task|Epic)\s*(?:ID)?[:\s#]+(\d+)',
                                 session_ctx, re.IGNORECASE)
            work_items = [{"id": int(i), "type": "User Story"} for i in raw_ids]
            if not project:
                proj_match = re.search(r'\[REQUIREMENTS — Project: ([^\]]+)\]', session_ctx)
                project = proj_match.group(1).strip() if proj_match else ""

        if not work_items or not project or project == "N/A":
            broadcast_log(manager, "No work items or project found — skipping board update.", level="WARNING")
            return state

        for item in work_items[:20]:
            wid = item.get("id")
            wtype = item.get("type", "User Story")
            if not wid:
                continue
            try:
                # "Done" is the universal terminal intent; providers resolve it
                # to the closest available workflow transition.
                await connector.write_adapter("move_item_state", project=project, item_id=wid, new_state="Done")
                broadcast_log(manager, f"#{wid} ({wtype}) → Done", level="INFO")
            except Exception as exc:
                logger.warning("Failed to update #%s (%s): %s", wid, wtype, exc)
    except Exception as exc:
        logger.warning("Work item update skipped (non-critical): %s", exc)
    return state


@tool
async def write_all_outputs(state: PipelineState) -> PipelineState:
    """Write all generated artifacts to disk and broadcast download URLs."""
    user_id = get_user_id()
    session_id = get_session_id()
    output_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"

    files_written: List[str] = []
    files_skipped: List[str] = []

    try:
        os.makedirs(output_dir, exist_ok=True)

        async def _write(filename: str, header: str, content: str) -> None:
            path = os.path.join(output_dir, filename)
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(header + content)
            files_written.append(path)
            await broadcast_file_generated(session_id, filename, path)

        def _ok(val: str) -> bool:
            return bool(val) and not val.startswith(("⚠", "❌", "# ⚠", "# ❌"))

        if _ok(state.get("deployment_plan", "")):
            await _write("deployment_plan.md", "# Deployment Plan\n\n*Generated by AI Deployment Agent*\n\n", state["deployment_plan"])
        else:
            files_skipped.append("deployment_plan.md")

        if _ok(state.get("risk_report", "")):
            await _write("risk_analysis_report.md", "# Risk Analysis Report\n\n*Generated by AI Deployment Agent*\n\n", state["risk_report"])
        else:
            files_skipped.append("risk_analysis_report.md")

        if _ok(state.get("deploy_report", "")):
            await _write("deployment_validation_report.md", "# Deployment Validation Report\n\n*Generated by AI Deployment Agent*\n\n", state["deploy_report"])
        else:
            files_skipped.append("deployment_validation_report.md")

        if _ok(state.get("dockerfile_content", "")) and state.get("codebase_extracted"):
            await _write("Dockerfile", "", state["dockerfile_content"])
        else:
            files_skipped.append("Dockerfile")

        if _ok(state.get("docker_compose", "")):
            await _write("docker-compose.yml", "", state["docker_compose"])
        else:
            files_skipped.append("docker-compose.yml")

        if _ok(state.get("cicd_pipeline_azure", "")):
            await _write("azure-pipelines.yml", "", state["cicd_pipeline_azure"])
        else:
            files_skipped.append("azure-pipelines.yml")

        if _ok(state.get("cicd_pipeline_github", "")):
            gh_dir = os.path.join(output_dir, ".github", "workflows")
            os.makedirs(gh_dir, exist_ok=True)
            gh_path = os.path.join(gh_dir, "deploy.yml")
            async with aiofiles.open(gh_path, "w", encoding="utf-8") as f:
                await f.write(state["cicd_pipeline_github"])
            files_written.append(gh_path)
            await broadcast_file_generated(session_id, "deploy.yml", gh_path)
        else:
            files_skipped.append(".github/workflows/deploy.yml")

        if _ok(state.get("readme_content", "")) and state.get("codebase_extracted"):
            await _write("README.md", "", state["readme_content"])
        else:
            files_skipped.append("README.md")

        if _ok(state.get("docker_instructions", "")):
            await _write("docker_build_instructions.md", "# Docker Build and Deployment Instructions\n\n*Generated by AI Deployment Agent*\n\n", state["docker_instructions"])
        else:
            files_skipped.append("docker_build_instructions.md")

        if _ok(state.get("rollback_plan", "")):
            await _write("rollback_plan.md", "# Rollback Plan\n\n*Generated by AI Deployment Agent*\n\n", state["rollback_plan"])
        else:
            files_skipped.append("rollback_plan.md")

        errors = state.get("errors", [])
        if errors:
            err_path = os.path.join(output_dir, "pipeline_errors.log")
            async with aiofiles.open(err_path, "w", encoding="utf-8") as f:
                await f.write(f"# Pipeline Errors\n\nTotal: {len(errors)}\n\n")
                for i, e in enumerate(errors, 1):
                    await f.write(f"{i}. {e}\n")
            files_written.append(err_path)
            await broadcast_file_generated(session_id, "pipeline_errors.log", err_path)

        msg = f"Output writing complete: {len(files_written)} files written."
        if files_skipped:
            msg += f" Skipped: {', '.join(files_skipped)}"
        broadcast_log(manager, msg, level="INFO")

    except Exception as exc:
        err = classify_error(exc, "output writing")
        logger.error("Output writing error: %s", exc)
        broadcast_log(manager, err, level="ERROR")
        state.setdefault("errors", []).append(err)

    return state


@tool
async def print_pipeline_summary(state: PipelineState) -> PipelineState:
    """Log a summary of what was generated in this pipeline run."""
    try:
        broadcast_log(manager, "Generating pipeline summary", level="INFO")

        def _ok(val: str) -> bool:
            return bool(val) and not val.startswith(("⚠", "❌", "# ⚠", "# ❌"))

        checks = {
            "Deployment Plan": _ok(state.get("deployment_plan", "")),
            "Risk Analysis": _ok(state.get("risk_report", "")),
            "Deployment Validation": _ok(state.get("deploy_report", "")),
            "Dockerfile": _ok(state.get("dockerfile_content", "")),
            "Docker Compose": _ok(state.get("docker_compose", "")),
            "Azure Pipelines CI/CD": _ok(state.get("cicd_pipeline_azure", "")),
            "GitHub Actions CI/CD": _ok(state.get("cicd_pipeline_github", "")),
            "README": _ok(state.get("readme_content", "")),
            "Docker Instructions": _ok(state.get("docker_instructions", "")),
            "Rollback Plan": _ok(state.get("rollback_plan", "")),
        }
        decision = state.get("deployment_decision", "")
        if decision:
            lines_extra = [f"\n  Deployment Decision: {decision}"]
        else:
            lines_extra = []
        success = sum(checks.values())
        total = len(checks)
        errors = state.get("errors", [])

        lines = ["", "=" * 70, "DEPLOYMENT PIPELINE SUMMARY", "=" * 70]
        for name, ok in checks.items():
            lines.append(f"  {'✅' if ok else '❌'} {name}")
        lines.extend(lines_extra)
        lines.append(f"\n  Generated: {success}/{total}   Errors: {len(errors)}")
        lines.append("=" * 70)

        for line in lines:
            print(line)

        status = "success" if success == total and not errors else ("partial" if success > 0 else "failed")
        broadcast_log(manager, f"Pipeline complete — status: {status}", level="INFO")

    except Exception as exc:
        logger.error("Error generating pipeline summary: %s", exc)

    return state


# ── Minimal LangGraph wrapper (kept for compatibility) ────────────────────────

pipeline_tools_list = [
    extract_codebase_zip, extract_testing_zip, read_codebase_files, read_testing_files,
    combine_content,
    generate_deployment_plan, generate_risk_analysis, generate_deployment_validation,
    extract_deployment_decision,
    generate_dockerfile, generate_docker_compose, generate_cicd_pipeline,
    generate_readme, generate_docker_instructions, generate_rollback_plan,
    write_all_outputs, print_pipeline_summary, update_work_items_to_deployed,
    # ADO deploy mode
    resolve_ado_target, clone_azure_repo, detect_tech_stack,
    generate_deployment_files, commit_and_push_to_ado,
    # Provider-aware trigger. trigger_azure_pipeline stays directly callable — the
    # dispatcher delegates to it, and existing callers still reference it by name.
    trigger_deployment_pipeline, trigger_azure_pipeline, trigger_github_actions_workflow,
]
