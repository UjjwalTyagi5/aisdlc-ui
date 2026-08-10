"""Read-only Capabilities API — powers the per-agent Capabilities panel (D7).

Native tools are shown but NOT configurable. Curated tools are shown with their
default-on flag; enable/disable is written via the Agent Profile, not here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config.agent_registry import AGENT_REGISTRY
from shared.authz.dependency import require_permission
from shared.capabilities import native_tags, system_catalog
from shared.db import get_db_session
from shared.routers.projects import _get_or_404
from shared.services import agent_profile_store, mcp_registry

capabilities_router = APIRouter(
    prefix="/capabilities",
    dependencies=[Depends(require_permission("artifact:view"))],
)


def build_agent_capability_view() -> list[dict]:
    out: list[dict] = []
    for agent_id, defn in AGENT_REGISTRY.items():
        native = [
            {"tool": tool_name, "capability": cap}
            for tool_name, value in native_tags.NATIVE_TAGS.get(agent_id, {}).items()
            for cap in native_tags._as_caps(value)
        ]
        curated = [
            {
                "key": ct.key,
                "display_name": ct.display_name,
                "capability": ct.capability,
                "default_on": agent_id in ct.default_on_agents,
            }
            for ct in system_catalog.SYSTEM_CATALOG
            if agent_id in ct.default_on_agents
        ]
        out.append({
            "agent_id": agent_id,
            "name": defn.name,
            "required": list(defn.required_capabilities),
            "optional": list(defn.optional_capabilities),
            "native": native,
            "curated": curated,
        })
    return out


@capabilities_router.get("/agents")
async def list_agent_capabilities():
    return build_agent_capability_view()


# ── Project-scoped view (powers the Agents & Capabilities panel) ───────────────
#
# Enriches the static view with per-project state: curated on/off (from the
# resolved Agent Profile) and assigned BYO servers (from Project.mcp_servers,
# resolved against the tenant's MCP registry).

def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


@capabilities_router.get("/projects/{project_id}/agents")
async def list_project_agent_capabilities(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    tenant_id = _tenant_id(request)
    project = await _get_or_404(db, project_id, tenant_id)

    base = build_agent_capability_view()
    agent_ids = [a["agent_id"] for a in base]
    disabled_map = await agent_profile_store.project_disabled_curated(
        tenant_id, str(project.id), agent_ids
    )
    assignment: dict = project.mcp_servers or {}
    servers = await mcp_registry.list_servers(
        tenant_id, created_by=_user_id(request), active_only=True
    )
    by_id = {s["id"]: s for s in servers}

    for a in base:
        disabled = set(disabled_map.get(a["agent_id"], []))
        for ct in a["curated"]:
            ct["enabled"] = ct["key"] not in disabled
        assigned_ids = assignment.get(a["agent_id"]) or []
        a["assigned_byo"] = [
            {
                "id": sid,
                "server_name": (by_id.get(sid) or {}).get("server_name", sid),
                "capabilities": (by_id.get(sid) or {}).get("capabilities", []),
            }
            for sid in assigned_ids
        ]

    available_byo = [
        {
            "id": s["id"],
            "server_name": s["server_name"],
            "transport": s["transport"],
            "capabilities": s.get("capabilities", []),
        }
        for s in servers
    ]
    return {"agents": base, "available_byo": available_byo}


class CuratedToggleIn(BaseModel):
    disabled: list[str]


@capabilities_router.put(
    "/projects/{project_id}/agents/{agent_id}/curated",
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def set_project_curated(
    project_id: str,
    agent_id: str,
    body: CuratedToggleIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    if agent_id not in AGENT_REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown agent")
    valid = {
        ct.key
        for ct in system_catalog.SYSTEM_CATALOG
        if agent_id in ct.default_on_agents
    }
    unknown = [k for k in body.disabled if k not in valid]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown curated tool(s): {', '.join(unknown)}"
        )
    tenant_id = _tenant_id(request)
    project = await _get_or_404(db, project_id, tenant_id)
    saved = await agent_profile_store.set_project_disabled_curated(
        tenant_id, agent_id, str(project.id), body.disabled, _user_id(request)
    )
    return {"agent_id": agent_id, "disabled": saved}
