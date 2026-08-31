"""Development Agent — LangGraph ReAct workflow (Claude Anthropic).

Tools available to the agent:
  File:     read_file, write_file, edit_file, list_directory, delete_file, search_codebase
  Git:      clone_repo, create_feature_branch, run_command, git_commit,
            push_branch, create_pr, mark_pr_ready, init_project_structure
  Artifact: read_design_artifact
"""
from __future__ import annotations

import logging
from typing import Annotated, List, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from config.checkpoint import build_checkpointer as _build_checkpointer
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from config.connection_manager import manager
from config.ws_helper import broadcast_log

logger = logging.getLogger(__name__)

from agents_orchestrator.development_agent.tools.file_tools import (
    delete_file,
    edit_file,
    list_directory,
    read_file,
    search_codebase,
    write_file,
)
from agents_orchestrator.development_agent.tools.git_tools import (
    get_ado_context,
    list_ado_projects,
    list_ado_repos,
    list_ado_branches,
    create_ado_repo,
    get_github_context,
    list_github_repos,
    list_github_branches,
    create_github_pr_tool,
    clone_repo,
    create_feature_branch,
    create_pr,
    git_commit,
    init_project_structure,
    mark_pr_ready,
    push_branch,
    run_command,
    update_work_item_state,
    add_pr_comment_to_work_items,
)
from agents_orchestrator.development_agent.tools.work_item_tools import (
    get_work_item,
    list_work_items,
)
from agents_orchestrator.development_agent.tools.artifact_tools import (
    read_design_artifact,
    submit_development_artifacts,
)
from agents_orchestrator.development_agent.tools.generation_tools import (
    generate_project_scaffold,
    generate_component,
    generate_api_endpoint,
    generate_database_migration,
)
from agents_orchestrator.development_agent.tools.lint_tools import lint_and_validate_code
from agents_orchestrator.development_agent.tools.code_execution_tools import execute_code_in_sandbox
from agents_orchestrator.development_agent.prompts.dev_agent_prompt import DEV_SYS_MESSAGE
from langchain_core.tools import tool
from shared.tools.mcp_runtime import get_mcp_tools
from shared.services.skill_runtime import get_skill_tools


# ── State ─────────────────────────────────────────────────────────────────────

class DevAgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    tenant_id: str
    model_id: str | None


# ── Tool registry ─────────────────────────────────────────────────────────────

tools = [
    read_file,
    write_file,
    edit_file,
    list_directory,
    delete_file,
    search_codebase,
    get_ado_context,
    list_ado_projects,
    list_ado_repos,
    list_ado_branches,
    create_ado_repo,
    get_github_context,
    list_github_repos,
    list_github_branches,
    create_github_pr_tool,
    clone_repo,
    create_feature_branch,
    run_command,
    git_commit,
    push_branch,
    create_pr,
    mark_pr_ready,
    init_project_structure,
    update_work_item_state,
    add_pr_comment_to_work_items,
    get_work_item,
    list_work_items,
    read_design_artifact,
    submit_development_artifacts,
    generate_project_scaffold,
    generate_component,
    generate_api_endpoint,
    generate_database_migration,
    lint_and_validate_code,
    execute_code_in_sandbox,
]

_tool_map = {t.name: t for t in tools}


# ── LLM — Claude (Anthropic) ──────────────────────────────────────────────────

# Per-alias LLM cache — keyed by (alias, model). Avoids per-call re-construction
# while supporting per-tenant BYOK keys (Pitfall 4 / architecture.py pattern).
_LLM_CACHE: dict[tuple[str, str], object] = {}


