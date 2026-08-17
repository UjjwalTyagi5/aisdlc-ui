"""MCP server registry service — DB CRUD + secret handling + config resolution.

Sits between the REST router / stage runners and the mcp_servers table.
Credentials (env vars / HTTP headers) are stored in the tenant-scoped secret store
under stable refs; only the refs live on the row. resolve_server_configs() decrypts
them back into plain ServerConfig dicts for shared.services.mcp_client.

All DB access goes through tenant-scoped sessions (RLS-enforced).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from sqlalchemy import select

from shared.capabilities import taxonomy
from shared.db import get_db_session_for_tenant
from shared.models.orm import McpServer
from shared.services import secret_store
from shared.services.mcp_client import ServerConfig


def validate_byo_capabilities(caps: "list[str] | None") -> None:
    """Reject unknown or native-only capability tags on a BYO server (D3, D9)."""
    if not caps:
        return
    taxonomy.assert_valid(caps)
    native_only = sorted([c for c in caps if taxonomy.is_native_only(c)])
    if native_only:
        raise ValueError(
            f"These capabilities are native-only and cannot be provided by a BYO server: "
            f"{', '.join(native_only)}"
        )


def _env_ref(server_id: str) -> str:
    return f"mcp/{server_id}/env"


def _headers_ref(server_id: str) -> str:
    return f"mcp/{server_id}/headers"


def to_public(row: McpServer) -> dict[str, Any]:
    """Serialize a row for the API — never exposes secret refs or secret values."""
    return {
        "id": str(row.id),
        "server_name": row.server_name,
        "description": row.description,
        "transport": row.transport,
        "url": row.url,
        "command": row.command,
        "args": row.args or [],
        "has_env_vars": bool(row.env_vars_secret_ref),
        "has_headers": bool(row.headers_secret_ref),
        "is_active": row.is_active,
        "allowed_stages": row.allowed_stages,
        "tools_snapshot": row.tools_snapshot or [],
        "capabilities": row.capabilities or [],
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_servers(
    tenant_id: str, created_by: Optional[str] = None, active_only: bool = False
) -> list[dict]:
    """List servers. created_by enforces per-user (creator-only) visibility."""
    async with get_db_session_for_tenant(tenant_id) as session:
        stmt = select(McpServer)
        if created_by:
            stmt = stmt.where(McpServer.created_by == created_by)
        if active_only:
            stmt = stmt.where(McpServer.is_active.is_(True))
        rows = (await session.execute(stmt)).scalars().all()
        return [to_public(r) for r in rows]


async def _get_owned(session, server_id: str, created_by: Optional[str]) -> Optional[McpServer]:
    row = await session.get(McpServer, uuid.UUID(server_id))
    if row is None:
        return None
    if created_by and row.created_by != created_by:
        return None  # not the owner — invisible (creator-only isolation)
    return row


async def get_server(
    tenant_id: str, server_id: str, created_by: Optional[str] = None
) -> Optional[dict]:
    async with get_db_session_for_tenant(tenant_id) as session:
        row = await _get_owned(session, server_id, created_by)
        return to_public(row) if row else None


async def create_server(tenant_id: str, created_by: str, data: dict) -> dict:
    validate_byo_capabilities(data.get("capabilities"))
    server_id = uuid.uuid4()
    env_vars = data.get("env_vars") or None
    headers = data.get("headers") or None

    env_ref = _env_ref(str(server_id)) if env_vars else None
    headers_ref = _headers_ref(str(server_id)) if headers else None
    if env_vars:
        await secret_store.put_secret(tenant_id, env_ref, json.dumps(env_vars))
    if headers:
        await secret_store.put_secret(tenant_id, headers_ref, json.dumps(headers))

    async with get_db_session_for_tenant(tenant_id) as session:
        row = McpServer(
            id=server_id,
            tenant_id=uuid.UUID(tenant_id),
            server_name=data["server_name"],
            description=data.get("description"),
            transport=data["transport"],
            url=data.get("url"),
            command=data.get("command"),
            args=data.get("args"),
            env_vars_secret_ref=env_ref,
            headers_secret_ref=headers_ref,
            is_active=data.get("is_active", True),
            allowed_stages=data.get("allowed_stages"),
            capabilities=data.get("capabilities"),
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        return to_public(row)


async def update_server(
    tenant_id: str, server_id: str, data: dict, created_by: Optional[str] = None
) -> Optional[dict]:
    validate_byo_capabilities(data.get("capabilities"))
    # Rewrite secrets outside the DB session (KV/Fernet calls are independent).
    if "env_vars" in data:
        ref = _env_ref(server_id)
        if data.get("env_vars"):
            await secret_store.put_secret(tenant_id, ref, json.dumps(data["env_vars"]))
        else:
            await secret_store.delete_secret(tenant_id, ref)
    if "headers" in data:
        ref = _headers_ref(server_id)
        if data.get("headers"):
            await secret_store.put_secret(tenant_id, ref, json.dumps(data["headers"]))
        else:
            await secret_store.delete_secret(tenant_id, ref)

    async with get_db_session_for_tenant(tenant_id) as session:
        row = await _get_owned(session, server_id, created_by)
        if row is None:
            return None
        for field in ("server_name", "description", "transport", "url", "command", "args",
                      "is_active", "allowed_stages", "capabilities"):
            if field in data:
                setattr(row, field, data[field])
        if "env_vars" in data:
            row.env_vars_secret_ref = _env_ref(server_id) if data.get("env_vars") else None
        if "headers" in data:
            row.headers_secret_ref = _headers_ref(server_id) if data.get("headers") else None
        await session.flush()
        return to_public(row)


async def delete_server(
    tenant_id: str, server_id: str, created_by: Optional[str] = None
) -> bool:
    async with get_db_session_for_tenant(tenant_id) as session:
        row = await _get_owned(session, server_id, created_by)
        if row is None:
            return False
        await session.delete(row)
    # Best-effort secret cleanup after the row is gone.
    for ref in (_env_ref(server_id), _headers_ref(server_id)):
        try:
            await secret_store.delete_secret(tenant_id, ref)
        except Exception:
            pass
    return True


async def save_tools_snapshot(
    tenant_id: str, server_id: str, tools: list[dict], created_by: Optional[str] = None
) -> None:
    async with get_db_session_for_tenant(tenant_id) as session:
        row = await _get_owned(session, server_id, created_by)
        if row is not None:
            row.tools_snapshot = tools


async def _decrypt_json(tenant_id: str, ref: Optional[str]) -> Optional[dict]:
    if not ref:
        return None
    raw = await secret_store.get_secret(tenant_id, ref)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _rows_to_configs(tenant_id: str, rows: list[McpServer], agent_id: Optional[str]) -> list[ServerConfig]:
    configs: list[ServerConfig] = []
    for row in rows:
        if agent_id and row.allowed_stages and agent_id not in row.allowed_stages:
            continue
        cfg: ServerConfig = {
            "id": str(row.id),
            "name": row.server_name,
            "transport": row.transport,
            "url": row.url,
            "command": row.command,
            "args": row.args or [],
            "capabilities": row.capabilities or [],
        }
        cfg["env"] = await _decrypt_json(tenant_id, row.env_vars_secret_ref)
        cfg["headers"] = await _decrypt_json(tenant_id, row.headers_secret_ref)
        configs.append(cfg)
    return configs


async def resolve_server_configs(
    tenant_id: str,
    server_ids: list[str],
    agent_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> list[ServerConfig]:
    """Load active servers by id and decrypt credentials.

    Used at run time with the project's per-stage selection (server_ids), and by the
    probe endpoint (with created_by to enforce ownership). agent_id is accepted for
    backward compatibility; stage gating now lives in the project mapping, not here.
    """
    if not server_ids:
        return []
    ids = {uuid.UUID(s) for s in server_ids}
    async with get_db_session_for_tenant(tenant_id) as session:
        stmt = select(McpServer).where(McpServer.id.in_(ids), McpServer.is_active.is_(True))
        if created_by:
            stmt = stmt.where(McpServer.created_by == created_by)
        rows = list((await session.execute(stmt)).scalars().all())
    return await _rows_to_configs(tenant_id, rows, agent_id)
