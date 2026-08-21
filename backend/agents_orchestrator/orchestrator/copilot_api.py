"""Orchestrator Copilot WebSocket — one chat socket over a durable pipeline run.

Per user turn, this routes the message into the ACTIVE stage agent's LangGraph app
(session-bound to the run via the Pipeline Session Bridge), streams tokens back,
persists the transcript, emits interactive choice cards for pick-decisions, and
emits role-routed gate state.

Reuse map (confirmed against the tree):
- Bridge:        workflows.activities.pipeline_session.pipeline_session
- Active stage:  runs.current_stage (fallback runs.stage → "requirements")
- Graphs:        agents_orchestrator.requirements_agent.agents.planning.app  (requirements)
                 agents_orchestrator.design_architecture_agent.agents.architecture.app (design)
- Streaming:     <graph>.astream(state, stream_mode="messages", config={thread_id: run_id})
                 modeled on requirements_agent_api._stream_agent_response.
- Auth:          config.auth.ws_ticket.redeem_ws_ticket  (ASYNC; claims = {user_id, tenant_id})
- Perms:         shared.authz.resolver.resolve_permissions_for_user  (roles+tenant → list[str])
- Cards (C3):    agents_orchestrator.orchestrator.copilot_cards  (provider-sourced structured data)
- Gate (C2):     shared.services.orchestrator.gate_routing
- Progression:   shared.services.orchestrator.progression
- Transcript:    shared.services.conversation_service.persist_turn

Defensive posture: the socket is never crashed by a turn-level failure. Every helper
swallows its own errors and (where user-visible) emits an `error` event. The choice
card and gate emission are best-effort ENHANCEMENTS — if provider sourcing or a DB
read fails, the agent still answered in plain text and the turn completes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import select

from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.models.orm import AuditEvent, Project, Run
from shared.services.conversation_service import ensure_session_with_id, persist_turn
from agents_orchestrator.orchestrator.stage_switch import STAGE_IDS, detect_switch
from shared.services.orchestrator.artifacts_view import (
    ARTIFACT_STAGES,
    looks_like_design_doc,
    parse_design_markdown,
    sections_from_run,
    _development_sections,
)
from shared.services.orchestrator.gate_routing import (
    can_user_approve,
    gate_owner_role,
    notify_gate_pending,
)
from shared.services.agent_session_store import fetch_session_artifacts
from shared.services.prompt_runtime import prompt_override_scope, prepare_agent_turn
from shared.services.skill_runtime import skill_context_scope

logger = logging.getLogger(__name__)
copilot_router = APIRouter()

# ── Cooperative turn cancellation ────────────────────────────────────────────
# The copilot WS processes one turn synchronously per received frame, so a "Stop"
# can't travel over the SAME socket (it would sit unread in the buffer until the
# turn finished). It arrives instead via REST (POST /runs/{id}/copilot/cancel-turn
# in runs.py), which flips the flag below. The streaming loop checks it between
# chunks and ends the turn early (flush what's streamed so far + stream_end). This
# is single-process cooperative cancellation — the WS handler and the REST route
# share this process; a multi-worker deployment would back this with Redis pub/sub.
# It interrupts between model/tool steps, not mid-blocking-call — good enough to
# stop a runaway loop or an unwanted long generation without killing the socket.
_cancel_requested: set[str] = set()


def request_turn_cancel(run_id: str) -> None:
    """Signal the in-flight turn for this run to stop (called by the REST route)."""
    _cancel_requested.add(run_id)


def _consume_cancel(run_id: str) -> bool:
    """True once if a cancel was requested for this run; consumes the flag."""
    if run_id in _cancel_requested:
        _cancel_requested.discard(run_id)
        return True
    return False


def _clear_cancel(run_id: str) -> None:
    """Drop any stale cancel flag at the start of a fresh turn."""
    _cancel_requested.discard(run_id)

# Board list-tools whose output should surface an interactive choice card. When the
# agent's last turn called one of these, we re-source the SAME data structurally from
# the provider and emit a ChoiceCard so the user can click instead of typing an id.
_PROJECT_LIST_TOOLS = {"list_board_projects"}
_STORY_LIST_TOOLS = {"list_board_items", "list_board_items_by_state"}

# Stages whose agent is a fixed-pipeline state machine (SuperAgentState-style: reads
# `user_prompt`, writes `final_user_message`) rather than a message-based ReAct agent.
# These take the _stream_state_machine adapter path, not _stream_active.
STATE_MACHINE_STAGES: set[str] = {"testing"}

# Downstream stages whose substantive turn output is a REPORT (findings / results /
# a readiness package) — like Design's document, this belongs in the Artifacts panel,
# not dumped into chat. `_stream_active`/`_stream_state_machine` route their reply into
# a panel artifact (kind="markdown", id=f"{stage}-report") instead of chat, and the turn
# loop persists it via `_capture_stage_report` + a compact chat card.
_REPORT_STAGES = {"code_review", "security", "testing", "deployment", "documentation"}
_STAGE_ARTIFACT_COLUMN = {
    "code_review": "code_review_artifacts", "security": "security_artifacts",
    "testing": "testing_artifacts", "deployment": "deployment_artifacts",
    "documentation": "documentation_artifacts",
    "requirements": "requirements_artifacts",
}

# Tool names whose completion means a report agent just produced generated files (SBOM,
# findings, staged deploy files). When one finishes mid-turn we push the "Generated files"
# tree to the panel immediately, rather than waiting for the whole turn to end.
_ARTIFACT_TOOLS = {
    "generate_sbom", "submit_security_review",       # security
    "submit_code_review",                             # code review
    "stage_deploy_file", "submit_release",            # deployment
}

# Minimum length (chars) a streamed report-stage reply must reach before we treat it as
# the panel report rather than a short conversational turn (e.g. a clarifying question).
_REPORT_MIN_LEN = 400

# Agent-profile prompt-injection routing (design §3.4). MESSAGE_PROMPT_STAGES take the
# system prompt as a SystemMessage in the first-turn state (their graph nodes don't
# self-inject), so the resolved+injected prompt rides in via _stream_active's
# `system_prompt_override`. SELF_INJECT_STAGES' graph nodes read the prompt back from the
# prompt_runtime contextvar (`get_prompt_override(stage) or BASE`), so the resolved prompt
# is set into that contextvar via prompt_override_scope for the length of the turn. The
# testing stage (STATE_MACHINE_STAGES) is in neither set and gets NO override.
MESSAGE_PROMPT_STAGES = {"requirements", "design", "development"}
SELF_INJECT_STAGES = {"code_review", "security", "deployment", "documentation"}


# ── Active-stage graph registry ──────────────────────────────────────────────
def _graph_for(stage: str):
    """Return the compiled LangGraph app for the active stage, or None if unmapped.

    Imports are deferred so importing this module never drags in every agent graph
    (and their heavy deps) at process start."""
    try:
        if stage == "requirements":
            from agents_orchestrator.requirements_agent.agents.planning import app
            return app
        if stage == "design":
            from agents_orchestrator.design_architecture_agent.agents.architecture import app
            return app
        if stage == "development":
            from agents_orchestrator.development_agent.agents.dev_agent import app
            return app
        if stage == "code_review":
            from agents_orchestrator.code_review_agent.agents.reviewer import app
            return app
        if stage == "security":
            from agents_orchestrator.security_agent.agents.scanner import app
            return app
        if stage == "testing":
            # Testing's app is compiled WITHOUT a checkpointer; the Copilot needs one for
            # thread_id-based multi-turn, so recompile the builder with MemorySaver.
            from agents_orchestrator.testing_agent.agents.testing_agent import graph_builder
            from langgraph.checkpoint.memory import MemorySaver
            if not hasattr(_graph_for, "_testing_app"):
                _graph_for._testing_app = graph_builder.compile(checkpointer=MemorySaver())
            return _graph_for._testing_app
        if stage == "deployment":
            from agents_orchestrator.deployment_agent.agents.deployer import app
            return app
        if stage == "documentation":
            from agents_orchestrator.documentation_agent.agents.compiler import app
            return app
    except Exception as exc:  # noqa: BLE001 — a bad import must not crash the socket
        logger.warning("copilot: graph import failed for stage=%s: %s", stage, exc)
    return None


def _system_prompt_for(stage: str) -> Optional[str]:
    """Return the stage agent's system prompt, or None if unmapped/unavailable.

    Lazy-imported (same rationale as _graph_for): importing this module must never
    drag in every agent's heavy deps. Without this prompt the stage agent has no
    instructions and will churn without producing a useful reply."""
    try:
        if stage == "requirements":
            from agents_orchestrator.requirements_agent.agents.planning import (
                INGESTION_SYS_MESSAGE,
            )
            return INGESTION_SYS_MESSAGE
        if stage == "design":
            from agents_orchestrator.design_architecture_agent.agents.architecture import (
                DESIGN_SYS_MESSAGE,
            )
            return DESIGN_SYS_MESSAGE
        if stage == "development":
            # Fixes a latent gap: development previously got NO system prompt on the
            # Copilot path (its graph node doesn't self-inject), so the dev agent churned
            # without instructions. DEV_SYS_MESSAGE is already MCP-note-suffixed at import.
            from agents_orchestrator.development_agent.prompts.dev_agent_prompt import (
                DEV_SYS_MESSAGE,
            )
            return DEV_SYS_MESSAGE
    except Exception as exc:  # noqa: BLE001 — a bad import must not crash the socket
        logger.warning("copilot: system prompt import failed for stage=%s: %s", stage, exc)
    return None


def _base_prompt_for(stage: str) -> Optional[str]:
    """Base system prompt used as the FLOOR for agent-profile injection (design §3.4).

    MESSAGE_PROMPT_STAGES reuse _system_prompt_for (already MCP-note-suffixed at import —
    same string their first-turn SystemMessage would carry). SELF_INJECT_STAGES return the
    BARE prompt constant WITHOUT MCP_TOOLS_PROMPT_NOTE, because the graph node re-appends
    its own suffix (reviewer/scanner) or uses the base verbatim (deployer/compiler) — this
    mirrors the node's own `get_prompt_override(stage) or CONST` fallback exactly, so an
    injected override slots in at the identical point with no double-note. Fail-soft None."""
    if stage in MESSAGE_PROMPT_STAGES:
        return _system_prompt_for(stage)
    try:
        if stage == "code_review":
            from agents_orchestrator.code_review_agent.prompts.review_prompt import (
                CODE_REVIEW_SYSTEM_PROMPT,
            )
            return CODE_REVIEW_SYSTEM_PROMPT
        if stage == "security":
            from agents_orchestrator.security_agent.prompts.security_prompt import (
                SECURITY_SYSTEM_PROMPT,
            )
            return SECURITY_SYSTEM_PROMPT
        if stage == "deployment":
            from agents_orchestrator.deployment_agent.prompts.deploy_prompt import (
                DEPLOY_SYSTEM_PROMPT,
            )
            return DEPLOY_SYSTEM_PROMPT
        if stage == "documentation":
            from agents_orchestrator.documentation_agent.prompts.doc_prompt import (
                DOC_SYSTEM_PROMPT,
            )
            return DOC_SYSTEM_PROMPT
    except Exception as exc:  # noqa: BLE001 — a bad import must not crash the socket
        logger.warning("copilot: base prompt import failed for stage=%s: %s", stage, exc)
    return None


async def _workspace_for_project(tenant_id: str, project_id: Optional[str]) -> Optional[str]:
    """Resolve a project's workspace_id for agent-profile scope-chain resolution.

    Tenant-scoped read (same get_db_session_for_tenant helper _project_config uses) so RLS
    keeps it to the caller's org. Fail-soft None → the profile resolve degrades to
    org(+project) scopes only and the turn is never broken."""
    if not tenant_id or not project_id:
        return None
    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            ws = (
                await s.execute(
                    select(Project.workspace_id).where(Project.id == _as_run_uuid(project_id))
                )
            ).scalar_one_or_none()
            return str(ws) if ws else None
    except Exception as exc:  # noqa: BLE001 — workspace resolution is an enhancement
        logger.warning("copilot _workspace_for_project(project=%s) failed: %s", project_id, exc)
        return None


async def _stamp_profile_applied(run_id, tenant_id, stage, profile, injected_prompt,
                                 stamped: set) -> None:
    """Best-effort audit stamp that an agent profile shaped this turn's prompt (§3.4.4).

    Emits `agent_profile.applied` carrying the resolved prompt hash + the profile's
    version_chain (e.g. ["org:v1", "project:v3"]) so a run is reproducible against the
    exact profile versions it executed with. Only stamps when the profile actually
    contributed a layer, and dedups per (stage, prompt_hash) via `stamped`. Never raises —
    a stamping failure must never break the turn."""
    try:
        contributed = bool(
            getattr(profile, "prompt_prepend", "")
            or getattr(profile, "prompt_append", "")
            or getattr(profile, "reference_doc_summaries", None)
            or getattr(profile, "output_contract_extra", "")
        )
        if not contributed:
            return
        import hashlib
        prompt_hash = hashlib.sha256((injected_prompt or "").encode("utf-8")).hexdigest()[:12]
        key = (stage, prompt_hash)
        if key in stamped:
            return
        stamped.add(key)
        from shared.audit.models import AuditEventPayload
        from shared.audit.service import audit_service
        await audit_service.emit(AuditEventPayload(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            event_type="agent_profile.applied",
            agent_type=stage,
            resource_type="run",
            resource_id=str(run_id),
            payload={
                "prompt_hash": prompt_hash,
                "version_chain": list(getattr(profile, "version_chain", []) or []),
                "layers": {
                    "prepend": bool(getattr(profile, "prompt_prepend", "")),
                    "append": bool(getattr(profile, "prompt_append", "")),
                    "reference_docs": len(getattr(profile, "reference_doc_summaries", []) or []),
                    "output_contract_extra": bool(getattr(profile, "output_contract_extra", "")),
                },
            },
        ))
    except Exception as exc:  # noqa: BLE001 — stamping must never break a turn
        logger.warning("copilot _stamp_profile_applied(run=%s stage=%s) failed: %s",
                       run_id, stage, exc)


async def _run_model_offering(run_id: str) -> tuple[Optional[str], Optional[str]]:
    """Return (model_id, offering_id) persisted on the run, or (None, None) on any miss.

    Superuser session — this is a run-keyed system read, not a tenant request path.
    Fail-soft: the graph's model resolution falls back to the org default on None."""
    try:
        async with get_db_session_superuser() as s:
            run = (
                await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
            ).scalar_one_or_none()
            if run is None:
                return None, None
            return getattr(run, "model_id", None), getattr(run, "offering_id", None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _run_model_offering(%s) failed: %s", run_id, exc)
        return None, None


def _as_run_uuid(run_id: str):
    try:
        return uuid.UUID(str(run_id))
    except (ValueError, TypeError, AttributeError):
        return run_id


async def _active_stage(run_id: str) -> tuple[str, Optional[str]]:
    """Return (active_stage, project_id) for the run.

    Reads runs.current_stage (falls back to runs.stage, then "requirements"). Superuser
    session — this is a run-keyed system read, not a tenant request path. Never raises:
    on any miss/DB error returns ("requirements", None) so the socket still functions."""
    try:
        async with get_db_session_superuser() as s:
            run = (
                await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
            ).scalar_one_or_none()
            if run is None:
                return "requirements", None
            stage = run.current_stage or run.stage or "requirements"
            project_id = str(run.project_id) if run.project_id else None
            return stage, project_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _active_stage(%s) failed: %s", run_id, exc)
        return "requirements", None


async def _resolve_perms(user_id: str, tenant_id: str) -> list[str]:
    """Resolve the caller's effective permission list from roles+tenant.

    Reuses the canonical DB resolver (the same one login bakes into the JWT). Ws-ticket
    claims carry NO permissions, so we resolve them here. Fail-closed to [] on any error
    so a resolver blip degrades to "cannot approve" rather than crashing the socket."""
    try:
        from shared.authz.resolver import resolve_permissions_for_user
        return await resolve_permissions_for_user(user_id, tenant_id)
    except Exception as exc:  # noqa: BLE001 — incl. PermissionResolutionError
        logger.warning("copilot _resolve_perms(user=%s) failed: %s", user_id, exc)
        return []


async def _project_config(tenant_id: str, project_id: Optional[str]) -> tuple[dict, dict]:
    """Load the project's per-stage connectors + mcp_servers maps ({agent_id: [...]}).

    Tenant-scoped read (get_db_session_for_tenant) so RLS keeps it to the caller's org.
    Fail-soft: any miss/DB error → ({}, {}) so stage_connector_kind falls back to its
    default and mcp_tools_for_stage becomes a no-op — the socket must never crash on this."""
    if not tenant_id or not project_id:
        return {}, {}
    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            project = (
                await s.execute(select(Project).where(Project.id == _as_run_uuid(project_id)))
            ).scalar_one_or_none()
            if project is None:
                return {}, {}
            return (project.connectors or {}), (project.mcp_servers or {})
    except Exception as exc:  # noqa: BLE001 — project config is an enhancement, never fatal
        logger.warning("copilot _project_config(project=%s) failed: %s", project_id, exc)
        return {}, {}


def _shim_input(run_id: str, tenant_id: str, project_id: Optional[str],
                connectors: Optional[dict] = None,
                mcp_servers: Optional[dict] = None) -> SimpleNamespace:
    """Minimal input the Bridge + stage helpers read.

    run_id + tenant_id are load-bearing for the Bridge; connectors/mcp_servers are the
    project's per-stage maps that stage_connector_kind / mcp_tools_for_stage consume."""
    return SimpleNamespace(
        run_id=run_id,
        tenant_id=tenant_id or None,
        project_id=project_id,
        model_id=None,
        offering_id=None,
        connectors=connectors or {},
        mcp_servers=mcp_servers or {},
    )


# Stages whose agent tools operate on a cloned working tree — the Bridge must prep
# a repo workspace for these; requirements/design never touch a repo.
_REPO_STAGES: set[str] = {
    "development", "code_review", "security", "testing", "deployment", "documentation",
}


def _stage_needs_repo(stage: str) -> bool:
    """True when *stage*'s agent needs a cloned repo workspace (the downstream,
    repo-touching stages); False for requirements/design."""
    return stage in _REPO_STAGES


def _downstream_repo_ref(dev_artifacts: Optional[dict], pat: Optional[str]) -> Optional[dict]:
    """Build the Bridge's `repo_ref` kwarg from the run's `development_artifacts`.

    Pure/no-IO: the caller resolves dev_artifacts (DB read) and pat (connector-cred
    read) beforehand. Returns None when there's nothing to clone — falsy dev_artifacts
    or no repo_url — so the caller falls back to the current no-repo pipeline_session()
    call. `base` prefers a commit sha (base_sha) but falls back to the target branch
    name so the Bridge's diff-against-base still has something to fetch."""
    if not dev_artifacts:
        return None
    repo_url = dev_artifacts.get("repo_url")
    if not repo_url:
        return None
    return {
        "repo_url": repo_url,
        "ref": dev_artifacts.get("branch_name"),
        # `base_sha`/`target_branch` are rarely populated on `development_artifacts`
        # today; without a fallback, `base` is None and `prepare_run_workspace` never
        # computes `diff_text`/`changed_files` (nothing to diff against). Default to
        # "main" — `prepare_run_workspace` diffs `origin/{base}...HEAD`, and
        # `origin/main` exists on any repo cloned from the default remote.
        "base": dev_artifacts.get("base_sha") or dev_artifacts.get("target_branch") or "main",
        "pat": pat,
    }


async def _run_development_artifacts(run_id: str) -> dict:
    """Return `runs.development_artifacts` for *run_id*, or {} on any miss/DB error.

    Superuser session — this is a run-keyed system read, not a tenant request path.
    Fail-soft: {} makes _downstream_repo_ref return None, so the turn proceeds with
    needs_repo=False rather than crashing."""
    try:
        async with get_db_session_superuser() as s:
            run = (
                await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
            ).scalar_one_or_none()
            if run is None:
                return {}
            return getattr(run, "development_artifacts", None) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _run_development_artifacts(%s) failed: %s", run_id, exc)
        return {}


async def _repo_ref_for_stage(stage: str, run_id: str, tenant_id: str) -> Optional[dict]:
    """Resolve the Bridge's `repo_ref` for a downstream turn: read the run's
    development_artifacts, resolve the ADO PAT via the same connector-cred path
    `_stage_connector` uses (get_connector_for_session, via ado_repos.resolve_auth),
    and build the ref. Fail-soft: any error → None (turn proceeds with needs_repo=False,
    never crashes on repo-ref prep)."""
    if not _stage_needs_repo(stage):
        return None
    try:
        dev_artifacts = await _run_development_artifacts(run_id)
        if not dev_artifacts or not dev_artifacts.get("repo_url"):
            # The run row may have no captured dev artifacts yet — the dev agent
            # submits them in-session, but they're only persisted onto the run on a
            # HANDOFF that advances the pipeline. When a downstream stage is reached
            # WITHOUT that advance (a rail click or a conversational "run documentation"
            # switch), pull them from the dev session on demand so the stage still
            # inherits the cloned repo/branch instead of seeing "no repo checked out".
            dev_artifacts = await _capture_development_artifacts(run_id, tenant_id) or dev_artifacts
        if not dev_artifacts or not dev_artifacts.get("repo_url"):
            return None
        from shared.services import ado_repos
        _org, pat = await ado_repos.resolve_auth(tenant_id or "")
        return _downstream_repo_ref(dev_artifacts, pat)
    except Exception as exc:  # noqa: BLE001 — repo-ref prep is best-effort
        logger.warning("copilot _repo_ref_for_stage(%s/%s) failed: %s", stage, run_id, exc)
        return None


def _seed_downstream_prepared(stage: str, tenant_id: str, project_id: str, run_id: str, ps: Any) -> None:
    """Seed a downstream agent's workspace from the Bridge's shared run clone
    (`ps.work_dir` / `ps._workspace`), so Code Review / Security / Deployment /
    Documentation inherit the cloned repo (+ diff, for Code Review) WITHOUT the
    standalone `/prepare` REST call the Copilot never makes.

    Two stores are seeded, because the two call paths read from different places:

    1. `set_prepared(tenant_id, project_id, ...)` — the (tenant, project)-keyed store.
       This is what each agent's STANDALONE `*_api.py` WS handler binds into its own
       session on the FIRST message (`for k, v in prepared.items(): setattr(s, k, v)`).
       Harmless to keep populating — it helps a standalone run of the same agent that
       happens to read this store — but the Copilot's graph tools never call that
       binding code, so by itself it never reaches the tools.
    2. The agent's own SESSION object, keyed by `run_id` — this is what the Copilot
       graph's tools actually read, via `get_session(get_session_id())` where
       `get_session_id()` resolves to the pipeline `run_id`. Without this, the
       seeded work_dir never reaches the tools and the agent operates on an empty
       session (no repo, no diff) even though the Bridge cloned one.

    Fail-soft per agent: a missing/failing agent module must never block seeding
    the others or the turn.
    """
    work_dir = getattr(ps, "work_dir", None)
    if not work_dir:
        return
    workspace = getattr(ps, "_workspace", None)
    diff_text = getattr(workspace, "diff_text", None) or ""
    changed_files = getattr(workspace, "changed_files", None) or []

    if stage == "code_review":
        try:
            from agents_orchestrator.code_review_agent.config.session_state import (
                get_session as _get_review_session,
                set_prepared as _set_review_prepared,
            )
            data = {"work_dir": work_dir, "diff_text": diff_text, "changed_files": changed_files}
            _set_review_prepared(tenant_id, project_id, data)
            s = _get_review_session(run_id)
            s.work_dir = work_dir
            s.diff_text = diff_text
            s.changed_files = changed_files
            if project_id:
                s.project_id = project_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("copilot _seed_downstream_prepared(code_review) failed: %s", exc)
    elif stage == "security":
        try:
            from agents_orchestrator.security_agent.config.session_state import (
                get_session as _get_security_session,
                set_prepared as _set_security_prepared,
            )
            _set_security_prepared(tenant_id, project_id, {"work_dir": work_dir})
            s = _get_security_session(run_id)
            s.work_dir = work_dir
            if project_id:
                s.project_id = project_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("copilot _seed_downstream_prepared(security) failed: %s", exc)
    elif stage == "deployment":
        try:
            from agents_orchestrator.deployment_agent.config.session_state import (
                get_session as _get_deploy_session,
                set_prepared as _set_deploy_prepared,
            )
            _set_deploy_prepared(tenant_id, project_id, {"work_dir": work_dir})
            s = _get_deploy_session(run_id)
            s.work_dir = work_dir
            if project_id:
                s.project_id = project_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("copilot _seed_downstream_prepared(deployment) failed: %s", exc)
    elif stage == "documentation":
        try:
            from agents_orchestrator.documentation_agent.config.session_state import (
                get_session as _get_docs_session,
                set_prepared as _set_docs_prepared,
            )
            _set_docs_prepared(tenant_id, project_id, {"work_dir": work_dir})
            s = _get_docs_session(run_id)
            s.work_dir = work_dir
            if project_id:
                s.project_id = project_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("copilot _seed_downstream_prepared(documentation) failed: %s", exc)


def _answer_to_text(msg: dict) -> str:
    """Render a choice_answer into a plain-text instruction the agent can act on.

    The card ids ARE the provider ids (project name / story id), so echoing the
    selection as text is enough for the agent to continue — no special resume path."""
    ids = msg.get("selected_ids") or []
    free = (msg.get("free_text") or "").strip()
    parts = []
    if ids:
        parts.append("I select: " + ", ".join(str(i) for i in ids))
    if free:
        parts.append(free)
    return ". ".join(parts) or "(no selection)"


def _extract_text(content: Any) -> str:
    """Handle both str and list[block] content shapes from langchain_anthropic."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _extract_thinking(content: Any) -> str:
    """Pull extended-thinking / reasoning deltas out of a chunk's content blocks.

    langchain_anthropic surfaces reasoning as blocks shaped {type: "thinking",
    thinking: "..."} (or "reasoning"). Plain str content never carries thinking."""
    if isinstance(content, list):
        return "".join(
            (b.get("thinking") or b.get("reasoning") or "")
            for b in content
            if isinstance(b, dict) and b.get("type") in ("thinking", "reasoning")
        )
    return ""


async def _send(websocket: WebSocket, payload: dict) -> None:
    """Best-effort JSON send — a dead socket must not raise into the turn loop."""
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:  # noqa: BLE001
        raise WebSocketDisconnect()


@asynccontextmanager
async def _stage_connector(shim_input: Any, stage: str, tenant_id: str):
    """Inject the project's bound board connector for the agent's own tools.

    The agent's board tools (list_board_projects, …) read the connector from the
    config.connectors.context contextvar via get_connector(); nothing else in the
    Copilot path sets it, so without this the agent reports "no board connected"
    even when a connector IS selected for the stage. Resolve the kind from the
    project's per-stage selection (stage_connector_kind → azure_devops/jira/…),
    resolve the credentialed connector, and set it for the turn. Fail-soft: any
    miss leaves the contextvar unset and the board tools fail closed with a clear
    message — the turn still proceeds on pasted/uploaded input. clear_connector in
    finally keeps credentials from surviving the turn (REQ-M3-10)."""
    injected = False
    try:
        if tenant_id:
            from config.connectors.context import set_connector
            from config.connector_factory import get_connector_for_session
            from workflows.activities._base import stage_connector_kind

            kind = stage_connector_kind(shim_input, stage)
            # project_id binds the connector to this project's effective access
            # (unit grant ∩ project narrowing). Without it the connector permits
            # nothing, which is the correct answer for a turn we cannot scope.
            connector = await get_connector_for_session(
                kind=kind, tenant_id=tenant_id,
                project_id=str(getattr(shim_input, "project_id", "") or ""),
                # `stage` here IS the agent id (see _REPO_STAGES above), and the
                # stage IS the access decision since migration 0024 — the level lives
                # per (stage, tool), so project_id alone resolves to no access.
                agent_id=stage,
            )
            set_connector(connector)
            injected = True
    except Exception as exc:  # noqa: BLE001 — resolution failure ≠ dead turn
        logger.warning("copilot _stage_connector(%s/%s) failed: %s", stage, tenant_id, exc)
    try:
        yield injected
    finally:
        if injected:
            try:
                from config.connectors.context import clear_connector
                clear_connector()
            except Exception:  # noqa: BLE001
                pass


async def _upstream_context(stage: str, run_id: str) -> str:
    """Format the artifacts of all PRIOR stages (present on the run) as a context block.

    This is how a downstream agent receives its predecessors' work: the Design agent's
    prompt expects "a requirements payload injected from the Requirements Agent" — it
    does not fetch it. We read the run's upstream artifact columns and prepend them to
    the stage's FIRST message. Empty for requirements (no upstream)."""
    from shared.services.orchestrator import progression
    from config.agent_registry import AGENT_REGISTRY
    try:
        idx = progression.STAGE_ORDER.index(stage)
    except ValueError:
        return ""
    cols = []
    for prior in progression.STAGE_ORDER[:idx]:
        defn = AGENT_REGISTRY.get(prior)
        col = getattr(defn, "output_artifact", None) if defn else None
        if col:
            cols.append((prior, col))
    if not cols:
        return ""
    try:
        async with get_db_session_superuser() as s:
            run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
            if run is None:
                return ""
            parts = []
            for prior, col in cols:
                val = getattr(run, col, None)
                if val:
                    parts.append(
                        f"### {prior.replace('_', ' ').title()} artifact\n"
                        f"```json\n{json.dumps(val, indent=2)[:8000]}\n```"
                    )
    except Exception as exc:  # noqa: BLE001 — context injection is best-effort
        logger.warning("copilot _upstream_context(%s/%s) failed: %s", stage, run_id, exc)
        return ""
    if not parts:
        return ""
    return (
        "--- PIPELINE CONTEXT (upstream artifacts from prior stages — use these as the "
        "source of requirements; do not ask the user to re-supply them) ---\n\n"
        + "\n\n".join(parts)
        + "\n\n--- END PIPELINE CONTEXT ---\n\n"
    )


async def _stream_active(graph, text: str, run_id: str, tenant_id: str,
                         websocket: WebSocket, stage: str,
                         model_id: Optional[str] = None,
                         offering_id: Optional[str] = None,
                         on_tool_files=None,
                         system_prompt_override: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Stream the active graph's tokens to the socket. Returns assembled agent text.

    Modeled on requirements_agent_api._stream_agent_response: astream(stream_mode=
    "messages"), skip tool-call chunks, emit stream_chunk per text delta then a single
    stream_end. The graph's checkpointer (thread_id=run_id) APPENDS messages via
    add_messages, so the stage's system prompt is prepended only on the first turn of
    the thread — every subsequent turn passes just the new HumanMessage, matching the
    design resume path. Without the system prompt + model_id on turn one, the stage
    agent has no instructions and churns without responding."""
    if graph is None:
        await _send(websocket, {
            "type": "error", "run_id": run_id,
            "message": "This stage has no interactive agent yet.",
        })
        return ""

    is_first_turn = True
    try:
        st = await graph.aget_state({"configurable": {"thread_id": run_id}})
        existing_messages = (getattr(st, "values", None) or {}).get("messages") or []
        is_first_turn = not existing_messages
    except Exception as exc:  # noqa: BLE001 — default to "first turn" fail-soft
        logger.warning("copilot _stream_active first-turn check failed (run=%s): %s", run_id, exc)

    if is_first_turn:
        # Agent-profile-injected prompt (design §3.4) wins over the baked constant for
        # MESSAGE_PROMPT_STAGES; falls back to the baked prompt when no override was passed.
        system_prompt = system_prompt_override or _system_prompt_for(stage)
        # Inject prior stages' artifacts so a downstream agent (e.g. Design) receives the
        # Requirements payload on turn one instead of asking the user to re-supply it.
        upstream = await _upstream_context(stage, run_id)
        human = f"{upstream}{text}" if upstream else text
        messages = (
            [SystemMessage(content=system_prompt), HumanMessage(content=human)]
            if system_prompt else [HumanMessage(content=human)]
        )
    else:
        messages = [HumanMessage(content=text)]

    state = {
        "messages": messages, "tenant_id": tenant_id,
        "model_id": model_id, "offering_id": offering_id,
    }
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}
    final_content = ""
    streaming_started = False
    emitted_tools: set[str] = set()
    # HANDOFF:: is a machine sentinel the orchestrator consumes — never show it to the
    # user. Buffer a small tail so a marker split across chunks is still caught, and stop
    # streaming once it appears (the sentinel is always the tail of the reply).
    _MARK = "HANDOFF::"
    hold = ""
    handoff_seen = False

    # Artifact routing: for doc-producing stages (Design) AND report stages (Code Review /
    # Security / Testing / Deployment / Documentation), the substantive output streams into
    # the Artifacts PANEL (artifact.* events), not the chat. We buffer a little until we can
    # tell a document/report (design headers, or just enough length) from a short
    # conversational reply, then route accordingly. art_decided: None → undecided,
    # "artifact" → panel, "chat" → chat.
    _is_doc_stage = stage in ARTIFACT_STAGES
    _is_report_stage = stage in _REPORT_STAGES
    art_decided: Optional[str] = None if (_is_doc_stage or _is_report_stage) else "chat"
    art_id = f"{stage}-report" if _is_report_stage else f"{stage}-{run_id}"
    art_title = (f"{stage.replace('_', ' ').title()} Report" if _is_report_stage
                 else "Design Document")
    art_md = ""          # full document text (for panel + persistence)
    art_probe = ""       # pre-decision buffer
    # The Design agent produces its document INSIDE a tool (generate_architecture_from_
    # context) and only summarizes in chat, so the doc arrives as a ToolMessage, not AI
    # text. When that happens we take the tool output as the artifact and suppress the
    # AI summary (the compact chat card replaces it).
    art_from_tool = False

    async def _to_chat(safe: str) -> None:
        nonlocal streaming_started, final_content
        streaming_started = True
        final_content += safe
        await _send(websocket, {"type": "stream_chunk", "content": safe, "run_id": run_id})

    async def _route(safe: str) -> None:
        nonlocal art_decided, art_md, art_probe
        if not safe:
            return
        # Doc came from a tool → the AI text is just a summary; suppress it (the
        # compact chat card stands in for it).
        if art_from_tool:
            return
        if art_decided == "chat":
            await _to_chat(safe)
            return
        if art_decided == "artifact":
            art_md += safe
            await _send(websocket, {"type": "artifact.delta", "run_id": run_id,
                                    "artifact_id": art_id, "content": safe})
            return
        # Undecided (doc/report stage): buffer until we recognise a document/report or
        # give up.
        art_probe += safe
        if _is_doc_stage and looks_like_design_doc(art_probe):
            art_decided = "artifact"
            art_md = art_probe
            await _send(websocket, {"type": "artifact.open", "run_id": run_id,
                                    "stage": stage, "artifact_id": art_id,
                                    "kind": "markdown", "title": art_title})
            await _send(websocket, {"type": "artifact.delta", "run_id": run_id,
                                    "artifact_id": art_id, "content": art_probe})
            art_probe = ""
        elif len(art_probe) > _REPORT_MIN_LEN:
            if _is_report_stage:
                # Report stages have no fixed header signature — once the reply is long
                # enough it's the substantive report, not a short clarifying question.
                art_decided = "artifact"
                art_md = art_probe
                await _send(websocket, {"type": "artifact.open", "run_id": run_id,
                                        "stage": stage, "artifact_id": art_id,
                                        "kind": "markdown", "title": art_title})
                await _send(websocket, {"type": "artifact.delta", "run_id": run_id,
                                        "artifact_id": art_id, "content": art_probe})
            else:
                # No document signature in the opening — treat as a normal chat reply.
                art_decided = "chat"
                await _to_chat(art_probe)
            art_probe = ""

    async def _emit(text_delta: str) -> None:
        nonlocal hold, handoff_seen
        if handoff_seen:
            return
        hold += text_delta
        idx = hold.find(_MARK)
        if idx != -1:
            handoff_seen = True
            safe = hold[:idx]
            hold = ""
        else:
            # keep the last (len-1) chars back in case the marker is split mid-stream
            keep = len(_MARK) - 1
            safe = hold[:-keep] if len(hold) > keep else ""
            hold = hold[len(safe):]
        if safe:
            await _route(safe)
    cancelled = False
    try:
        async for chunk in graph.astream(state, stream_mode="messages", config=config):
            # Cooperative Stop: the user hit Stop (REST flipped the flag). End the
            # turn now — flush what streamed so far below, then stream_end.
            if _consume_cancel(run_id):
                cancelled = True
                break
            msg_chunk = chunk[0] if isinstance(chunk, tuple) else chunk

            # Tool RESULT arrives as a ToolMessage → the invocation just finished.
            if isinstance(msg_chunk, ToolMessage):
                tname = getattr(msg_chunk, "name", None)
                if tname:
                    await _send(websocket, {
                        "type": "tool.call", "run_id": run_id,
                        "name": tname, "status": "done",
                    })
                    # Incremental artifact surfacing: the moment a file-producing tool
                    # finishes (SBOM, findings, staged deploy files), push the "Generated
                    # files" tree to the panel — don't wait for the whole (possibly long)
                    # turn to end. Fail-soft; the callback swallows its own errors.
                    if on_tool_files is not None and tname in _ARTIFACT_TOOLS:
                        try:
                            await on_tool_files()
                        except WebSocketDisconnect:
                            raise
                        except Exception:  # noqa: BLE001
                            pass
                # Doc stage: if a tool returned a full design document, that IS the
                # artifact — stream it into the panel (arrives whole, not token-by-token).
                if _is_doc_stage and not art_from_tool:
                    tcontent = _extract_text(getattr(msg_chunk, "content", None)) \
                        if not isinstance(getattr(msg_chunk, "content", None), str) \
                        else msg_chunk.content
                    if tcontent and looks_like_design_doc(tcontent):
                        art_from_tool = True
                        art_decided = "artifact"
                        art_md = tcontent
                        await _send(websocket, {"type": "artifact.open", "run_id": run_id,
                                                "stage": stage, "artifact_id": art_id,
                                                "kind": "markdown", "title": "Design Document"})
                        await _send(websocket, {"type": "artifact.delta", "run_id": run_id,
                                                "artifact_id": art_id, "content": tcontent})
                continue

            # Tool INVOCATION streams as tool_call_chunks; the name lands on the first
            # chunk of each call. Dedupe by name so we announce each tool once.
            for tcc in getattr(msg_chunk, "tool_call_chunks", None) or []:
                tname = tcc.get("name") if isinstance(tcc, dict) else getattr(tcc, "name", None)
                if tname and tname not in emitted_tools:
                    emitted_tools.add(tname)
                    await _send(websocket, {
                        "type": "tool.call", "run_id": run_id,
                        "name": tname, "status": "running",
                    })

            raw = getattr(msg_chunk, "content", None)
            if not raw:
                continue

            thinking = _extract_thinking(raw)
            if thinking:
                await _send(websocket, {
                    "type": "agent.thinking", "run_id": run_id, "delta": thinking,
                })

            content = _extract_text(raw)
            if content and not getattr(msg_chunk, "tool_calls", None):
                await _emit(content)
        # Flush any held tail (unless it was the start of a marker we suppressed).
        if hold and not handoff_seen:
            await _route(hold)
            hold = ""
        # Undecided at end → the whole reply was short conversational text → chat.
        if art_decided is None and art_probe:
            await _to_chat(art_probe)
            art_probe = ""
        # Finalize a streamed artifact so the panel stops its streaming state.
        if art_decided == "artifact":
            await _send(websocket, {"type": "artifact.end", "run_id": run_id,
                                    "artifact_id": art_id})
        # Cancelled turn: acknowledge in the thread so Stop is never silent, then
        # ensure the stream closes even if no text had started.
        if cancelled:
            notice = "\n\n_Stopped._" if streaming_started else "_Stopped before the agent replied._"
            await _to_chat(notice)
        if streaming_started:
            await _send(websocket, {"type": "stream_end", "run_id": run_id})
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("copilot streaming error (run=%s): %s", run_id, exc)
        await _send(websocket, {
            "type": "error", "run_id": run_id, "message": "Streaming failed for this turn.",
        })
    return final_content, (art_md if art_decided == "artifact" else None)


async def _stream_state_machine(graph, text: str, run_id: str, tenant_id: str,
                                websocket: WebSocket, stage: str,
                                model_id: Optional[str] = None,
                                offering_id: Optional[str] = None,
                                work_dir: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Adapter for fixed-pipeline (non-message) stage agents (e.g. testing).

    These graphs read `state["user_prompt"]` and write `state["final_user_message"]`
    instead of consuming/producing `messages`. The Copilot's message-based streaming
    (`_stream_active`) does not fit them, so we invoke the graph with a user_prompt state
    and surface its final message as chat. Multi-turn state (pending approvals, target_url,
    selected_test_types) persists via the MemorySaver checkpointer keyed on thread_id=run_id
    — the graph re-enters its entry node each turn with the checkpointed state, which is how
    its stop-graph/resume HITL works. Test runs are long and blocking, so we emit a heartbeat
    before invoking. Returns (reply_text, artifact_md): artifact_md is set (== reply_text)
    when the reply is a substantial report, so the turn loop captures it via
    _capture_stage_report instead of streaming the full report into chat."""
    if graph is None:
        await _send(websocket, {"type": "error", "run_id": run_id,
                                "message": "This stage has no interactive agent yet."})
        return "", None

    state = {
        "user_prompt": text,
        "tenant_id": tenant_id,
        "model_id": model_id,
        "offering_id": offering_id,
        # Reset per-turn fields every turn (mirrors _initial_state's resumed-state
        # resets in testing_agent.py). SuperAgentState is a plain TypedDict with NO
        # reducers, so any key omitted here would retain its CHECKPOINTED value from
        # the prior turn — e.g. a stale final_user_message would short-circuit
        # generate_final_message's early-return and mask this turn's real summary.
        "final_user_message": None,
        "classified_intent": "unsupported",
        "error_message": None,
        "final_outputs": {},
    }
    # Task 5 — an already-cloned Bridge workspace (ps.work_dir) wins over the testing
    # graph's own clone_target/upstream_development fallbacks (setup_workspace checks
    # this key first). Only set when present so a turn with no repo_ref (e.g. an
    # upload-based test) doesn't clobber the checkpointed work_dir from an earlier turn.
    if work_dir:
        state["work_dir"] = work_dir
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 100}

    # Heartbeat: the invoke below can run for minutes (clone → generate → execute).
    await _send(websocket, {"type": "tool.call", "run_id": run_id,
                            "name": f"{stage}_running", "status": "running"})
    try:
        # Resolve + stash the run's BYOK model before invoking (mirrors
        # run_super_agent_async's resolve-before-invoke in testing_agent.py). The
        # testing graph's LLM sites read the resolved model from this contextvar;
        # in enterprise mode build_llm() raises if it's unset. Same-task await (no
        # executor hop here) means the contextvar set below is visible to ainvoke
        # without needing copy_context() plumbing.
        from agents_orchestrator.testing_agent.agents.testing_agent import (
            _resolve_and_stash_model,
        )
        from shared.services.model_resolver import (
            NoModelConfiguredError, ModelNotEnabledError,
        )
        await _resolve_and_stash_model(tenant_id, model_id, offering_id)
        final_state = await graph.ainvoke(state, config=config)
    except WebSocketDisconnect:
        raise
    except (NoModelConfiguredError, ModelNotEnabledError) as exc:
        # Mirror run_super_agent_async's fail-closed friendly message (same exception
        # types, same copy) instead of falling into the generic error below — this is
        # an expected enterprise-mode configuration state, not an agent/graph failure,
        # so it's surfaced as a normal chat reply (persisted + shown) not an error event.
        logger.warning("copilot state-machine model resolution failed (run=%s stage=%s): %s",
                       run_id, stage, type(exc).__name__)
        msg = (
            "No usable model is configured for your organization. An administrator "
            "must add and verify a model provider in Org Settings → Model Providers."
        )
        await _send(websocket, {"type": "stream_chunk", "content": msg, "run_id": run_id})
        await _send(websocket, {"type": "stream_end", "run_id": run_id})
        return msg, None
    except Exception as exc:  # noqa: BLE001
        logger.error("copilot state-machine invoke error (run=%s stage=%s): %s",
                     run_id, stage, exc)
        await _send(websocket, {"type": "error", "run_id": run_id,
                                "message": "The testing agent could not process this turn."})
        return "", None
    finally:
        await _send(websocket, {"type": "tool.call", "run_id": run_id,
                                "name": f"{stage}_running", "status": "done"})

    reply = (final_state or {}).get("final_user_message") or ""
    if not reply:
        return reply, None
    # A substantial reply on a report stage (e.g. testing results) is the panel report,
    # not a chat dump — same threshold/route as _stream_active's report-stage path. The
    # turn loop persists it via _capture_stage_report and sends the compact card instead
    # of streaming this text to chat. Short replies (clarifying questions, HITL prompts)
    # still go straight to chat since they arrive whole (no token stream to buffer).
    if stage in _REPORT_STAGES and len(reply.strip()) > _REPORT_MIN_LEN:
        return reply, reply
    await _send(websocket, {"type": "stream_chunk", "content": reply, "run_id": run_id})
    await _send(websocket, {"type": "stream_end", "run_id": run_id})
    return reply, None


async def _last_list_tool(graph, run_id: str) -> Optional[str]:
    """Inspect the checkpoint for the agent's most recent list-tool call name.

    Scans the CURRENT agent turn (newest-first, back to the previous HumanMessage):
    the most recent board list-tool call in that turn is what the user should now pick
    from. Returns None if the turn had no list-tool (→ no card; agent asked plainly).

    Must scan the whole turn, not just the last message: the agent typically calls
    list_board_projects and THEN summarizes in a tool-less AIMessage, so the tool call
    sits one message back. Stopping at the first AIMessage (the summary) missed it and
    suppressed the choice card."""
    try:
        state = await graph.aget_state({"configurable": {"thread_id": run_id}})
        messages = (getattr(state, "values", {}) or {}).get("messages", []) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _last_list_tool(%s) failed: %s", run_id, exc)
        return None
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break  # start of the current agent turn — don't look into prior turns
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name in _PROJECT_LIST_TOOLS or name in _STORY_LIST_TOOLS:
                return name
    return None


# committed = the user has chosen a concrete testing path; non-committal intents
# (greeting / follow-up / unsupported) must NOT suppress the type card.
_TESTING_COMMITTED_INTENTS = {
    "full_test", "single_file_test", "ui_test", "api_ui_only",
    "generate_plan_only", "trigger_pipeline_and_collect",
}


def _testing_type_uncommitted(vals: dict) -> bool:
    """True when no concrete testing type is chosen yet -> show the type card."""
    if vals.get("selected_test_types"):
        return False
    if vals.get("classified_intent") in _TESTING_COMMITTED_INTENTS:
        return False
    return True


def _testing_card_kind(vals: dict) -> Optional[str]:
    """Which testing card to show from checkpoint state: 'url' when awaiting a
    functional/api URL, 'type' when no concrete testing type is chosen yet, else None."""
    if vals.get("awaiting_scope") and (vals.get("selected_test_types")
                                       or vals.get("ui_scope") or vals.get("api_scope")):
        return "url"
    if _testing_type_uncommitted(vals):
        return "type"
    return None


async def _maybe_emit_choice_card(stage: str, graph, run_id: str, tenant_id: str,
                                  project_id: Optional[str], shim_input: Any,
                                  websocket: WebSocket, reply: str = "") -> None:
    """Emit a clickable ChoiceCard for a question the agent asked this turn.

    Two layers: (1) the STAGE-SPECIFIC cards (testing state-machine prompts, requirements
    board project/story pickers), re-sourced from the provider; (2) a GENERIC fallback —
    if any agent's plain-text reply enumerates numbered options ("1. …  2. …  reply with
    the number"), turn it into a clickable single-select card so the user clicks instead
    of typing the number. Best-effort — a failure just leaves the agent's text prompt."""
    if await _emit_specific_choice_card(
            stage, graph, run_id, tenant_id, project_id, shim_input, websocket, reply):
        return
    try:
        await _emit_numbered_choice_card(reply, stage, run_id, websocket)
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — generic card is optional
        logger.info("copilot generic choice-card skipped (run=%s): %s", run_id, exc)


async def _emit_specific_choice_card(stage: str, graph, run_id: str, tenant_id: str,
                                     project_id: Optional[str], shim_input: Any,
                                     websocket: WebSocket, reply: str = "") -> bool:
    """Emit a stage-specific ChoiceCard (testing/requirements). Returns True when the
    stage's card path handled this turn (emitted a card OR is fully owned by the state
    machine) so the generic numbered-option fallback should NOT also fire; False when
    nothing specific applies and the generic detector may run.

    Best-effort ENHANCEMENT: structured data is re-sourced from the PROVIDER (the @tool
    functions return human-readable strings, not JSON) via the project's BOUND connector."""
    if stage == "testing":
        try:
            st = await graph.aget_state({"configurable": {"thread_id": run_id}})
            vals = (getattr(st, "values", None) or {})
            kind = _testing_card_kind(vals)
            if kind is None:
                return True  # testing cards are state-machine owned; skip generic
            from agents_orchestrator.orchestrator import copilot_cards
            if kind == "url":
                _sel = vals.get("selected_test_types") or []
                scope = "functional" if ("functional" in _sel or vals.get("ui_scope")) else "api"
                card = copilot_cards.testing_url_card(run_id, scope)
            else:
                card = copilot_cards.testing_type_card(run_id)
            await _send(websocket, {"type": "choice.card", "run_id": run_id,
                                    "card": card.model_dump()})
        except WebSocketDisconnect:
            raise
        except Exception as exc:  # noqa: BLE001 — card is optional
            logger.info("copilot testing card skipped (run=%s): %s", run_id, exc)
        return True
    if stage != "requirements":
        return False
    # The agent has already acted on a selection this run — either it packaged the
    # requirements payload (persisted on the run row) or this turn just handed off
    # (about to be persisted by _advance_or_gate). Either way there's nothing left to
    # pick, so re-emitting "Select stories"/"Select project" here would be a stale,
    # confusing card the user has already resolved.
    try:
        async with get_db_session_superuser() as s:
            run = (
                await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
            ).scalar_one_or_none()
            if run is not None and getattr(run, "requirements_payload", None):
                return True
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — best-effort guard; fall through on error
        logger.info("copilot requirements-payload check skipped (run=%s): %s", run_id, exc)
    if await _detect_handoff(graph, run_id) is not None:
        return True
    tool_name = await _last_list_tool(graph, run_id)
    if not tool_name:
        return False  # requirements asked a non-board question → let the generic try
    # A story-list tool runs for many reasons that are NOT "pick a story" — e.g. a
    # dedup check before create, or confirming the board. Only surface the story
    # picker when the agent's reply actually asks the user to choose; otherwise the
    # card would hijack the composer mid-flow (e.g. right after creating an item,
    # when the agent is asking to confirm an assignee). Project listing is always a
    # "which project?" pick, so it is not gated.
    if tool_name in _STORY_LIST_TOOLS and not _reply_asks_to_select(reply):
        return False
    try:
        from config.connectors.context import set_connector, clear_connector
        from config.connector_factory import get_connector_for_session
        from agents_orchestrator.orchestrator import copilot_cards
        from workflows.activities._base import stage_connector_kind

        if not tenant_id:
            return False
        kind = stage_connector_kind(shim_input, stage)
        connector = await get_connector_for_session(
            kind=kind, tenant_id=tenant_id,
            project_id=str(getattr(shim_input, "project_id", "") or ""),
            agent_id=stage,
        )
        set_connector(connector)
        try:
            if tool_name in _PROJECT_LIST_TOOLS:
                projects = await connector.read_adapter("list_projects")
                card = copilot_cards.project_choice_card(run_id, projects or [])
            else:
                proj = await _resolve_project_for_stories(graph, run_id, project_id)
                if not proj:
                    return False
                items = await connector.read_adapter("list_all_items", project=proj, team=None)
                card = copilot_cards.story_choice_card(run_id, items or [])
        finally:
            clear_connector()
        if not card.options:
            return False
        await _send(websocket, {"type": "choice.card", "run_id": run_id,
                                "card": card.model_dump()})
        return True
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — card is optional; never fail the turn
        logger.info("copilot choice-card sourcing skipped (run=%s): %s", run_id, exc)
        return False


# A line that is a numbered option: "1. Foo", "2) Bar", "3 - Baz" (1-2 digit index).
_NUM_OPT_RE = re.compile(r"^\s*(\d{1,2})\s*[.)\-:]\s+(.+?)\s*$")

# The agent is genuinely asking the user to PICK one of the numbered items.
_SELECT_CUES = ("reply with", "which ", "select ", "pick ", "choose ",
                "enter the number", "type the number", "let me know which",
                "specify by", "which one", "option would you")


def _reply_asks_to_select(reply: str) -> bool:
    """True when the agent's reply is genuinely asking the user to pick from a list.

    Gates the requirements STORY picker: a story-list tool fires for many reasons
    that are not "choose a story" (a dedup check before creating an item, confirming
    the board), and forcing the picker in those cases hijacks the composer. Require
    a question plus an explicit selection cue — the same signal the numbered-option
    detector uses."""
    if not reply:
        return False
    low = reply.lower()
    return "?" in reply and any(cue in low for cue in _SELECT_CUES)


def _parse_numbered_options(reply: str) -> list[tuple[str, str]]:
    """Extract a consecutive 1., 2., 3.… option list the agent wants the user to PICK.

    Returns [(number, label)] ONLY when: the reply has ≥2 options numbered consecutively
    from 1 AND the reply contains an explicit selection cue ("which…", "reply with the
    number", "select/pick/choose one"). The selection cue is what distinguishes a real
    "pick one" prompt from an enumerated list that merely precedes a yes/no confirmation
    ("Does this look correct?") — e.g. numbered acceptance-criteria scenarios — which must
    NOT become a forced-choice card. Empty list otherwise."""
    if not reply:
        return []
    low = reply.lower()
    if "?" not in reply or not any(cue in low for cue in _SELECT_CUES):
        return []
    opts: list[tuple[str, str]] = []
    for line in reply.splitlines():
        m = _NUM_OPT_RE.match(line)
        if not m:
            continue
        label = m.group(2).strip().strip("*_`").strip()
        # Drop a trailing markdown bold marker imbalance and over-long lines.
        if not label or len(label) > 160:
            continue
        opts.append((m.group(1), label))
    if len(opts) < 2:
        return []
    if [int(n) for n, _ in opts] != list(range(1, len(opts) + 1)):
        return []
    return opts


async def _emit_numbered_choice_card(reply: str, stage: str, run_id: str,
                                     websocket: WebSocket) -> None:
    """Turn a plain-text numbered question ("Which project? 1. …  2. …") into a clickable
    single-select card. The option id is the number the agent expects back, so the
    `_answer_to_text` echo ("I select: 2") resolves to the agent's intended choice."""
    opts = _parse_numbered_options(reply)
    if not opts:
        return
    from shared.models.copilot import ChoiceCard, ChoiceOption

    card = ChoiceCard(
        card_id=f"num_{uuid.uuid4().hex[:8]}",
        run_id=run_id,
        stage=stage or "",
        kind="custom",
        prompt="Choose an option:",
        options=[ChoiceOption(id=num, label=label[:120]) for num, label in opts],
        min_select=1,
        max_select=1,
    )
    await _send(websocket, {"type": "choice.card", "run_id": run_id,
                            "card": card.model_dump()})


async def _resolve_project_for_stories(graph, run_id: str, project_id: Optional[str]) -> Optional[str]:
    """Best-effort: find the exact provider project name the story list was scoped to.

    The reliable source is the `project` argument the agent passed to the last
    list_board_items/list_board_items_by_state call, captured in the checkpoint. Falls
    back to the run's project_id. Returns None when neither is available (caller then
    skips the story card rather than guessing)."""
    try:
        state = await graph.aget_state({"configurable": {"thread_id": run_id}})
        messages = (getattr(state, "values", {}) or {}).get("messages", []) or []
    except Exception:  # noqa: BLE001
        messages = []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break  # scan the current turn only (agent may summarize after the tool call)
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name in _STORY_LIST_TOOLS:
                args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
                proj = (args or {}).get("project") if isinstance(args, dict) else None
                if proj:
                    return str(proj)
    return project_id


def _advance_decision(artifact_present: bool, can_approve: bool, gate_type: str) -> str:
    """Pure advance-vs-gate rule — the single source of truth for both call sites.

    Deliberately ignores whether a HANDOFF:: sentinel appeared this turn; that was
    an accident of LLM phrasing, not a real signal. The pipeline NEVER auto-advances
    to the next agent on its own — moving on is always the user's explicit choice
    (they click Approve on the gate, or switch agents). Otherwise a driver who can
    approve every stage (admin:* wildcard) would cascade Security->Testing->Deployment
    with no chance to steer. So:

    - No artifact yet             -> "wait"  (nothing to advance or gate on).
    - gate_type == "auto_approve" -> "advance" (only Documentation, the terminal
                                      auto-approved stage — advancing just completes).
    - Otherwise                   -> "gate"  (open the approval gate + WAIT for the
                                      user to approve; `can_approve` decides whether
                                      the Approve affordance is enabled, not whether
                                      we advance automatically).

    `can_approve` is retained in the signature (callers pass it, and it drives the
    gate.state `can_approve` flag emitted by `_apply_gate`) but no longer forces an
    automatic advance.
    """
    if not artifact_present:
        return "wait"
    if gate_type == "auto_approve":
        return "advance"
    return "gate"


async def _stage_artifact_present(stage: str, run_id: str) -> bool:
    """True if AGENT_REGISTRY[stage].output_artifact is populated on the run row."""
    from config.agent_registry import AGENT_REGISTRY

    defn = AGENT_REGISTRY.get(stage)
    artifact_col = getattr(defn, "output_artifact", None) if defn else None
    if not artifact_col:
        return False
    async with get_db_session_superuser() as s:
        run = (
            await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
        ).scalar_one_or_none()
        if run is None:
            return False
        return bool(getattr(run, artifact_col, None))


async def _apply_advance(stage: str, run_id: str, tenant_id: str, websocket: WebSocket) -> None:
    """Advance current_stage to the next stage (or mark the run complete).

    Emits the visible "✓ <stage> approved — starting <next>" chat note + stage.changed
    (shared side effect for both the HANDOFF path and the conversational-gate path)."""
    from shared.services.orchestrator import progression

    nxt = progression.next_stage(stage)
    async with get_db_session_for_tenant(tenant_id) as s:
        run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
        if run is not None:
            run.gate_pending = False
            if nxt:
                run.current_stage = nxt
                run.status = "running"
            else:
                run.status = "complete"
            await s.commit()
    if nxt:
        await _send(websocket, {
            "type": "stream_chunk", "run_id": run_id,
            "content": f"\n\n✓ {stage.replace('_', ' ').title()} approved — starting "
                       f"{nxt.replace('_', ' ').title()}.\n",
        })
        await _send(websocket, {"type": "stream_end", "run_id": run_id})
        await _send(websocket, {"type": "stage.changed", "run_id": run_id, "stage": nxt})
    else:
        await _send(websocket, {"type": "stream_chunk", "run_id": run_id,
                                "content": "\n\n✓ Pipeline complete.\n"})
        await _send(websocket, {"type": "stream_end", "run_id": run_id})


async def _apply_gate(stage: str, run_id: str, tenant_id: str, perms: list[str],
                      websocket: WebSocket) -> None:
    """Open the approval gate for stage; emit gate.state + notify the owning role.

    Shared side effect for both the HANDOFF path and the conversational-gate path."""
    if tenant_id:
        async with get_db_session_for_tenant(tenant_id) as s:
            run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
            if run is not None and not bool(run.gate_pending):
                run.status = f"awaiting_{stage}_approval"
                run.gate_pending = True
                await s.commit()
    owner_role = gate_owner_role(stage)
    await _send(websocket, {
        "type": "gate.state", "run_id": run_id, "stage": stage,
        "status": "awaiting_gate", "owner_role": owner_role,
        "can_approve": can_user_approve(perms, stage),
    })
    await notify_gate_pending(run_id, stage, owner_role, tenant_id or "")


def _stage_label(stage: str) -> str:
    """Human-facing stage name, e.g. "code_review" -> "Code Review"."""
    return stage.replace("_", " ").title()


async def _repoint_stage(
    run_id: str, tenant_id: Optional[str], target: str, actor_id: str = "system"
) -> None:
    """Repoint run.current_stage to *target* for a conversational agent switch.

    Mirrors `copilot_set_stage`'s run-write + AuditEvent pattern (shared/routers/
    runs.py), but this is invoked from inside the WS turn loop, which — like every
    other run-keyed read in this module (`_active_stage`, `_run_model_offering`) —
    has no request-scoped tenant DB session to reuse, so it uses the superuser
    session for this run-keyed system write. The AuditEvent write is best-effort
    and never blocks the switch. `actor_id` defaults to "system" for callers that
    don't have a real user in scope, but `_maybe_switch_stage` always threads the
    caller's real user_id through so the audit trail attributes the switch
    correctly (mirrors `copilot_set_stage`'s actor_id, runs.py)."""
    async with get_db_session_superuser() as s:
        run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
        if run is None:
            return
        run.current_stage = target
        run.status = "running"
        run.gate_pending = False
        try:
            audit = AuditEvent(
                tenant_id=uuid.UUID(tenant_id) if tenant_id else run.tenant_id,
                actor_id=actor_id,
                event_type="run.stage_set",
                resource_type="run",
                resource_id=str(run.id),
                payload={"stage": target, "conversational": True},
            )
            s.add(audit)
        except Exception as exc:  # noqa: BLE001 — audit is best-effort
            logger.warning("copilot _repoint_stage audit skipped (run=%s): %s", run_id, exc)
        await s.commit()


async def _classify_switch(text: str, current_stage: str, run_id: str) -> dict:
    """Cheap LLM JSON classifier for the ambiguity gate in `detect_switch` — only
    called when the turn mentions a stage but the rule-based `rule_match` couldn't
    tell whether it's a real "switch agents" cue or an in-agent request (e.g.
    "document this function" vs "can you get the docs done next").

    Reuses the run's resolved BYOK model (same resolve_model_for_run + local-dev
    ANTHROPIC_API_KEY fallback every other agent's build_llm uses) so this doesn't
    require its own model configuration. FAIL-SOFT: any error (no model configured,
    provider error, malformed JSON) returns {"switch": False} — a classifier hiccup
    must never block the turn (spec §4.2)."""
    try:
        async with get_db_session_superuser() as s:
            run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
            if run is None:
                return {"switch": False}
            # Capture ORM attrs into locals BEFORE the session (and its identity
            # map) closes below — reading them after `async with` exits risks a
            # DetachedInstanceError (mirrors `_active_stage`'s in-block reads).
            tenant_id = str(run.tenant_id) if run.tenant_id else ""
            model_id = getattr(run, "model_id", None)
            offering_id = getattr(run, "offering_id", None)

        from shared.services.model_resolver import (
            ModelNotEnabledError,
            NoModelConfiguredError,
            ResolvedModel,
            resolve_model_for_run,
        )

        try:
            resolved = await resolve_model_for_run(tenant_id, model_id, offering_id=offering_id)
        except (NoModelConfiguredError, ModelNotEnabledError):
            from config.env import AGENT_RUNTIME_MODE, ANTHROPIC_API_KEY, ANTHROPIC_MODEL
            if AGENT_RUNTIME_MODE == "enterprise" or not ANTHROPIC_API_KEY:
                return {"switch": False}
            resolved = ResolvedModel(
                provider="anthropic", litellm_provider="anthropic", model=ANTHROPIC_MODEL,
                api_key=ANTHROPIC_API_KEY, base_url=None, alias="local-dev:anthropic",
            )

        from langchain_core.messages import HumanMessage as _Human, SystemMessage as _System
        from langchain_litellm import ChatLiteLLM

        llm = ChatLiteLLM(
            model=resolved.model, custom_llm_provider=resolved.litellm_provider,
            api_base=resolved.base_url or None, api_key=resolved.api_key,
            temperature=0.0, max_tokens=64, max_retries=1,
        )
        system = (
            "You classify one chat turn for a multi-agent SDLC pipeline. The user is "
            f"currently talking to the '{current_stage}' agent. Given the turn text, decide "
            "if the user wants to SWITCH to a DIFFERENT agent (not just ask the current "
            "agent to do something related). The only valid target ids are: "
            f"{', '.join(STAGE_IDS)}. Reply with STRICT JSON only, no prose: "
            '{"switch": true|false, "target": "<one of the ids above>" or null}'
        )
        response = await llm.ainvoke([_System(content=system), _Human(content=text)])
        content = response.content if isinstance(response.content, str) else str(response.content)
        blob = content.strip()
        start, end = blob.find("{"), blob.rfind("}")
        if start == -1 or end == -1 or end < start:
            return {"switch": False}
        result = json.loads(blob[start:end + 1])
        return result if isinstance(result, dict) else {"switch": False}
    except Exception as exc:  # noqa: BLE001 — classifier is best-effort, never blocks the turn
        logger.info("copilot _classify_switch(%s) skipped: %s", run_id, exc)
        return {"switch": False}


async def _maybe_switch_stage(
    text: str, active: str, run_id: str, tenant_id: str, perms: list[str], websocket: WebSocket,
    actor_id: str = "system",
) -> tuple[str, bool]:
    """Detect + apply a natural-language "switch agent" cue for this turn.

    Returns `(active_stage, should_process_turn)`. `active_stage` is the (possibly
    new) active stage. `should_process_turn` is False whenever a switch happened OR
    was denied — a switch only ACTIVATES the target agent, it does NOT run its work.
    The switching message ("switch to security", "run documentation") is a routing
    command, not a task; feeding it to the newly-activated agent made it auto-run its
    entire flow (full security scan, all docs) unbidden. So after switching we stop
    the turn and WAIT for the user's next instruction. `should_process_turn` is True
    only for no-switch (route to the unchanged current agent) or a fail-soft error.

    RBAC mirrors `set-stage`: a caller lacking the target stage's approve permission
    gets a visible chat notice and stays on `active`. Fully fail-soft — any error
    (detector, RBAC lookup, DB write) returns `(active, True)` unchanged so a bug
    here can never block a turn (spec §4.2)."""
    try:
        target = await detect_switch(
            text, active, llm_classify=lambda t, c, ids: _classify_switch(t, c, run_id))
        if not target or target == active:
            return active, True

        if not can_user_approve(perms, target):
            notice = f"You don't have permission to switch to {_stage_label(target)}."
            await _send(websocket, {"type": "stream_chunk", "run_id": run_id, "content": notice})
            await _send(websocket, {"type": "stream_end", "run_id": run_id})
            await persist_turn(run_id, "agent", notice, tenant_id=tenant_id or None, author_id="system")
            return active, False

        await _repoint_stage(run_id, tenant_id, target, actor_id=actor_id)
        await _send(websocket, {"type": "stage.changed", "run_id": run_id, "stage": target})
        note = (f"↳ Switched to the {_stage_label(target)} agent. "
                f"Tell me what you'd like it to do.")
        await _send(websocket, {"type": "stream_chunk", "run_id": run_id, "content": note})
        await _send(websocket, {"type": "stream_end", "run_id": run_id})
        await persist_turn(run_id, "agent", note, tenant_id=tenant_id or None, author_id=target)
        # Activate only — do NOT route this switch command into the target agent.
        return target, False
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — conversational switching is an enhancement
        logger.warning("copilot _maybe_switch_stage(%s) failed: %s", run_id, exc)
        return active, True


async def _maybe_detect_conversational_gate(
    stage: str, run_id: str, tenant_id: str, perms: list[str], websocket: WebSocket
) -> None:
    """Advance or open the stage gate once the stage's artifact exists.

    NOTHING ELSE advances current_stage or flips gate_pending — there used to be a
    Temporal workflow that could, and this guarded against double-advancing a run it
    owned. That engine is gone and every run is conversational, so the guard went with
    it and this path is now unconditional. Here we detect that the
    active stage's output artifact column (AGENT_REGISTRY[stage].output_artifact) is now
    populated on the run and — via the SAME `_advance_decision` rule used by the HANDOFF
    path (`_advance_or_gate`) — either advance in-chat or open the approval gate. This is
    what makes Design->Development behave identically to Requirements->Design, regardless
    of whether the agent happened to emit a HANDOFF:: sentinel that turn.

    Fully fail-soft: any miss (unmapped stage, DB error, no artifact yet) is a no-op — _maybe_open_gate still runs afterward for the already-
    gated case."""
    try:
        from shared.services.orchestrator import progression

        # Read run state (superuser — run-keyed system read).
        async with get_db_session_superuser() as s:
            run = (
                await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
            ).scalar_one_or_none()
            if run is None:
                return
            if bool(run.gate_pending):
                return  # already gated — _maybe_open_gate handles emission

        artifact_present = await _stage_artifact_present(stage, run_id)
        gate_type = progression.gate_type_for(stage)
        can_approve = can_user_approve(perms, stage)
        decision = _advance_decision(artifact_present, can_approve, gate_type)

        if decision == "wait":
            return  # agent hasn't produced its artifact yet
        if decision == "advance":
            await _apply_advance(stage, run_id, tenant_id, websocket)
            return
        await _apply_gate(stage, run_id, tenant_id, perms, websocket)
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — gate detection is an enhancement
        logger.warning("copilot _maybe_detect_conversational_gate(%s) failed: %s", run_id, exc)


async def _maybe_open_gate(stage: str, run_id: str, tenant_id: str, perms: list[str],
                           websocket: WebSocket) -> None:
    """Emit role-routed gate state when the active stage is awaiting its gate.

    Reads runs.gate_pending; when set, emits gate.state = {stage, status, owner_role,
    can_approve} and fires the best-effort notify seam. Never raises into the turn."""
    try:
        gate_pending = False
        async with get_db_session_superuser() as s:
            run = (
                await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
            ).scalar_one_or_none()
            if run is not None:
                gate_pending = bool(run.gate_pending)
        if not gate_pending:
            return
        owner_role = gate_owner_role(stage)
        payload = {
            "type": "gate.state",
            "run_id": run_id,
            "stage": stage,
            "status": "awaiting_gate",
            "owner_role": owner_role,
            "can_approve": can_user_approve(perms, stage),
        }
        await _send(websocket, payload)
        await notify_gate_pending(run_id, stage, owner_role, tenant_id or "")
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _maybe_open_gate(%s) failed: %s", run_id, exc)


async def _detect_handoff(graph, run_id: str) -> Optional[dict]:
    """Parse the HANDOFF::{...} sentinel from the current turn's last AIMessage.

    The requirements/design agents emit HANDOFF::<json> after the user confirms the
    stage is done. Returns the parsed dict ({"to": <next>, "stage_completed": …}) or
    None when the turn produced no handoff."""
    try:
        state = await graph.aget_state({"configurable": {"thread_id": run_id}})
        messages = (getattr(state, "values", {}) or {}).get("messages", []) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _detect_handoff(%s) failed: %s", run_id, exc)
        return None
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break
        if not isinstance(m, AIMessage):
            continue
        text = _extract_text(m.content) if not isinstance(m.content, str) else m.content
        idx = text.find("HANDOFF::")
        if idx == -1:
            continue
        blob = text[idx + len("HANDOFF::"):].strip()
        # The payload is a single JSON object; take from the first { to its matching }.
        start = blob.find("{")
        if start == -1:
            return {}
        depth = 0
        for j in range(start, len(blob)):
            if blob[j] == "{":
                depth += 1
            elif blob[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(blob[start:j + 1])
                    except Exception:  # noqa: BLE001
                        return {}
        return {}
    return None


async def _capture_requirements_payload(graph, run_id: str) -> Optional[dict]:
    """Pull the REQUIREMENTS_PAYLOAD:: JSON the agent built, from the turn's tool output.

    build_requirements_payload returns 'REQUIREMENTS_PAYLOAD::\\n<json>' as a tool
    message; that structured object is what the Design stage consumes. Returns the
    parsed dict or None (agent hasn't packaged yet)."""
    try:
        state = await graph.aget_state({"configurable": {"thread_id": run_id}})
        messages = (getattr(state, "values", {}) or {}).get("messages", []) or []
    except Exception:  # noqa: BLE001
        return None
    for m in reversed(messages):
        content = m.content if isinstance(getattr(m, "content", None), str) else _extract_text(getattr(m, "content", None))
        if content and "REQUIREMENTS_PAYLOAD::" in content:
            blob = content.split("REQUIREMENTS_PAYLOAD::", 1)[1].strip()
            try:
                return json.loads(blob)
            except Exception:  # noqa: BLE001
                return None
    return None


async def _capture_development_artifacts(run_id: str, tenant_id: str) -> Optional[dict]:
    """Pull the dev agent's `development_artifacts` from the session store and persist it
    onto the run row, so downstream stages (Testing, Code Review, ...) inherit the repo/
    branch/changes. Mirrors _capture_requirements_payload's handoff-capture pattern.
    Fail-soft: any error (or no artifacts yet) returns None without raising."""
    try:
        artifacts = await fetch_session_artifacts(run_id)
        payload = (artifacts or {}).get("development_artifacts")
        if not payload:
            return None
        # overwrite=True: a real submit (repo edited/committed) must win over the minimal
        # clone stub that _ensure_dev_workspace_persisted may have written first.
        await _persist_run_artifact(run_id, tenant_id, "development_artifacts", payload,
                                    overwrite=True)
        return payload
    except Exception as exc:  # noqa: BLE001 — handoff capture is best-effort
        logger.warning("copilot _capture_development_artifacts(%s) failed: %s", run_id, exc)
        return None


async def _ensure_dev_workspace_persisted(run_id: str, tenant_id: str) -> None:
    """Persist a MINIMAL development_artifacts stub once the dev agent has cloned a repo.

    The dev agent only writes a full `development_artifacts` payload when it SUBMITS
    (after editing/committing) — cloning alone leaves the run column null, so the
    code-tree is a live-only frontend synthetic that vanishes on a stage switch and the
    rail can't show Development as done. The dev agent's in-process session state
    (`get_session(run_id)`) does hold repo_url/branch/work_dir right after the clone, so
    we mirror that into a minimal stub (overwrite=False — a later real submit wins). This
    also lets downstream stages inherit the cloned repo. Fail-soft."""
    try:
        from agents_orchestrator.development_agent.config.session_state import get_session
        sess = get_session(run_id)
        repo_url = getattr(sess, "repo_url", "") or ""
        work_dir = getattr(sess, "work_dir", "") or ""
        if not (repo_url or work_dir):
            # Session state is in-memory (lost on restart); fall back to the on-disk
            # clone so the code-tree persists regardless. _run_dev_work_dir checks the
            # session, then the files/<user>/orchestrator/<run>/project clone, then the
            # Bridge clone.
            from shared.routers.runs import _run_dev_work_dir
            work_dir = await _run_dev_work_dir(run_id, tenant_id=tenant_id or "") or ""
            if not work_dir:
                return  # nothing cloned yet
        stub = {
            "repo_url": repo_url or None,
            "repo_type": getattr(sess, "repo_type", "") or None,
            "branch_name": getattr(sess, "branch_name", "") or None,
            "pr_url": getattr(sess, "pr_url", "") or None,
            "cloned": True,
        }
        await _persist_run_artifact(run_id, tenant_id, "development_artifacts", stub)
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail the turn
        logger.info("copilot dev workspace stub skipped (run=%s): %s", run_id, exc)


async def _persist_run_artifact(run_id: str, tenant_id: str, column: str, payload: dict,
                                *, overwrite: bool = False) -> None:
    """Write a stage artifact onto the run row (tenant-scoped). Best-effort.

    overwrite=False only fills an empty column (idempotent handoff capture);
    overwrite=True replaces it (a regenerated document should win)."""
    if not (tenant_id and payload):
        return
    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
            if run is not None and (overwrite or getattr(run, column, None) in (None, {}, "")):
                setattr(run, column, payload)
                await s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _persist_run_artifact(%s.%s) failed: %s", run_id, column, exc)


async def _finalize_artifact(stage: str, run_id: str, tenant_id: str, markdown: str,
                             websocket: WebSocket) -> None:
    """Parse a streamed document into sections, persist it, and emit the authoritative list."""
    from config.agent_registry import AGENT_REGISTRY
    try:
        if stage == "design":
            sections, persist = parse_design_markdown(markdown)
        else:
            sections, persist = [], {}
        defn = AGENT_REGISTRY.get(stage)
        col = getattr(defn, "output_artifact", None) if defn else None
        if col and persist:
            await _persist_run_artifact(run_id, tenant_id, col, persist, overwrite=True)
        await _send(websocket, {"type": "artifact.ready", "run_id": run_id,
                                "stage": stage, "artifacts": sections})
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — artifact finalize is best-effort
        logger.warning("copilot _finalize_artifact(%s/%s) failed: %s", stage, run_id, exc)


async def _capture_stage_report(stage: str, run_id: str, tenant_id: str,
                                reply: str, websocket: WebSocket) -> None:
    """Capture a downstream agent's substantive output as a panel 'Report' artifact.

    Code Review / Security / Testing / Deployment / Documentation produce findings /
    results as markdown in chat; we mirror the latest substantial reply into the run's
    artifact column + emit artifact.ready so it also appears (and persists) in the panel.
    Markdown renders their tables / code / diagrams inline. Overwrites so the panel
    always shows the latest report."""
    col = _STAGE_ARTIFACT_COLUMN.get(stage)
    if not col or not reply or len(reply.strip()) < 200:
        return
    title = f"{stage.replace('_', ' ').title()} Report"
    sections = [{
        "id": f"{stage}-report", "stage": stage, "kind": "markdown",
        "title": title, "content": reply,
    }]
    try:
        await _persist_run_artifact(run_id, tenant_id, col, {"sections": sections, "markdown": reply}, overwrite=True)
        await _send(websocket, {"type": "artifact.ready", "run_id": run_id,
                                "stage": stage, "artifacts": sections})
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _capture_stage_report(%s/%s) failed: %s", stage, run_id, exc)


def _reply_artifact_title(reply: str, fallback: str = "Requirements") -> str:
    """Derive a distinguishing panel title from a captured turn reply.

    Incremental Requirements captures were all titled "Requirements", which made
    the panel list unreadable. Prefer the first markdown heading in the reply;
    fall back to the first non-empty line; truncate for the panel row."""
    heading = re.search(r"(?m)^#{1,4}\s+(.+?)\s*$", reply)
    title = heading.group(1) if heading else ""
    if not title:
        for line in reply.splitlines():
            line = line.strip()
            if line:
                title = line
                break
    title = re.sub(r"[*_`#]+", "", title).strip()
    if not title:
        return fallback
    return title[:57] + "…" if len(title) > 60 else title


async def _capture_requirements_artifacts(run_id: str, tenant_id: str, reply: str,
                                          websocket: WebSocket) -> None:
    """Mirror a Requirements turn's substantial reply (normalised Gherkin acceptance
    criteria, doc/BRD summaries) into `requirements_artifacts.sections` as markdown.

    Unlike `_capture_stage_report` (which overwrites with the latest single report),
    Requirements is a multi-turn conversation where distinct turns each carry their
    own useful content (e.g. the AC turn AND the doc-summary turn) — so this
    ACCUMULATES distinct replies instead of replacing. Keeps the reply in chat too;
    this never routes Requirements into the report/panel-only path. Fail-soft
    throughout — a capture failure must never surface into the turn."""
    if not reply or len(reply.strip()) < 200:
        return
    reply = reply.strip()
    try:
        existing: dict = {}
        async with get_db_session_superuser() as s:
            run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
            val = getattr(run, "requirements_artifacts", None) if run is not None else None
            if isinstance(val, dict):
                existing = dict(val)

        secs = list(existing.get("sections") or [])
        if any(sec.get("content") == reply for sec in secs if isinstance(sec, dict)):
            return
        secs.append({
            "id": f"requirements-doc-{len(secs) + 1}", "stage": "requirements",
            "kind": "markdown", "title": _reply_artifact_title(reply), "content": reply,
        })
        secs = secs[-10:]
        existing["sections"] = secs

        await _persist_run_artifact(run_id, tenant_id, "requirements_artifacts", existing, overwrite=True)
        await _send(websocket, {"type": "artifact.ready", "run_id": run_id,
                                "stage": "requirements", "artifacts": secs})
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _capture_requirements_artifacts(%s) failed: %s", run_id, exc)


def _generated_stage_dir(run_id: str, stage: str) -> str:
    """Directory Security/Code Review/Deployment write their generated files to for
    this run: `{FILES}/system/orchestrator/<run_id>/generated/<stage>`. "system" is a
    synthetic user segment (the Copilot has no per-stage acting-user concept) that
    `_run_stage_output_dir`'s `_glob_user_scoped_dir` (`glob("*")`) matches like any
    other user directory once it exists — created here on first write. Created
    lazily so the dir only appears once a stage actually has output."""
    from shared.routers.runs import _stage_files_dir
    d = os.path.join(_stage_files_dir(), "system", "orchestrator", str(run_id), "generated", stage)
    os.makedirs(d, exist_ok=True)
    return d


def _write_text_file(out_dir: str, filename: str, content: str) -> None:
    with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as fh:
        fh.write(content or "")


def _write_json_file(out_dir: str, filename: str, data: Any) -> None:
    _write_text_file(out_dir, filename, json.dumps(data, indent=2, default=str))


def _render_findings_markdown(findings: list, *, label_key: str = "title") -> str:
    """Render a Security/Code-Review findings list as readable markdown. Both artifact
    shapes carry `id`/`severity`/`file`/`line`; Security findings have a `title`,
    Code Review findings only have `description` (label_key picks which to header
    with) — everything is read via `.get()` so a missing key never raises."""
    if not findings:
        return "_No findings._\n"
    lines = ["# Findings", ""]
    for f in findings:
        if not isinstance(f, dict):
            continue
        fid = f.get("id", "")
        sev = f.get("severity", "")
        label = f.get(label_key) or f.get("title") or f.get("description") or ""
        header = " ".join(p for p in (fid, f"[{sev}]" if sev else "", label) if p)
        lines.append(f"## {header}".strip() or "## Finding")
        loc = f.get("file")
        if loc:
            line_no = f.get("line")
            lines.append(f"- File: `{loc}`" + (f":{line_no}" if line_no else ""))
        desc = f.get("description")
        if desc and label_key != "description":
            lines.append("")
            lines.append(str(desc))
        rec = f.get("recommendation") or f.get("remediation")
        if rec:
            lines.append("")
            lines.append(f"**Recommendation:** {rec}")
        lines.append("")
    return "\n".join(lines)


async def _write_security_generated_files(run_id: str) -> bool:
    """Security's SBOM + findings live only in the session's `last_artifact` (the
    SecurityArtifact.model_dump() submitted by submit_security_review) until now —
    write them to the run's generated dir so the panel can browse/download them."""
    try:
        from agents_orchestrator.security_agent.config.session_state import get_session
    except Exception:  # noqa: BLE001
        return False
    artifact = getattr(get_session(run_id), "last_artifact", None) or {}
    if not isinstance(artifact, dict):
        return False
    sbom = artifact.get("sbom") or []
    findings = artifact.get("findings") or []
    if not sbom and not findings:
        return False
    out_dir = _generated_stage_dir(run_id, "security")
    if sbom:
        _write_json_file(out_dir, "sbom.json", sbom)
    if findings:
        _write_text_file(out_dir, "findings.md", _render_findings_markdown(findings, label_key="title"))
    return True


async def _write_code_review_generated_files(run_id: str) -> bool:
    """Code Review's findings (+ any autofix_patch, a copyable unified diff never
    applied automatically) live only in the session's `last_artifact` until now."""
    try:
        from agents_orchestrator.code_review_agent.config.session_state import get_session
    except Exception:  # noqa: BLE001
        return False
    artifact = getattr(get_session(run_id), "last_artifact", None) or {}
    findings = artifact.get("findings") if isinstance(artifact, dict) else None
    if not findings:
        return False
    out_dir = _generated_stage_dir(run_id, "code_review")
    _write_text_file(out_dir, "findings.md", _render_findings_markdown(findings, label_key="description"))
    n = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        patch = f.get("autofix_patch")
        if patch:
            n += 1
            _write_text_file(out_dir, f"fix_{n}.patch", patch)
    return True


def _safe_relpath(raw: str) -> Optional[list[str]]:
    """Normalize a staged-file path into safe path segments, guarding traversal
    (`../`, absolute paths, drive letters) — collapses to just the basename if the
    given path tries to escape the target directory."""
    if not raw:
        return None
    norm = os.path.normpath(raw).replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if not parts or norm.startswith("..") or os.path.isabs(norm) or ":" in norm or ".." in parts:
        base = os.path.basename(raw)
        return [base] if base else None
    return parts


async def _write_deployment_generated_files(run_id: str) -> bool:
    """Deployment stages files in-memory via `stage_deploy_file` before opening the
    gated PR (`session.staged_files` — [{path, language, contents}], see deploy_tools.
    stage_deploy_file); write them out so the panel can browse the package before the
    PR lands. Accepts `content` as a fallback key in case a caller used that name."""
    try:
        from agents_orchestrator.deployment_agent.config.session_state import get_session
    except Exception:  # noqa: BLE001
        return False
    staged = getattr(get_session(run_id), "staged_files", None) or []
    if not staged:
        return False
    out_dir = _generated_stage_dir(run_id, "deployment")
    wrote = False
    for entry in staged:
        if not isinstance(entry, dict):
            continue
        parts = _safe_relpath(entry.get("path") or "")
        if not parts:
            continue
        content = entry.get("contents")
        if content is None:
            content = entry.get("content", "")
        target_dir = os.path.join(out_dir, *parts[:-1]) if len(parts) > 1 else out_dir
        os.makedirs(target_dir, exist_ok=True)
        _write_text_file(target_dir, parts[-1], content)
        wrote = True
    return wrote


async def _capture_stage_files(stage: str, run_id: str, tenant_id: str,
                               project_id: Optional[str], websocket: WebSocket) -> bool:
    """Persist a downstream generator's in-memory output (Security SBOM/findings,
    Code Review findings/patches, Deployment staged files) to a run-keyed directory
    on disk, so the Copilot artifacts panel can browse them exactly like it already
    browses Development's workspace. Testing/Documentation/Requirements already
    write to disk on their own (docx for Requirements) — this only checks whether
    their output dir has anything in it.

    When the stage now has >=1 generated file, marks the stage's persisted
    `{stage}_artifacts` dict `has_files: True` (merged with whatever
    `_capture_stage_report` just wrote for this same turn) and emits an
    `artifact.ready` carrying a `file-tree` section so the panel renders it. Returns
    True iff the stage has output on disk. Fail-soft throughout (a dead socket is the
    only thing allowed to propagate)."""
    try:
        if stage == "security":
            wrote = await _write_security_generated_files(run_id)
        elif stage == "code_review":
            wrote = await _write_code_review_generated_files(run_id)
        elif stage == "deployment":
            wrote = await _write_deployment_generated_files(run_id)
        elif stage in ("testing", "documentation", "requirements"):
            from shared.routers.runs import _run_stage_output_dir
            d = await _run_stage_output_dir(run_id, stage, project_id=project_id)
            wrote = bool(d and os.path.isdir(d) and os.listdir(d))
        else:
            return False
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _capture_stage_files(%s/%s) failed: %s", stage, run_id, exc)
        return False

    if not wrote:
        return False

    section = {"id": f"{stage}-files", "stage": stage, "kind": "file-tree",
               "title": "Generated files", "source": stage}
    col = _STAGE_ARTIFACT_COLUMN.get(stage)
    report_section: Optional[dict] = None
    if col:
        existing: dict = {}
        try:
            async with get_db_session_for_tenant(tenant_id) as s:
                run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
                val = getattr(run, col, None) if run is not None else None
                if isinstance(val, dict):
                    existing = dict(val)
        except Exception as exc:  # noqa: BLE001 — merge is best-effort; falls back to just the file-tree section
            logger.warning("copilot _capture_stage_files(%s/%s) fetch-existing failed: %s", stage, run_id, exc)
        # C3: do NOT embed the file-tree into the persisted sections list —
        # `sections_from_run` synthesizes it once (from `has_files`) on reload, so
        # persisting it here too would duplicate the `{stage}-files` id there.
        persisted_sections = [sec for sec in (existing.get("sections") or []) if sec.get("kind") != "file-tree"]
        report_section = next((sec for sec in persisted_sections if sec.get("id") == f"{stage}-report"), None)
        existing["sections"] = persisted_sections
        existing["has_files"] = True
        await _persist_run_artifact(run_id, tenant_id, col, existing, overwrite=True)

    # C1: carry the report section (if `_capture_stage_report` persisted one this turn)
    # alongside the file-tree so the frontend's per-event "replace" semantics for
    # `readyArtifacts` don't drop the Report when the file-tree event lands second.
    emit_artifacts = [report_section, section] if report_section else [section]
    try:
        await _send(websocket, {"type": "artifact.ready", "run_id": run_id,
                                "stage": stage, "artifacts": emit_artifacts})
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("copilot _capture_stage_files(%s/%s) send failed: %s", stage, run_id, exc)

    return True


_DESIGN_SHORT = {
    "High-Level Design (HLD)": "HLD", "Low-Level Design (LLD)": "LLD",
    "C4 Architecture Diagram": "C4", "API Contracts": "API",
    "Database Schema": "DB", "Architecture Decision Records (ADRs)": "ADRs",
    "Technology Stack & Infrastructure": "Tech Stack",
}


def _artifact_section_titles(stage: str, markdown: str) -> str:
    """Short 'HLD · LLD · C4 · …' summary for the chat card."""
    if stage == "design":
        sections, _ = parse_design_markdown(markdown)
        return " · ".join(_DESIGN_SHORT.get(s["title"], s["title"]) for s in sections)
    return ""


async def _emit_stage_artifacts_ready(run_id: str, tenant_id: str, stage: str,
                                      websocket: WebSocket) -> None:
    """Push a just-persisted stage's artifacts to the LIVE panel (`artifact.ready`) so
    they show up without a reload — the same event `_finalize_artifact`/
    `_capture_stage_report` already emit for Design/report stages. Development has no
    equivalent call site (its handoff persists `development_artifacts` in
    `_advance_or_gate` with nothing pushing the resulting code-tree/summary/PR sections
    live), which is what this closes. Reads the just-committed run row and builds the
    stage's sections via `artifacts_view` (the same helpers the REST reload path uses,
    so live and reload agree). Fail-soft: never raises, no-ops if the stage has nothing
    to show yet."""
    if not tenant_id:
        return
    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            run = (await s.execute(
                select(Run).where(Run.id == _as_run_uuid(run_id))
            )).scalar_one_or_none()
        if run is None:
            return
        if stage == "development":
            dev = getattr(run, "development_artifacts", None)
            sections = _development_sections(dev) if isinstance(dev, dict) and dev else []
        else:
            sections = [sec for sec in sections_from_run(run) if sec.get("stage") == stage]
        if not sections:
            return
        await _send(websocket, {"type": "artifact.ready", "run_id": run_id,
                                "stage": stage, "artifacts": sections})
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — live push is best-effort, reload still works
        logger.warning("copilot _emit_stage_artifacts_ready(%s/%s) failed: %s", stage, run_id, exc)


async def _advance_or_gate(stage: str, run_id: str, tenant_id: str, perms: list[str],
                           project_id: Optional[str], graph, websocket: WebSocket) -> None:
    """Consume a stage handoff: persist the artifact, then advance or gate (smart-advance).

    - Persist the stage's output artifact to the run row so the next agent (via
      pipeline_session upstream mirroring) receives it.
    - Route through the SAME `_advance_decision` rule used by the no-HANDOFF
      conversational-gate path (`_maybe_detect_conversational_gate`): artifact present +
      gate_type + can-approve decide advance/gate/wait — never the HANDOFF sentinel
      itself. Advance → current_stage to the next stage in-chat (emit stage.changed), or
      mark the run complete when none. Gate → open the approval gate and notify the
      owning role (separation of duties: the driver is not the approver). Fully
      fail-soft."""
    from shared.services.orchestrator import progression

    try:
        # 1) Capture + persist the stage artifact (requirements today; design reply later).
        if stage == "requirements":
            payload = await _capture_requirements_payload(graph, run_id)
            if payload:
                await _persist_run_artifact(run_id, tenant_id, "requirements_payload", payload)
        elif stage == "development":
            await _capture_development_artifacts(run_id, tenant_id)
            # Push the dev code-tree/summary/PR sections to the live panel now — nothing
            # else does (Design/report stages emit their own artifact.ready inline while
            # streaming; Development's handoff has no equivalent, which is why the panel
            # went blank after the stage advanced without a reload).
            await _emit_stage_artifacts_ready(run_id, tenant_id, "development", websocket)

        # 2) Smart advance vs gate vs wait — same rule as the conversational path.
        artifact_present = await _stage_artifact_present(stage, run_id)
        gate_type = progression.gate_type_for(stage)
        can_approve = can_user_approve(perms, stage)
        decision = _advance_decision(artifact_present, can_approve, gate_type)

        if decision == "wait":
            return  # HANDOFF fired but the artifact wasn't captured — nothing to do yet
        if decision == "advance":
            await _apply_advance(stage, run_id, tenant_id, websocket)
            return
        await _apply_gate(stage, run_id, tenant_id, perms, websocket)
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — advancement is best-effort; never kill the turn
        logger.warning("copilot _advance_or_gate(%s/%s) failed: %s", stage, run_id, exc)


async def _handle_gate_decision(stage: str, run_id: str, tenant_id: str, perms: list[str],
                                decision: str, reason: Optional[str],
                                websocket: WebSocket) -> None:
    """Approve/reject the current stage's gate over the WS (conversational run).

    Approve → advance current_stage to the next stage (or complete). Reject → clear the
    gate, stay on the stage. Server re-checks the stage approve permission (fail-closed).
    Emits a chat note; the caller emits stage.changed after re-reading active."""
    from shared.services.orchestrator import progression
    approve = decision in ("approve", "approved", "accept")
    if approve and not can_user_approve(perms, stage):
        await _send(websocket, {"type": "error", "run_id": run_id,
                                "message": "You don't hold the approval permission for this stage."})
        return
    # Never approve-advance a stage that hasn't actually produced its output
    # artifact. Without this, an approve that lands right after the run advances
    # INTO a stage (a duplicate/auto gate-decision from the client) skips the
    # agent entirely — e.g. the mandatory Security gate gets "approved" before
    # the scan ever runs, silently cascading Security -> Testing. auto_approve
    # (Documentation) is exempt: it owns no artifact and its approval just
    # completes the pipeline.
    if approve and progression.gate_type_for(stage) != "auto_approve" \
            and not await _stage_artifact_present(stage, run_id):
        await _send(websocket, {"type": "error", "run_id": run_id,
                                "message": f"{_stage_label(stage)} hasn't run yet — "
                                           f"run this stage before approving its gate."})
        return
    try:
        nxt = progression.next_stage(stage) if approve else None
        async with get_db_session_for_tenant(tenant_id) as s:
            run = (await s.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))).scalar_one_or_none()
            if run is None:
                return
            # A gate can only be actioned when one is genuinely pending. Without this,
            # a spurious/duplicate/mis-timed gate.decision advances a stage that never
            # opened a gate — which cascaded Design → Development with no Design turn.
            if not bool(run.gate_pending):
                logger.info("copilot gate.decision ignored — no gate pending (run=%s stage=%s)",
                            run_id, stage)
                return
            # The gate belongs to the run's CURRENT stage; if the message's stage has
            # drifted from it, act on the run's stage (the source of truth), not a stale one.
            if run.current_stage and run.current_stage != stage:
                stage = run.current_stage
                nxt = progression.next_stage(stage) if approve else None
            run.gate_pending = False
            if approve:
                if nxt:
                    run.current_stage = nxt
                    run.status = "running"
                else:
                    run.status = "complete"
            else:
                run.status = "running"  # rejected → stays on the stage to re-run
            await s.commit()
        if approve and nxt:
            note = f"\n\n✓ {stage.replace('_', ' ').title()} approved — starting {nxt.replace('_', ' ').title()}.\n"
        elif approve:
            note = "\n\n✓ Approved — pipeline complete.\n"
        else:
            note = (f"\n\n✗ {stage.replace('_', ' ').title()} sent back for changes"
                    + (f": {reason}" if reason else "") + ".\n")
        await _send(websocket, {"type": "stream_chunk", "content": note, "run_id": run_id})
        await _send(websocket, {"type": "stream_end", "run_id": run_id})
        await persist_turn(run_id, "agent", note.strip(), tenant_id=tenant_id or None, author_id=stage)
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — never crash the socket on a gate action
        logger.warning("copilot _handle_gate_decision(%s/%s) failed: %s", stage, run_id, exc)