def _build_llm(model: str, litellm_provider: str, api_key: str,
               base_url: str | None, alias: str) -> object:
    """Build (or return cached) ChatLiteLLM. Direct-provider BYOK call.
    Cached by (alias, model); alias is non-secret (SC#5).

    Returns the UNBOUND model — tools are bound per-call in agent_node so that
    per-run MCP tools (which vary by run) are never baked into the shared cache."""
    cache_key = (alias, model)
    if cache_key in _LLM_CACHE:
        return _LLM_CACHE[cache_key]
    # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
    from langchain_litellm import ChatLiteLLM
    kwargs: dict = {
        "model": model,
        "custom_llm_provider": litellm_provider,
        "api_base": base_url,
        "api_key": api_key,
        "max_tokens": 8192,
        "max_retries": 2,
        # Stream tokens so the copilot shows the dev agent's replies live.
        "streaming": True,
    }
    # gpt-5-family models (including gpt-5-codex) reject any temperature other
    # than the default 1 -- litellm raises UnsupportedParamsError pre-call if
    # we pass one. Confirmed live against a real azure/gpt-5-mini deployment
    # (2026-08-31): identical call succeeds the instant temperature is omitted.
    # Every other model keeps the low, determinism-favoring temperature this
    # agent wants for code generation -- this is a narrow exception for the one
    # model family that structurally cannot take the parameter, not a
    # litellm.drop_params=True escape hatch that would silently swallow
    # unsupported params for every model everywhere.
    if "gpt-5" not in model.lower():
        kwargs["temperature"] = 0.1
    instance = ChatLiteLLM(**kwargs)
    _LLM_CACHE[cache_key] = instance
    return instance


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize_messages(messages: list) -> list:
    """Drop ToolMessages whose tool_call_id has no matching AIMessage tool_call."""
    valid_ids: set = set()
    out = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                valid_ids.add(tc["id"])
            out.append(msg)
        elif isinstance(msg, ToolMessage):
            if msg.tool_call_id in valid_ids:
                out.append(msg)
        else:
            out.append(msg)
    return out


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def agent_node(state: DevAgentState):
    from shared.services.model_resolver import resolve_model_for_run, NoModelConfiguredError, ModelNotEnabledError
    tenant_id = state.get("tenant_id", "")
    requested = state.get("model_id")
    try:
        resolved = await resolve_model_for_run(tenant_id, requested)
    except (NoModelConfiguredError, ModelNotEnabledError) as e:
        logger.warning("Dev agent model resolution failed (tenant=%s): %s", tenant_id, type(e).__name__)
        return {"messages": [AIMessage(content=(
            "No usable model is configured for your organization. "
            "An administrator must add and verify a model provider in Org Settings → Model Providers."))]}
    except Exception as e:
        logger.error("Dev agent model resolution error (tenant=%s): %s", tenant_id, type(e).__name__)
        return {"messages": [AIMessage(content=f"Agent error: {type(e).__name__}")]}
    try:
        from shared.services.model_call_wrapper import guarded_completion

        clean = _sanitize_messages(state["messages"])
        llm = _build_llm(resolved.model, resolved.litellm_provider,
                         resolved.api_key, resolved.base_url, resolved.alias)
        bound = llm.bind_tools(tools + get_skill_tools("development") + get_mcp_tools())
        response = await guarded_completion(
            resolved, bound, clean, tenant_id=tenant_id, agent_type="development",
            config={"metadata": {"user_api_key_alias": resolved.alias}},
        )
        return {"messages": [response]}
    except Exception as e:
        logger.error("Dev agent error (tenant=%s alias=%s): %s",
                     tenant_id, resolved.alias, type(e).__name__)
        return {"messages": [AIMessage(content=f"Agent error: {type(e).__name__}")]}


