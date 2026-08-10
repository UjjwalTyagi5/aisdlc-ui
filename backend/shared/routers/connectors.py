"""Connectors resource router.

Exposes CRUD-like operations over the connector registry, returning the
app-shaped Connector[] that the apps/web/ client expects (Zod Connector schema).

Symbol name: connectors_resource_router (not connectors_router — avoids collision
with the existing health router imported from config.connectors.router).

Routes (absolute paths — registered without a router prefix):
  GET   /connectors             — list all connectors (reshaped from health cache)
  GET   /connectors/{kind}      — single connector by kind
  POST  /connectors/{kind}/install         — OAuth start (returns {redirect_url}), connector:manage-gated
  POST  /connectors/{kind}/disconnect      — disconnect + delete tenant KV secret(s)
  GET   /connectors/{kind}/oauth/callback  — OAuth callback: verify CSRF state, exchange code, write KV

All routes are JWT-protected (NOT in _EXEMPT_PATHS): the connector list may reveal
connector configuration / org URLs, so authenticated callers only (T-M4-04).

Wave C changes (REQ-M7-20/21/22):
- install_connector: returns {redirect_url} pointing to provider OAuth authorize URL
- oauth_callback: verifies CSRF state, exchanges code, writes tokens to tenant KV
- disconnect_connector: deletes tenant KV secret(s) for the connector kind
- All connector CRUD calls now pass request.state.tenant_id (M7.1 follow-on fix)
- connector:manage required on install, callback, and disconnect (T-7.4-21)

T-7.4-16/17: CSRF state is HMAC-SHA256 bound to tenant_id; verify_oauth_state
raises ValueError → 400 on tamper or tenant-mismatch.
T-7.4-19: Tokens stored via store_secret (KV only; never ORM tables).
T-7.4-21: require_permission("connector:manage") on install + callback + disconnect.
T-7.4-22: OAuth Bearer token resolved ephemerally; never logged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import uuid as _uuid

from config.env import (
    AGENTIC_BASE_URL,
    JIRA_OAUTH_CLIENT_ID,
    JIRA_OAUTH_CLIENT_SECRET,
    SLACK_CLIENT_ID,
    SLACK_CLIENT_SECRET,
    GITHUB_APP_ID,
    GITHUB_OAUTH_CLIENT_SECRET,
)
from shared.auth.oauth_state import verify_oauth_state
from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.keyvault import load_secret, store_secret
from shared.models.orm import WorkspaceConnector
from shared.routers._schemas import ConnectorOut
from shared.services.oauth_service import build_oauth_start_url
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

connectors_resource_router = APIRouter()

# Known connector kinds that the catalog exposes (even if not yet in the health cache).
_KNOWN_KINDS = {
    "azure_devops",
    "jira",
    "github",
    "azure_repos",
    "github_actions",
    "slack",
    "sso_okta",
    "sso_entra",
}

# KV secret names written at callback per connector kind (D-04: disconnect = delete these)
_KIND_KV_SECRETS: Dict[str, List[str]] = {
    "jira": ["jira-access-token", "jira-refresh-token", "jira-cloud-id"],
    "github": ["github-access-token"],
    "slack": ["slack-bot-token"],
    "azure_repos": ["ado-pat"],
}

# secret_store refs written by the "Add credentials" form (POST /credentials).
# Disconnect must delete these too, or the per-tenant overlay keeps showing the
# connector as installed after the user disconnects (KV-only delete misses them).
_KIND_SECRET_STORE_REFS: Dict[str, List[str]] = {
    "azure_devops": ["ado-pat", "ado-org-url"],
    "jira": ["jira-url", "jira-email", "jira-api-token"],
    "github_actions": ["gha-pat", "gha-owner"],
}

# The single secret whose presence means "this tenant connected this kind". On
# disconnect it's tombstoned with DISCONNECTED_MARKER (authoritative); the overlay
# and auth_adapter key off it.
_KIND_PRIMARY_CREDENTIAL: Dict[str, str] = {
    "azure_devops": "ado-pat",
    "jira": "jira-api-token",
    "github_actions": "gha-pat",
}

# Atlassian token exchange endpoint
_JIRA_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
_JIRA_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

# GitHub token exchange endpoint
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"

# Slack token exchange endpoint
_SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"


def _build_connector_list(
    health_cache: dict, tenant_id: str
) -> List[ConnectorOut]:
    """Reshape app.state.connector_health_cache into a list[ConnectorOut]."""
    connectors: List[ConnectorOut] = []
    for key, value in health_cache.items():
        if key == "probed_at":
            continue
        if not isinstance(value, dict):
            continue
        try:
            connector = ConnectorOut.from_health_entry(key, value, tenant_id)
            connectors.append(connector)
        except Exception as exc:
            logger.warning("connectors_resource_router: failed to parse entry %r: %s", key, exc)
    return connectors


# Credential-based connectors: (kind, secret ref that proves it's configured, account ref).
_CREDENTIAL_CONNECTORS = [
    ("azure_devops", "ado-pat", "ado-org-url"),
    ("jira", "jira-api-token", "jira-url"),
    ("github_actions", "gha-pat", "gha-owner"),
]


async def _overlay_tenant_credentials(
    connectors: List[ConnectorOut], tenant_id: str
) -> List[ConnectorOut]:
    """Reflect per-tenant pasted credentials in the connector list.

    The base list comes from the GLOBAL (env-based) health probe, which can't see
    a tenant's stored PAT/API-token. For credential connectors where THIS tenant
    has a stored secret, mark the entry installed and run a live per-tenant health
    probe so the card shows the real state right after "Add credentials". Never
    raises — probe failures degrade to a "degraded" pill.
    """
    if not tenant_id:
        return connectors
    from datetime import datetime, timezone

    from shared.services import secret_store
    from config.connector_factory import get_connector_for_session

    by_kind = {c.kind: c for c in connectors}
    for kind, secret_ref, account_ref in _CREDENTIAL_CONNECTORS:
        try:
            secret = await secret_store.get_secret(tenant_id, secret_ref)
        except Exception:  # noqa: BLE001
            secret = None
        # The per-tenant secret is AUTHORITATIVE for credential connectors — it
        # overrides the tenant-blind global env probe (which would otherwise mark
        # azure_devops "installed/healthy" for every tenant whenever ADO_PAT env is
        # set, and keep a connector showing connected after disconnect).
        connected = bool(secret) and secret != secret_store.DISCONNECTED_MARKER
        existing = by_kind.get(kind)

        if not connected:
            # No tenant credential (never added, or tombstoned by disconnect) ⇒ force
            # disconnected so the card leaves "Installed" and offers "Add credentials".
            if existing is not None:
                existing.installed = False
                existing.health = "disconnected"
            continue

        health = "degraded"
        if kind == "github_actions":
            from shared.services.deployment_probe import probe_github_actions

            try:
                owner = await secret_store.get_secret(tenant_id, "gha-owner")
            except Exception:  # noqa: BLE001
                owner = None
            ok, _account, _err = await probe_github_actions(secret, owner)
            health = "healthy" if ok else "degraded"
        else:
            try:
                connector = await get_connector_for_session(kind=kind, tenant_id=tenant_id)
                hc = await connector.health_check()
                health = "healthy" if getattr(hc, "status", "") == "healthy" else "degraded"
            except Exception:  # noqa: BLE001
                health = "degraded"

        try:
            account = await secret_store.get_secret(tenant_id, account_ref)
        except Exception:  # noqa: BLE001
            account = None

        now_iso = datetime.now(timezone.utc).isoformat()
        if existing is not None:
            existing.installed = True
            existing.health = health
            existing.lastCheckedAt = now_iso
            if account:
                existing.account = account
        else:
            connectors.append(
                ConnectorOut(
                    id=kind,
                    tenantId=tenant_id,
                    kind=kind,
                    name=kind,
                    installed=True,
                    health=health,
                    capabilities=[],
                    lastCheckedAt=now_iso,
                    account=account,
                )
            )
    return connectors


# ── Workspace connector helpers ───────────────────────────────────────────────


async def _workspace_enabled_kinds(workspace_id: str, db: AsyncSession) -> set[str]:
    """Return the set of connector kinds enabled for this workspace."""
    try:
        wid = _uuid.UUID(workspace_id)
    except ValueError:
        return set()
    rows = (
        await db.execute(
            select(WorkspaceConnector.kind).where(
                WorkspaceConnector.workspace_id == wid,
                WorkspaceConnector.enabled == True,  # noqa: E712
            )
        )
    ).scalars().all()
    return set(rows)


async def _upsert_workspace_connector(workspace_id: str, tenant_id: str, kind: str, db: AsyncSession) -> None:
    """Upsert a workspace_connectors row (idempotent enable)."""
    try:
        wid = _uuid.UUID(workspace_id)
        tid = _uuid.UUID(tenant_id)
    except ValueError:
        return
    stmt = (
        pg_insert(WorkspaceConnector)
        .values(workspace_id=wid, tenant_id=tid, kind=kind, enabled=True)
        .on_conflict_do_update(
            constraint="uq_workspace_connector_kind",
            set_={"enabled": True},
        )
    )
    await db.execute(stmt)
    await db.commit()


async def _remove_workspace_connector(workspace_id: str, kind: str, db: AsyncSession) -> None:
    """Remove (or disable) a workspace_connectors row."""
    try:
        wid = _uuid.UUID(workspace_id)
    except ValueError:
        return
    await db.execute(
        delete(WorkspaceConnector).where(
            WorkspaceConnector.workspace_id == wid,
            WorkspaceConnector.kind == kind,
        )
    )
    await db.commit()


# ── CRUD list / get ───────────────────────────────────────────────────────────


@connectors_resource_router.get(
    "/connectors",
    response_model=List[ConnectorOut],
    dependencies=[Depends(require_permission("connector:view"))],
)
async def list_connectors(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Return a Connector[] reshaped from the connector-health cache (T-M4-04).

    Overlaid with this tenant's pasted credentials. When an X-Workspace-Id header
    is present, further filters to connectors enabled for that workspace — connectors
    with credentials but no workspace_connectors row appear as disconnected (not yet
    enabled for this workspace).
    """
    tenant_id = getattr(request.state, "tenant_id", "")
    workspace_id = request.headers.get("x-workspace-id", "")

    cache = getattr(request.app.state, "connector_health_cache", {})
    connectors = _build_connector_list(cache, tenant_id)
    connectors = await _overlay_tenant_credentials(connectors, tenant_id)

    if workspace_id:
        enabled_kinds = await _workspace_enabled_kinds(workspace_id, db)
        for c in connectors:
            if c.installed and c.kind not in enabled_kinds:
                # Credentials exist at tenant level but this workspace hasn't enabled it
                c.installed = False
                c.health = "disconnected"

    return connectors