@copilot_router.websocket("/ws")
async def copilot_ws(websocket: WebSocket) -> None:
    """Copilot socket: /sdlc/agent/copilot/ws?ticket=...&run=<run_id>.

    IN  : {type:"user_message", text, run_id?}
          {type:"choice_answer", card_id, selected_ids, free_text, run_id?}
    OUT : stream_chunk | stream_end | choice.card | gate.state | stage.changed | error
    """
    ticket = websocket.query_params.get("ticket", "")
    run_id = websocket.query_params.get("run", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(
            code=4401,
            reason='{"error": "invalid_or_expired_ticket", "detail": "Provide a valid single-use ticket from POST /auth/ws-ticket"}',
        )
        return

    tenant_id = claims.get("tenant_id", "") or ""
    user_id = claims.get("user_id", "") or ""

    if AGENT_RUNTIME_MODE == "enterprise":
        expected_tenant = websocket.query_params.get("tenant_id", "")
        if expected_tenant and tenant_id != expected_tenant:
            await websocket.close(
                code=4403,
                reason='{"error": "tenant_mismatch", "detail": "Token tenant does not match requested tenant"}',
            )
            return
    if not run_id:
        await websocket.close(
            code=4400,
            reason='{"error": "missing_run", "detail": "Provide ?run=<run_id>"}',
        )
        return

    await websocket.accept()

    # Resolve the caller's permissions once per connection (roles are stable for a chat
    # session; the gate check re-uses this list every turn).
    perms = await _resolve_perms(user_id, tenant_id)
    active, run_project_id = await _active_stage(run_id)

    # Per-connection agent-profile scaffolding (design §3.4): memoize each project's
    # workspace_id (the run's project is fixed, but project_id is re-read per turn) so the
    # scope chain resolves without re-hitting Postgres, and dedup profile-applied audit
    # stamps per (stage, prompt_hash) across the whole connection.
    _ws_by_project: dict = {}
    _profile_stamped: set = set()

    async def _workspace_for(pid: Optional[str]) -> Optional[str]:
        if not pid:
            return None
        if pid not in _ws_by_project:
            _ws_by_project[pid] = await _workspace_for_project(tenant_id, pid)
        return _ws_by_project[pid]

    # Pin a conversation session to run_id so the transcript persists (and survives a
    # reload). Copilot keys everything off run_id, so the session's PK must equal it —
    # create_session mints its own uuid, hence ensure_session_with_id. Best-effort.
    try:
        _proj_uuid = _as_run_uuid(run_project_id) if run_project_id else None
    except Exception:  # noqa: BLE001
        _proj_uuid = None
    await ensure_session_with_id(
        run_id, tenant_id, scope_type="copilot", scope_id=run_id,
        run_id=run_id, project_id=_proj_uuid, created_by=str(user_id) or None,
    )

    from workflows.activities.pipeline_session import pipeline_session
    from config.ws_helper import set_user_id

    # Sync the client's rail to the run's ACTUAL stage on connect. Without this a
    # reopened mid-pipeline run (e.g. at Design) shows the rail stuck on Requirements
    # until the next turn, since stage.changed only fires when the stage transitions.
    await _send(websocket, {"type": "stage.changed", "run_id": run_id, "stage": active})
    # Re-emit any pending gate so the approval affordance shows immediately on reopen.
    await _maybe_open_gate(active, run_id, tenant_id, perms, websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                await _send(websocket, {"type": "error", "run_id": run_id,
                                        "message": "Malformed message (expected JSON)."})
                continue

            mtype = msg.get("type")
            if mtype not in ("user_message", "choice_answer", "gate.decision"):
                await _send(websocket, {"type": "error", "run_id": run_id,
                                        "message": f"Unsupported message type: {mtype}"})
                continue

            # Gate approval/rejection over the SAME socket, so the advance is reflected
            # live (rail + chat) instead of only mutating the DB via REST.
            if mtype == "gate.decision":
                new_active, run_project_id = await _active_stage(run_id)
                active = new_active
                await _handle_gate_decision(
                    active, run_id, tenant_id, perms,
                    str(msg.get("decision") or "approved"),
                    (msg.get("reason") or None), websocket)
                nxt_active, run_project_id = await _active_stage(run_id)
                if nxt_active != active:
                    active = nxt_active
                    await _send(websocket, {"type": "stage.changed", "run_id": run_id,
                                            "stage": active})
                continue

            # Re-read the active stage each turn so a gate approval that advanced the run
            # (stage.changed) is picked up without reconnecting.
            new_active, run_project_id = await _active_stage(run_id)
            if new_active != active:
                active = new_active
                await _send(websocket, {"type": "stage.changed", "run_id": run_id,
                                        "stage": active})

            text = msg.get("text") if mtype == "user_message" else _answer_to_text(msg)
            text = (text or "").strip()
            if not text:
                await _send(websocket, {"type": "error", "run_id": run_id,
                                        "message": "Empty message."})
                continue

            project_id = msg.get("project_id") or run_project_id
            try:
                set_user_id(user_id)
            except Exception:  # noqa: BLE001
                pass

            # Fresh turn: drop any stale Stop flag so a cancel from a PRIOR turn
            # can't abort this one before it starts.
            _clear_cancel(run_id)

            await persist_turn(run_id, "user", text, tenant_id=tenant_id or None,
                               author_id=str(user_id))

            # NOTE: natural-language agent switching (`_maybe_switch_stage`) is intentionally
            # NOT invoked here. Auto-switching on a stage NAME in the message ("move to
            # design") short-circuited the current agent's HANDOFF — the Requirements agent
            # never got to package its requirements artifact and pass it to Design, so Design
            # lost all upstream context. The current agent handles the whole turn instead:
            # "save and move to design" -> Requirements packages -> HANDOFF -> gate/approve ->
            # Design inherits the requirements payload. Explicit jumps still work via the
            # left-rail (set-stage). The `_maybe_switch_stage` helper is kept for possible
            # future opt-in but is deliberately off the conversational path.

            # Load the project's per-stage connector + MCP-server maps (fail-soft to {})
            # so this turn honors the project's bound connector and assigned MCP tools.
            connectors_map, mcp_servers_map = await _project_config(tenant_id, project_id)
            shim_input = _shim_input(run_id, tenant_id, project_id,
                                     connectors=connectors_map, mcp_servers=mcp_servers_map)

            # Bind the durable run session (session_id=run_id, mirror upstream, prep
            # workspace) for the length of this turn, then route into the active graph
            # with the project's assigned MCP tools injected (mcp_tools_for_stage sets the
            # contextvar the graph binds via tools + get_mcp_tools(); degrades to no tools).
            graph = _graph_for(active)
            model_id, offering_id = await _run_model_offering(run_id)
            # Downstream (repo-touching) stages get the Bridge to clone the dev
            # agent's branch ONCE into a run-scoped workspace (ps.work_dir) so their
            # tools can operate on it; requirements/design never need one.
            repo_ref = await _repo_ref_for_stage(active, run_id, tenant_id)
            try:
                from workflows.activities._base import mcp_tools_for_stage
                if repo_ref:
                    bridge_cm = pipeline_session(shim_input, active,
                                                 needs_repo=True, repo_ref=repo_ref)
                else:
                    bridge_cm = pipeline_session(shim_input, active)
                async with bridge_cm as ps:
                    # Task 5 — seed Code Review/Security/Deployment/Documentation's
                    # prepared-workspace store from the SAME clone the Bridge just made,
                    # so those agents' tools see a ready repo without the standalone
                    # REST /prepare call the Copilot never makes. No-op for stages that
                    # don't use a prepared-store (requirements/design/development) or
                    # when the Bridge has no work_dir (needs_repo=False / clone failed).
                    _seed_downstream_prepared(active, tenant_id, project_id, run_id, ps)
                    # Report stages: surface generated files (SBOM/findings/deploy package)
                    # to the panel the moment a file-producing tool finishes, not only at
                    # turn end — so a long agent turn shows its artifacts live.
                    _on_files = None
                    if active in _REPORT_STAGES:
                        async def _on_files(_stg=active, _pid=project_id):
                            await _capture_stage_files(_stg, run_id, tenant_id, _pid, websocket)
                    # Agent-profile + skills-index layer (design §3.4): compose the profile
                    # over the stage's base prompt AND resolve the active skills for this
                    # turn in one call. Fail-soft
                    # to base-only + no skills — a miss must NEVER break the turn. testing
                    # (state machine) has no base prompt → injected stays None, skills [].
                    base_prompt = _base_prompt_for(active)
                    injected = base_prompt
                    skills: list = []
                    try:
                        workspace_id = await _workspace_for(project_id)
                        injected, skills, profile = await prepare_agent_turn(
                            active, base_prompt, tenant_id, project_id, workspace_id)
                        if base_prompt and tenant_id and injected != base_prompt:
                            await _stamp_profile_applied(
                                run_id, tenant_id, active, profile, injected,
                                _profile_stamped)
                    except Exception as exc:  # noqa: BLE001 — profile/skills are enhancements
                        logger.warning("copilot profile/skill resolve failed (run=%s stage=%s): %s",
                                       run_id, active, exc)
                        injected = base_prompt
                        skills = []

                    _self_override = injected if active in SELF_INJECT_STAGES else None
                    _msg_override = injected if active in MESSAGE_PROMPT_STAGES else None
                    # prompt_override_scope feeds SELF_INJECT_STAGES via the prompt_runtime
                    # contextvar their graph nodes read; MESSAGE_PROMPT_STAGES receive it as
                    # _stream_active's system_prompt_override. skill_context_scope sets the
                    # per-turn load_skill tool channel (no-op + no scope for the testing state
                    # machine — skills=[]). Outermost so it wraps the whole dispatch and is
                    # cleared on exit even if a turn raises.
                    async with prompt_override_scope(active, _self_override):
                        async with skill_context_scope(active, skills):
                            async with _stage_connector(shim_input, active, tenant_id):
                                async with mcp_tools_for_stage(shim_input, active):
                                    if active in STATE_MACHINE_STAGES:
                                        reply, artifact_md = await _stream_state_machine(
                                            graph, text, run_id, tenant_id, websocket,
                                            active, model_id=model_id, offering_id=offering_id,
                                            work_dir=ps.work_dir)
                                    else:
                                        reply, artifact_md = await _stream_active(
                                            graph, text, run_id, tenant_id, websocket,
                                            active, model_id=model_id, offering_id=offering_id,
                                            on_tool_files=_on_files,
                                            system_prompt_override=_msg_override)
            except WebSocketDisconnect:
                raise
            except Exception as exc:  # noqa: BLE001 — Bridge/graph/MCP failure ≠ dead socket
                logger.error("copilot turn failed (run=%s stage=%s): %s", run_id, active, exc)
                await _send(websocket, {"type": "error", "run_id": run_id,
                                        "message": "The agent could not process this turn."})
                continue

            # A document/report turn streamed into the Artifacts panel: persist to the
            # run's artifact column, emit the authoritative list, and put a compact card
            # in the chat transcript (the full doc/report lives in the panel, never chat).
            if artifact_md:
                if active in _REPORT_STAGES:
                    await _capture_stage_report(active, run_id, tenant_id, artifact_md, websocket)
                    await _capture_stage_files(active, run_id, tenant_id, project_id, websocket)
                    card = (f"✓ {active.replace('_', ' ').title()} report generated"
                            " — open the Artifacts panel →")
                else:
                    await _finalize_artifact(active, run_id, tenant_id, artifact_md, websocket)
                    titles = _artifact_section_titles(active, artifact_md)
                    card = (f"✓ {active.replace('_', ' ').title()} ready"
                            + (f" — {titles}" if titles else "")
                            + " — open the Artifacts panel →")
                await _send(websocket, {"type": "stream_chunk", "content": card, "run_id": run_id})
                await _send(websocket, {"type": "stream_end", "run_id": run_id})
                await persist_turn(run_id, "agent", card, tenant_id=tenant_id or None,
                                   author_id=active)
            else:
                await persist_turn(run_id, "agent", reply, tenant_id=tenant_id or None,
                                   author_id=active)
                # A report stage's reply stayed chat-only (below the report length
                # threshold — e.g. a clarifying question), but generated files
                # (Security/Code Review/Deployment stage files) are written
                # independently of reply length, so still check for those.
                if active in _REPORT_STAGES:
                    await _capture_stage_files(active, run_id, tenant_id, project_id, websocket)

            # Requirements: mirror substantial replies (normalised Gherkin AC, doc
            # summaries) into the artifacts panel AND surface any generated docx
            # (BRD/MoM/Risk Register) as a "Generated files" file-tree — the reply
            # itself stays in chat (requirements never routes into `_REPORT_STAGES`).
            if active == "requirements" and not artifact_md:
                await _capture_requirements_artifacts(run_id, tenant_id, reply, websocket)
                await _capture_stage_files("requirements", run_id, tenant_id, project_id, websocket)

            # Development: persist the dev artifacts (repo/branch/code-tree pointer) and
            # push them to the panel on EVERY dev turn — not only on handoff. Otherwise the
            # code-tree is a live-only frontend synthetic that vanishes the moment the user
            # switches to another agent, and the rail can't show Development as done when
            # they switch back. Fail-soft; idempotent (fills the column once, re-emits live).
            if active == "development":
                await _capture_development_artifacts(run_id, tenant_id)
                await _ensure_dev_workspace_persisted(run_id, tenant_id)
                await _emit_stage_artifacts_ready(run_id, tenant_id, "development", websocket)

            # Enhancements (best-effort; each swallows its own errors). Pass the plain
            # reply (not report turns) so a numbered question becomes a clickable card.
            await _maybe_emit_choice_card(active, graph, run_id, tenant_id, project_id,
                                          shim_input, websocket,
                                          reply=("" if artifact_md else reply))

            # Stage handoff: the agent emitted HANDOFF:: (stage done + user confirmed).
            # Persist the artifact and either advance in-chat (driver can approve) or open
            # the approval gate for the owning role. When it advances, re-read active so
            # the same socket routes the NEXT turn into the new stage's agent.
            handoff = await _detect_handoff(graph, run_id)
            if handoff is not None:
                await _advance_or_gate(active, run_id, tenant_id, perms, project_id,
                                       graph, websocket)
                new_active, run_project_id = await _active_stage(run_id)
                active = new_active
            else:
                # No handoff. Self-gate ONLY when THIS turn actually produced the
                # stage's document (artifact_md). A plain chat/greeting turn on a
                # stage whose artifact already exists (e.g. re-entering Design to
                # discuss it, or any "hi") must NOT re-open the approval gate — that
                # was the "an approval popup appeared out of nowhere" bug. When the
                # agent does produce a doc this turn, the gate opens as normal HITL.
                if artifact_md:
                    await _maybe_detect_conversational_gate(active, run_id, tenant_id, perms, websocket)
                # Still re-emit an ALREADY-pending gate so a reopened run shows it.
                await _maybe_open_gate(active, run_id, tenant_id, perms, websocket)

    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 — final backstop; log + close cleanly
        logger.warning("copilot ws closed (run=%s): %s", run_id, exc)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