def _tool_label(name: str, args: dict) -> str:
    """Return a human-readable one-line description of what the tool is doing."""
    a = args or {}
    def _path(a: dict) -> str:
        return a.get("relative_path", a.get("path", a.get("file_path", "?")))

    match name:
        case "read_file":
            return f"Reading {_path(a)}"
        case "write_file":
            lines = len((a.get("content", "") or "").splitlines())
            return f"Writing {_path(a)} ({lines} lines)"
        case "edit_file":
            return f"Editing {_path(a)}"
        case "list_directory":
            return f"Listing {a.get('path', a.get('directory', '.'))}"
        case "delete_file":
            return f"Deleting {_path(a)}"
        case "search_codebase":
            return f"Searching for: {a.get('query', a.get('pattern', '?'))}"
        case "run_command":
            cmd = (a.get("command", "") or "").strip()
            return f"$ {cmd[:120]}" if cmd else "Running command"
        case "clone_repo":
            url = a.get("repo_url", "?")
            return f"Cloning {url.split('/')[-1].replace('.git', '')}"
        case "create_feature_branch":
            return f"Creating branch: {a.get('branch_name', '?')}"
        case "git_commit":
            msg = (a.get("message", "") or "").strip()[:80]
            return f"Committing: {msg}"
        case "push_branch":
            return "Pushing branch to remote"
        case "create_pr":
            return f"Creating PR: {(a.get('title', '') or '').strip()[:80]}"
        case "mark_pr_ready":
            return f"Marking PR ready: {a.get('pr_url', '?')}"
        case "init_project_structure":
            return f"Initialising {a.get('project_name', '?')} ({a.get('tech_stack', '?')})"
        case "generate_project_scaffold":
            return f"Generating scaffold: {a.get('project_name', a.get('name', '?'))}"
        case "generate_component":
            fp = a.get("file_path", "?")
            lines = len((a.get("code_content", "") or "").splitlines())
            return f"Generating component: {fp} ({lines} lines)"
        case "generate_api_endpoint":
            fp = a.get("file_path", "?")
            summary = a.get("endpoint_summary", "")
            return f"Generating endpoint: {fp}" + (f" [{summary}]" if summary else "")
        case "generate_database_migration":
            return f"Generating migration: {a.get('file_path', '?')} ({a.get('orm_type', 'sql')})"
        case "lint_and_validate_code":
            return f"Linting {a.get('target_path', a.get('path', 'workspace'))}"
        case "execute_code_in_sandbox":
            cmd = (a.get("command", a.get("code", "")) or "").strip()[:60]
            return f"Sandbox: {cmd}"
        case "read_design_artifact":
            return "Loading design & requirements context"
        case "submit_development_artifacts":
            return "Submitting development artifacts"
        case "update_work_item_state":
            return f"Updating work item #{a.get('work_item_id', '?')} → {a.get('target_state', '?')}"
        case "add_pr_comment_to_work_items":
            return f"Linking PR to work items: {a.get('work_item_ids', [])}"
        case "get_ado_context":
            return "Fetching ADO project context"
        case "get_github_context":
            return "Fetching GitHub credentials"
        case "list_github_repos":
            return f"Listing GitHub repos for: {a.get('org_or_user', '?')}"
        case "list_github_branches":
            return f"Listing GitHub branches: {a.get('owner', '?')}/{a.get('repo', '?')}"
        case "create_github_pr_tool":
            return f"Creating GitHub PR: {(a.get('title', '') or '').strip()[:80]}"
        case _:
            return f"Tool: {name}"



async def tool_node(state: DevAgentState):
    last = state["messages"][-1]
    results = []
    _dyn = get_skill_tools("development") + get_mcp_tools()
    tool_map = {**_tool_map, **{t.name: t for t in _dyn}} if _dyn else _tool_map
    for tc in last.tool_calls:
        args = dict(tc.get("args") or {})
        label = _tool_label(tc["name"], args)
        broadcast_log(manager, label, level="LOGS")
        t = tool_map[tc["name"]]
        try:
            obs = await t.ainvoke(args)
            obs_str = str(obs)
            if tc["name"] in ("run_command", "git_commit", "push_branch", "create_pr"):
                summary = obs_str[:200].replace("\n", " ")
                broadcast_log(manager, f"✓ {tc['name']}: {summary}", level="INFO")
            elif tc["name"] in ("generate_component", "generate_api_endpoint",
                                "generate_database_migration", "write_file"):
                broadcast_log(manager, f"✓ {obs_str[:120]}", level="INFO")
        except Exception as e:
            obs_str = f"Tool error: {e}"
            broadcast_log(manager, f"✗ {tc['name']} failed: {e}", level="ERROR")
        results.append(ToolMessage(content=obs_str, tool_call_id=tc["id"], name=tc["name"]))
    return {"messages": results}


_MAX_TOOL_CALLS = 60  # hard cap per request — breaks infinite retry loops

def should_continue(state: DevAgentState) -> str:
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return END
    tool_call_count = sum(1 for m in state["messages"] if isinstance(m, ToolMessage))
    if tool_call_count >= _MAX_TOOL_CALLS:
        broadcast_log(
            manager,
            f"⚠ Tool call limit ({_MAX_TOOL_CALLS}) reached — stopping to prevent infinite loop. "
            "Commit what is done and report remaining work to the user.",
            level="WARN",
        )
        return END
    return "tools"


# ── Graph ─────────────────────────────────────────────────────────────────────

workflow = StateGraph(DevAgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.add_edge("tools", "agent")
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue)

app = workflow.compile(checkpointer=_build_checkpointer("development"))