@connectors_resource_router.get(
    "/connectors/{kind}",
    response_model=ConnectorOut,
    dependencies=[Depends(require_permission("connector:view"))],
)
async def get_connector(kind: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    """Return a single connector by kind, workspace-scoped when X-Workspace-Id present."""
    tenant_id = getattr(request.state, "tenant_id", "")
    workspace_id = request.headers.get("x-workspace-id", "")
    cache = getattr(request.app.state, "connector_health_cache", {})
    entry = cache.get(kind)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=f"Connector '{kind}' not found")
    connector = ConnectorOut.from_health_entry(kind, entry, tenant_id)
    if workspace_id and connector.installed:
        enabled_kinds = await _workspace_enabled_kinds(workspace_id, db)
        if kind not in enabled_kinds:
            connector.installed = False
            connector.health = "disconnected"
    return connector


# ── OAuth start ───────────────────────────────────────────────────────────────


@connectors_resource_router.post(
    "/connectors/{kind}/install",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def install_connector(kind: str, request: Request):
    """OAuth start — returns {redirect_url} pointing to the provider OAuth authorize URL.

    The browser should redirect to redirect_url to begin the OAuth consent flow.
    CSRF state is HMAC-SHA256 bound to the tenant_id (T-7.4-16/17).
    Returns 400 when tenant_id is absent from auth context (JWT middleware not wired).
    Returns 400 for unsupported connector kinds.
    """
    tenant_id = getattr(request.state, "tenant_id", "")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id missing from auth context")

    try:
        redirect_url = build_oauth_start_url(kind, tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("OAuth install started: kind=%r tenant=%r", kind, tenant_id)
    return {"redirect_url": redirect_url}


# ── Direct credential entry (PAT / API token) ─────────────────────────────────
# For providers where the user pastes a credential rather than running OAuth
# (Azure DevOps PAT, Jira email + API token). Credentials are written to the
# tenant secret store (Key Vault in prod, Fernet-encrypted DB in dev) and a live
# probe verifies them. The secret is never echoed back.

_CREDENTIAL_KINDS = {"azure_devops", "jira", "github_actions"}


class SetCredentialsIn(BaseModel):
    # Azure DevOps
    org_url: Optional[str] = None
    pat: Optional[str] = None
    # Jira (Basic auth)
    base_url: Optional[str] = None
    email: Optional[str] = None
    api_token: Optional[str] = None
    # GitHub Actions (reuses `pat`; owner/org optional)
    owner: Optional[str] = None


@connectors_resource_router.post(
    "/connectors/{kind}/credentials",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def set_connector_credentials(kind: str, body: SetCredentialsIn, request: Request, db: AsyncSession = Depends(get_db_session)):
    """Store a tenant's pasted connector credentials and verify them with a live probe.

    Returns {kind, status: "valid"|"invalid", account, error}. The credential is
    written to the tenant secret store and never returned.
    """
    tenant_id = getattr(request.state, "tenant_id", "")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id missing from auth context")
    if kind not in _CREDENTIAL_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Connector '{kind}' does not support pasted credentials. "
            f"Supported: {sorted(_CREDENTIAL_KINDS)}",
        )

    from shared.services import secret_store
    from config.connector_factory import get_connector_for_session

    try:
        if kind == "azure_devops":
            org_url = (body.org_url or "").strip()
            pat = (body.pat or "").strip()
            if not org_url or not pat:
                raise HTTPException(status_code=422, detail="Azure DevOps requires 'org_url' and 'pat'.")
            await secret_store.put_secret(tenant_id, "ado-org-url", org_url)
            await secret_store.put_secret(tenant_id, "ado-pat", pat)
            account = org_url
        elif kind == "github_actions":
            pat = (body.pat or "").strip()
            owner = (body.owner or "").strip()
            if not pat:
                raise HTTPException(status_code=422, detail="GitHub Actions requires 'pat'.")
            await secret_store.put_secret(tenant_id, "gha-pat", pat)
            if owner:
                await secret_store.put_secret(tenant_id, "gha-owner", owner)
            account = owner or None
        else:  # jira
            base_url = (body.base_url or "").strip()
            email = (body.email or "").strip()
            api_token = (body.api_token or "").strip()
            if not base_url or not email or not api_token:
                raise HTTPException(
                    status_code=422, detail="Jira requires 'base_url', 'email', and 'api_token'."
                )
            await secret_store.put_secret(tenant_id, "jira-url", base_url)
            await secret_store.put_secret(tenant_id, "jira-email", email)
            await secret_store.put_secret(tenant_id, "jira-api-token", api_token)
            account = base_url
    except HTTPException:
        raise
    except RuntimeError as exc:
        # secret_store with no KV and no SECRET_STORE_KEY — config problem, not user error.
        logger.error("connector credential store unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="Secret store is not configured. Set SECRET_STORE_KEY (dev) or AZURE_KEY_VAULT_URL (prod).",
        )

    # Live verify probe with the just-stored credentials.
    status = "invalid"
    error: Optional[str] = None
    if kind == "github_actions":
        from shared.services.deployment_probe import probe_github_actions

        ok, probe_account, probe_error = await probe_github_actions(
            (body.pat or "").strip(), (body.owner or "").strip() or None
        )
        status = "valid" if ok else "invalid"
        error = probe_error
        if probe_account:
            account = probe_account
    else:
        try:
            connector = await get_connector_for_session(kind=kind, tenant_id=tenant_id)
            health = await connector.health_check()
            status = "valid" if getattr(health, "status", "") == "healthy" else "invalid"
            error = getattr(health, "error", None)
        except Exception as exc:  # noqa: BLE001 — probe failure must not 500; report invalid.
            status = "invalid"
            error = type(exc).__name__

    # Enable connector for the active workspace (if present)
    workspace_id = request.headers.get("x-workspace-id", "")
    if workspace_id and status == "valid":
        try:
            await _upsert_workspace_connector(workspace_id, tenant_id, kind, db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_connector_credentials: failed to upsert workspace row: %s", exc)

    logger.info("connector credentials set: kind=%r tenant=%r status=%r error=%r", kind, tenant_id, status, error)
    return {"kind": kind, "status": status, "account": account, "error": error}


# ── OAuth callbacks ───────────────────────────────────────────────────────────


@connectors_resource_router.get(
    "/connectors/{kind}/oauth/callback",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def oauth_callback(
    kind: str,
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
):
    """OAuth callback: verify CSRF state, exchange code, write tokens to tenant KV.

    T-7.4-17: verify_oauth_state raises ValueError on tenant mismatch → 400.
    T-7.4-16: HMAC-SHA256 signature verified before any token exchange.
    T-7.4-19: Tokens written to KV only; never to ORM tables.
    T-7.4-21: connector:manage required.
    T-7.4-22: Tokens never logged.
    """
    tenant_id = getattr(request.state, "tenant_id", "")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id missing from auth context")

    # Verify CSRF state before touching the authorization code (T-7.4-16/17)
    try:
        verify_oauth_state(state, tenant_id)
    except ValueError as exc:
        logger.warning("OAuth callback CSRF state invalid: kind=%r %s", kind, type(exc).__name__)
        raise HTTPException(status_code=400, detail="Invalid OAuth state — possible CSRF")

    try:
        if kind == "jira":
            await _jira_oauth_exchange(code, state, tenant_id)
        elif kind == "github":
            await _github_oauth_exchange(code, state, tenant_id)
        elif kind == "slack":
            await _slack_oauth_exchange(code, state, tenant_id)
        elif kind == "azure_repos":
            await _azure_repos_oauth_exchange(code, state, tenant_id)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported connector kind: {kind!r}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("OAuth callback failed: kind=%r error=%s", kind, type(exc).__name__)
        raise HTTPException(status_code=502, detail="OAuth exchange failed")

    # Enable connector for the active workspace
    workspace_id = request.headers.get("x-workspace-id", "")
    if workspace_id:
        try:
            await _upsert_workspace_connector(workspace_id, tenant_id, kind, db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("oauth_callback: failed to upsert workspace connector row: %s", exc)

    logger.info("OAuth callback complete: kind=%r tenant=%r", kind, tenant_id)
    return {"status": "connected", "kind": kind}


# ── Disconnect ────────────────────────────────────────────────────────────────


@connectors_resource_router.post(
    "/connectors/{kind}/disconnect",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def disconnect_connector(kind: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    """Disconnect: delete tenant KV secret(s) for the connector kind.

    D-04: disconnect = delete KV secret (clean revocation). Connector calls
    then fail-closed (already the M7.1 behavior when creds are absent).
    T-7.4-21: connector:manage required.
    """
    tenant_id = getattr(request.state, "tenant_id", "")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id missing from auth context")

    secrets_to_delete = _KIND_KV_SECRETS.get(kind, [])
    for secret_name in secrets_to_delete:
        # KV delete: set to empty string signals deletion (Azure KV delete async;
        # overwriting with empty is the soft-disable path for simplicity).
        # A more robust implementation would call client.begin_delete_secret.
        # For now, load_secret returning empty/None means disconnected (fail-closed).
        try:
            await _delete_kv_secret(secret_name, tenant_id)
        except Exception as exc:
            logger.warning(
                "disconnect_connector: failed to delete KV secret %r for kind=%r: %s",
                secret_name,
                kind,
                type(exc).__name__,
            )

    # Authoritatively disconnect the pasted-credential connectors. The primary
    # credential ref is overwritten with the DISCONNECTED_MARKER tombstone — this
    # works even when the KV identity lacks secrets/delete (the bug that left
    # connectors showing "connected" after disconnect). The read paths
    # (auth_adapter, _overlay_tenant_credentials) treat the marker as no-credential.
    # The remaining (non-credential) refs are best-effort hard-deleted.
    from shared.services import secret_store
    primary_ref = _KIND_PRIMARY_CREDENTIAL.get(kind)
    for ref in _KIND_SECRET_STORE_REFS.get(kind, []):
        try:
            if ref == primary_ref:
                await secret_store.put_secret(tenant_id, ref, secret_store.DISCONNECTED_MARKER)
            else:
                await secret_store.delete_secret(tenant_id, ref)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "disconnect_connector: failed to clear secret-store ref %r for kind=%r: %s",
                ref,
                kind,
                type(exc).__name__,
            )

    # Remove workspace-level enablement for the active workspace
    workspace_id = request.headers.get("x-workspace-id", "")
    if workspace_id:
        try:
            await _remove_workspace_connector(workspace_id, kind, db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("disconnect_connector: failed to remove workspace row: %s", exc)

    cache = getattr(request.app.state, "connector_health_cache", {})
    entry = cache.get(kind)
    if not isinstance(entry, dict):
        entry = {"connector_name": kind, "status": "disconnected"}
    connector = ConnectorOut.from_health_entry(kind, entry, tenant_id)
    connector.installed = False
    logger.info("Connector disconnected: kind=%r tenant=%r", kind, tenant_id)
    return connector


# ── Internal exchange helpers ─────────────────────────────────────────────────


async def _jira_oauth_exchange(code: str, state: str, tenant_id: str) -> None:
    """Exchange Jira authorization code for tokens; write to tenant KV.

    Also fetches cloud_id from accessible-resources (Pitfall 6 — OAuth 3LO
    tokens must use api.atlassian.com/ex/jira/{cloud_id}, not the tenant URL).
    T-7.4-22: Token values never logged.
    """
    # Re-verify state here for callers that bypass the HTTP route (e.g. tests)
    verify_oauth_state(state, tenant_id)

    tokens = await _jira_token_exchange(code, tenant_id)
    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    cloud_id = await _jira_fetch_cloud_id(access_token)

    # Write to tenant-namespaced KV (T-7.4-19)
    await store_secret("jira-access-token", access_token, tenant_id=tenant_id)
    await store_secret("jira-refresh-token", refresh_token, tenant_id=tenant_id)
    await store_secret("jira-cloud-id", cloud_id, tenant_id=tenant_id)


async def _jira_token_exchange(code: str, tenant_id: str) -> Dict[str, Any]:
    """POST to Atlassian token endpoint; return {access_token, refresh_token}."""
    callback = f"{AGENTIC_BASE_URL.rstrip('/')}/connectors/jira/oauth/callback"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _JIRA_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": JIRA_OAUTH_CLIENT_ID,
                "client_secret": JIRA_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": callback,
            },
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def _jira_fetch_cloud_id(access_token: str) -> str:
    """Fetch the Atlassian cloud_id from accessible-resources.

    Required for OAuth 3LO API calls — must use
    api.atlassian.com/ex/jira/{cloud_id} as the base URL (Pitfall 6).
    T-7.4-22: access_token never logged.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _JIRA_RESOURCES_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        resp.raise_for_status()
        resources = resp.json()
    if not resources:
        raise ValueError("No accessible Atlassian resources for this token")
    return resources[0]["id"]


async def _github_oauth_exchange(code: str, state: str, tenant_id: str) -> None:
    """Exchange GitHub authorization code for an access token; write to tenant KV."""
    verify_oauth_state(state, tenant_id)

    tokens = await _github_token_exchange(code, tenant_id)
    access_token = tokens.get("access_token", "")
    await store_secret("github-access-token", access_token, tenant_id=tenant_id)


async def _github_token_exchange(code: str, tenant_id: str) -> Dict[str, Any]:
    """POST to GitHub token endpoint; return {access_token}."""
    callback = f"{AGENTIC_BASE_URL.rstrip('/')}/connectors/github/oauth/callback"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _GITHUB_TOKEN_URL,
            data={
                "client_id": GITHUB_APP_ID,
                "client_secret": GITHUB_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": callback,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def _slack_oauth_exchange(code: str, state: str, tenant_id: str) -> None:
    """Exchange Slack authorization code for a bot token; write to tenant KV."""
    verify_oauth_state(state, tenant_id)

    tokens = await _slack_token_exchange(code, tenant_id)
    bot_token = (tokens.get("access_token") or "")
    await store_secret("slack-bot-token", bot_token, tenant_id=tenant_id)


async def _slack_token_exchange(code: str, tenant_id: str) -> Dict[str, Any]:
    """POST to Slack oauth.v2.access endpoint; return token response."""
    callback = f"{AGENTIC_BASE_URL.rstrip('/')}/connectors/slack/oauth/callback"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _SLACK_TOKEN_URL,
            data={
                "client_id": SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": callback,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _azure_repos_oauth_exchange(code: str, state: str, tenant_id: str) -> None:
    """Exchange Azure Repos OAuth code for a PAT-equivalent token; write to tenant KV.

    [ASSUMED] A3 — exact implementation not verified against live Azure AD.
    Built + live-deferred per D-M7-02.
    """
    verify_oauth_state(state, tenant_id)
    # Store code as PAT placeholder; real implementation requires Azure AD token exchange
    # and a PAT creation API call. Live-deferred per D-M7-02.
    await store_secret("ado-pat", code, tenant_id=tenant_id)


async def _delete_kv_secret(secret_name: str, tenant_id: str) -> None:
    """Soft-delete a tenant KV secret so a disconnected connector's credential is
    actually removed from Key Vault (not just hidden in the UI).

    Uses the async client's `delete_secret`; after this, `load_secret` returns None
    so auth_adapter() falls through to env-var fallback or raises ValueError. Failures
    are logged (not raised) so a disconnect never hard-fails on a KV hiccup; the
    dominant residual cause would be the KV identity lacking secrets/delete.
    """
    import asyncio
    from azure.keyvault.secrets.aio import SecretClient
    from azure.identity.aio import DefaultAzureCredential
    from config.env import AZURE_KEY_VAULT_URL

    if not AZURE_KEY_VAULT_URL:
        logger.debug("AZURE_KEY_VAULT_URL not set — skipping KV secret deletion")
        return

    resolved_name = f"{tenant_id}-{secret_name}"
    credential = DefaultAzureCredential()
    try:
        async with SecretClient(vault_url=AZURE_KEY_VAULT_URL, credential=credential) as client:
            # The ASYNC SecretClient deletes via `delete_secret` (a coroutine). The sync
            # client's `begin_delete_secret` does not exist here and raised AttributeError
            # that the except below swallowed — so disconnect silently left the credential
            # in Key Vault. `delete_secret` performs the (soft-)delete so it's truly removed.
            await asyncio.wait_for(
                client.delete_secret(resolved_name),
                timeout=15,
            )
            logger.debug("Deleted KV secret: %r", resolved_name)
    except Exception as exc:
        logger.warning("KV secret deletion failed for %r: %s", resolved_name, type(exc).__name__)
    finally:
        await credential.close()
