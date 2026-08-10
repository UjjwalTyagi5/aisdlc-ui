"""MCP server registry API — register/manage MCP servers consumed as agent tools.

Reuses the connector governance tier: connector:manage for writes, connector:view
for reads (MCP servers are tenant integrations, same trust boundary). Secret values
(env vars / headers) are accepted on write, stored in the secret store, and never
returned. The whole router is mounted only when MCP_ENABLED (see process_api).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shared.authz.dependency import require_permission
from shared.services import mcp_client, mcp_registry

mcp_registry_router = APIRouter(prefix="/mcp/registry", tags=["mcp"])

Transport = Literal["streamable_http", "sse", "stdio"]


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


class McpServerIn(BaseModel):
    server_name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    transport: Transport
    url: Optional[str] = Field(default=None, max_length=1024)
    command: Optional[str] = Field(default=None, max_length=512)
    args: Optional[list[str]] = None
    env_vars: Optional[dict[str, str]] = None
    headers: Optional[dict[str, str]] = None
    is_active: bool = True
    allowed_stages: Optional[list[str]] = None
    capabilities: Optional[list[str]] = None


class McpServerUpdate(BaseModel):
    server_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    transport: Optional[Transport] = None
    url: Optional[str] = Field(default=None, max_length=1024)
    command: Optional[str] = Field(default=None, max_length=512)
    args: Optional[list[str]] = None
    env_vars: Optional[dict[str, str]] = None
    headers: Optional[dict[str, str]] = None
    is_active: Optional[bool] = None
    allowed_stages: Optional[list[str]] = None
    capabilities: Optional[list[str]] = None


class McpTestConnectionIn(BaseModel):
    server_name: str = Field(default="probe", max_length=128)
    transport: Transport
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    env_vars: Optional[dict[str, str]] = None
    headers: Optional[dict[str, str]] = None


@mcp_registry_router.get("", dependencies=[Depends(require_permission("connector:view"))])
async def list_servers(request: Request, active_only: bool = False):
    # Creator-only visibility: a user sees only the servers they registered.
    return await mcp_registry.list_servers(
        _tenant_id(request), created_by=_user_id(request), active_only=active_only
    )


@mcp_registry_router.get("/{server_id}", dependencies=[Depends(require_permission("connector:view"))])
async def get_server(request: Request, server_id: str):
    row = await mcp_registry.get_server(_tenant_id(request), server_id, created_by=_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return row


@mcp_registry_router.post("", dependencies=[Depends(require_permission("connector:manage"))])
async def create_server(request: Request, body: McpServerIn):
    return await mcp_registry.create_server(
        _tenant_id(request), _user_id(request), body.model_dump(exclude_none=False)
    )


@mcp_registry_router.put("/{server_id}", dependencies=[Depends(require_permission("connector:manage"))])
async def update_server(request: Request, server_id: str, body: McpServerUpdate):
    row = await mcp_registry.update_server(
        _tenant_id(request), server_id, body.model_dump(exclude_unset=True),
        created_by=_user_id(request),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return row


@mcp_registry_router.delete("/{server_id}", dependencies=[Depends(require_permission("connector:manage"))])
async def delete_server(request: Request, server_id: str):
    ok = await mcp_registry.delete_server(
        _tenant_id(request), server_id, created_by=_user_id(request)
    )
    if not ok:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"deleted": True}


@mcp_registry_router.post("/test-connection", dependencies=[Depends(require_permission("connector:manage"))])
async def test_connection(request: Request, body: McpTestConnectionIn):
    """Connect to an unsaved config and list its tools (register-time validation)."""
    cfg = {
        "name": body.server_name,
        "transport": body.transport,
        "url": body.url,
        "command": body.command,
        "args": body.args or [],
        "headers": body.headers,
        "env": body.env_vars,
    }
    return await mcp_client.test_connection(cfg)  # type: ignore[arg-type]


@mcp_registry_router.post("/{server_id}/probe", dependencies=[Depends(require_permission("connector:manage"))])
async def probe_server(request: Request, server_id: str):
    """Connect to a saved server and refresh its cached tools_snapshot."""
    tenant_id = _tenant_id(request)
    user_id = _user_id(request)
    configs = await mcp_registry.resolve_server_configs(
        tenant_id, [server_id], created_by=user_id
    )
    if not configs:
        raise HTTPException(status_code=404, detail="MCP server not found or inactive")
    result = await mcp_client.test_connection(configs[0])
    if result.get("ok"):
        await mcp_registry.save_tools_snapshot(tenant_id, server_id, result["tools"], created_by=user_id)
    return result
