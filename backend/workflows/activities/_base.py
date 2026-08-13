"""Shared idempotency helper for stage runners (D-05).

Public API:
  check_existing_artifact(run_id, agent_type, version)
      -> returns the stored artifact dict when the runs.<column> JSONB field exists
         AND its embedded "version" key matches the requested version; else None.
      -> uses a fresh get_db_session_for_tenant/superuser context per call (Assumption A3 —
         avoids stale ORM object across stage retries).

  write_and_notify   — re-exported from artifact_service for a single import
                       surface in all activity wrappers.

Column mapping (derived from AGENT_REGISTRY):
  Built automatically from each AgentDefinition.output_artifact field.
  Mirrors artifact_service._COLUMN_MAP at runtime.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy import select

from config.agent_registry import AGENT_REGISTRY
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.eval.service import eval_service
from shared.models.orm import Run
from shared.services.artifact_service import write_and_notify as _persist_and_notify

logger = logging.getLogger(__name__)

__all__ = [
    "check_existing_artifact",
    "write_and_notify",
    "detect_clarification_need",
    "mcp_tools_for_stage",
    "resolved_tools_for_stage",
    "ResolvedRun",
    "stage_connector_kind",
]

# Derived from AGENT_REGISTRY — adding a new agent with output_artifact
# automatically extends this map. No hardcoded entries to maintain.
_COLUMN_MAP = {
    agent_id: defn.output_artifact
    for agent_id, defn in AGENT_REGISTRY.items()
    if defn.output_artifact
}


async def check_existing_artifact(
    run_id: str,
    agent_type: str,
    version: int,
    tenant_id: Optional[str] = None,
) -> Optional[dict]:
    """Return the stored artifact dict when it already exists for this version.

    Uses a fresh session per call — never a cached session — so retries
    always see the committed Postgres state rather than a stale in-memory snapshot
    (Assumption A3 from M5-RESEARCH.md).

    tenant_id: when provided, uses get_db_session_for_tenant (D-04). When absent,
    uses get_db_session_superuser() because this is a system-internal idempotency
    read across the retry boundary — no user tenant is in scope at the
    read site (D-05 — cross-retry idempotency read is a justified system op).
    Callers that have SDLCWorkflowInput.tenant_id should pass it here to keep the
    read tenant-scoped once FORCE RLS is active (Plan 04).

    Returns None when:
    - The run_id does not exist in the runs table.
    - The JSONB column for agent_type is NULL (no artifact persisted yet).
    - The stored artifact's "version" field does not match the requested version
      (a newer version was requested; re-run the agent).

    Raises ValueError for unknown agent_type values (same contract as
    artifact_service._COLUMN_MAP to catch typos at activity-write time).
    """
    column_name = _COLUMN_MAP.get(agent_type)
    if not column_name:
        raise ValueError(
            f"Unknown agent_type: {agent_type!r}. Must be one of {list(_COLUMN_MAP)}"
        )

    # D-04: tenant-scoped when tenant_id known; D-05: superuser for cross-retry idempotency.
    _ctx = get_db_session_for_tenant(tenant_id) if tenant_id else get_db_session_superuser()
    async with _ctx as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()

        if run is None:
            return None

        existing: Optional[dict] = getattr(run, column_name, None)
        if not existing:
            return None

        if existing.get("version") == version:
            return existing

    return None


async def write_and_notify(
    run_id: str,
    artifact_type: str,
    artifact_data: dict,
    *,
    tenant_id: Optional[str] = None,
) -> None:
    """Persist artifact + publish notification, then emit one EvalRecord (REQ-M9-11).

    This is the single shared call site used by all 4 artifact-producing activity
    wrappers (requirements, design, development, testing) — wrapping it locally here
    (rather than editing artifact_service.write_and_notify itself) keeps the eval hook
    colocated with the other shared activity-wrapper helper (check_existing_artifact)
    and avoids touching artifact_service's persist/notify contract.

    Why here and not the M8 AuditCallbackHandler: the callback fires per-tool/
    per-LLM-call (too granular — REQ-M9-11 wants exactly ONE record per run after
    completion), whereas write_and_notify fires exactly once per agent run, after
    the artifact is persisted, and already carries run_id + tenant_id + artifact_type
    — one edit here covers every agent with no per-agent changes.

    The eval emit is fire-and-forget (EvalRecordService.emit never raises, T-9.3-05)
    and is called AFTER persist+publish succeed, so an eval failure can never prevent
    or roll back the artifact write.
    """
    await _persist_and_notify(run_id, artifact_type, artifact_data, tenant_id=tenant_id)
    await eval_service.emit(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_type=artifact_type,
        artifact_data=artifact_data,
    )


def stage_connector_kind(input, agent_id: str, default: str = "azure_devops") -> str:
    """Return the connector kind to inject for this stage.

    Reads SDLCWorkflowInput.connectors[agent_id]; uses the first selected kind (the
    single active-connector contextvar supports one at a time — multi-connector tools
    are a follow-up). Falls back to `default` (azure_devops) when the project made no
    per-stage selection, preserving today's behavior for existing projects.
    """
    selection = getattr(input, "connectors", None)
    kinds = selection.get(agent_id) if isinstance(selection, dict) else None
    if kinds:
        return kinds[0]
    return default


@asynccontextmanager
async def mcp_tools_for_stage(input, agent_id: str) -> AsyncIterator[None]:
    """Resolve + inject this stage's MCP tools for the duration of graph.ainvoke.

    Reads SDLCWorkflowInput.mcp_servers[agent_id], loads the matching tenant's MCP
    servers as LangChain tools, and sets them on the mcp_runtime contextvar so the
    agent node binds them and the dynamic tool node dispatches them. Clears on exit
    so tools never survive the activity (mirrors set_connector/clear_connector).

    Disabled or empty selection → no-op. Any resolution/connection failure degrades
    gracefully (logged, no tools) so MCP can never fail an otherwise-healthy stage.

    Delegates to the shared mcp_tools_scope so the pipeline and interactive-chat
    surfaces resolve/inject identically (the only difference is where the server
    ids come from — here, SDLCWorkflowInput.mcp_servers[agent_id]).
    """
    from shared.services.mcp_injection import mcp_tools_scope  # noqa: PLC0415

    selection = getattr(input, "mcp_servers", None)
    server_ids = selection.get(agent_id) if isinstance(selection, dict) else None
    async with mcp_tools_scope(getattr(input, "tenant_id", None), server_ids, agent_id):
        yield


from dataclasses import dataclass as _dataclass  # noqa: E402 — placed after mcp_tools_for_stage


@_dataclass
class ResolvedRun:
    """Output of resolved_tools_for_stage — the fully resolved, profile-injected run context."""
    bound_tools: list
    prompt: str
    gap: list
    resolved: "ResolvedToolset"  # noqa: F821 — lazy type annotation


@asynccontextmanager
async def resolved_tools_for_stage(input, agent_id: str, base_tools: list, base_prompt: str):
    """Build the resolved, capability-deduped toolset + profile-injected prompt for a run.

    Nests inside mcp_tools_for_stage so BYO tools are loaded before gathering, then
    re-sets the contextvar to only the precedence-resolved winners. Caller binds
    ResolvedRun.bound_tools and blocks on ResolvedRun.gap (run-start enforcement, DP6).

    Degrades gracefully: profile lookup failure returns empty ResolvedProfile;
    server config failure returns empty server_rows; any missing curated tool
    produces a provider with tool=None (still counts for gap, binds nothing).
    """
    from shared.capabilities.providers import gather_native, gather_curated, gather_byo  # noqa: PLC0415
    from shared.capabilities.resolution import resolve  # noqa: PLC0415
    from shared.capabilities.preflight import preflight_agent  # noqa: PLC0415
    from shared.services.agent_profile_store import resolve_profile, inject_prompt  # noqa: PLC0415
    from shared.services import mcp_registry  # noqa: PLC0415
    from shared.tools.mcp_runtime import set_mcp_tools, clear_mcp_tools, get_mcp_tools  # noqa: PLC0415

    tenant_id = getattr(input, "tenant_id", None)
    workspace_id = getattr(input, "workspace_id", None)  # None for SDLCWorkflowInput — resolve_profile handles None
    project_id = getattr(input, "project_id", None)

    # 1. BYO servers chosen for this stage (existing path), load their tools.
    async with mcp_tools_for_stage(input, agent_id):
        byo_tools = get_mcp_tools()
        server_rows = []
        selection = getattr(input, "mcp_servers", None)
        server_ids = selection.get(agent_id) if isinstance(selection, dict) else None
        if tenant_id and server_ids:
            try:
                server_rows = await mcp_registry.resolve_server_configs(tenant_id, server_ids, agent_id)
            except Exception:
                server_rows = []

        # 2. Resolve profile (gives disabled_curated + primary_overrides).
        profile = await resolve_profile(tenant_id, agent_id, workspace_id, project_id) if tenant_id else None
        disabled = profile.disabled_curated if profile else set()
        overrides = profile.primary_overrides if profile else {}

        # 3. Gather providers across tiers. Curated managed-server tools are loaded
        #    elsewhere (Tier-1 hosting, D14); in dev curated tool objects are None.
        providers = []
        providers += gather_native(agent_id, base_tools)
        providers += gather_curated(agent_id, disabled, curated_tools_by_key={})
        providers += gather_byo(server_rows, byo_tools)

        # 4. Resolve + de-dup, compute gap, inject prompt.
        resolved = resolve(providers, primary_overrides=overrides)
        gap = preflight_agent(agent_id, resolved)
        prompt = inject_prompt(base_prompt, profile) if profile else base_prompt

        # Re-set the contextvar to non-native resolved winners only.
        # Native tools already come from the base `tools` list in the agent bind
        # (e.g. `orch.bind_tools(tools + get_mcp_tools())`); re-adding them here
        # would double-bind and defeat de-duplication (C-2 decision).
        set_mcp_tools([p.tool for p in resolved.active.values() if p.tier != "native" and p.tool is not None])
        try:
            yield ResolvedRun(bound_tools=resolved.tools, prompt=prompt, gap=gap, resolved=resolved)
        finally:
            clear_mcp_tools()


def detect_clarification_need(
    final_state: dict,
    run_id: str,
    agent_type: str,
) -> Optional["ClarificationRequest"]:  # noqa: F821 — lazy import below
    """Return a ClarificationRequest if the agent ended its turn with questions.

    Pure post-hoc inspection of the `final_state` dict already returned by
    `graph.ainvoke(...)` — zero agent prompt/tool/graph changes (D-M10-02).

    Heuristic (REQ-M10-01): the last message's content ends with "?" or
    contains the "STOP HERE" prompt sentinel (planning.py:1053/1063/1075/1091)
    AND no typed artifact key is present in final_state.

    thread_id is set ONLY from the passed run_id argument — the pipeline
    run_id namespace, never a chat session_id (resolved A3 / RESEARCH
    Pitfall 7 / T-10.1-01).
    """
    from shared.models.workflow_models import ClarificationRequest  # noqa: PLC0415

    messages = final_state.get("messages", [])
    if not messages:
        return None

    last = messages[-1]
    content = getattr(last, "content", "") or ""
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )

    artifact_keys = (
        "requirements_payload",
        "design_artifacts",
        "development_artifacts",
        "testing_artifacts",
    )
    artifact_present = any(final_state.get(k) for k in artifact_keys)

    content_stripped = content.strip()
    has_question = content_stripped.endswith("?") or "STOP HERE" in content

    if has_question and not artifact_present:
        return ClarificationRequest(
            questions=[content_stripped],
            thread_id=run_id,
            agent_type=agent_type,
            phase=agent_type,
        )
    return None
