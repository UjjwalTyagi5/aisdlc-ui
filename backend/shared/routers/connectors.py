"""Connectors resource router.

Exposes CRUD-like operations over the connector registry, returning the
app-shaped Connector[] that the apps/web/ client expects (Zod Connector schema).

Symbol name: connectors_resource_router (not connectors_router — avoids collision
with the existing health router imported from config.connectors.router).

Routes (absolute paths — registered without a router prefix):
  GET   /connectors                    — list all connectors (reshaped from health cache)
  GET   /connectors/{kind}             — single connector by kind
  POST  /connectors/{kind}/credentials — paste a credential, verify it, store per tenant
  POST  /connectors/{kind}/disconnect  — disconnect + delete tenant KV secret(s)

All routes are JWT-protected (NOT in _EXEMPT_PATHS): the connector list may reveal
connector configuration / org URLs, so authenticated callers only (T-M4-04).

REMOVED: /connectors/{kind}/install (OAuth start) and /connectors/{kind}/oauth/callback.
Both existed to run a 3LO flow against ONE OAuth app registered by the platform, whose
client_id and client_secret therefore had to live in process configuration and be held
on every tenant's behalf. Every provider they covered — Jira, GitHub, Slack, Figma,
Azure Repos — is reachable with a credential the tenant pastes itself and which is
stored only in that tenant's secret store. Deleting the flow removes the last reason
for the platform to hold a connector credential at all.

T-7.4-19: credentials stored via store_secret (KV only; never ORM tables).
T-7.4-21: require_permission("connector:manage") on credentials + disconnect.
T-7.4-22: credentials resolved ephemerally; never logged.
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
)
from shared.authz.connector_grants import granted_target_refs
from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.keyvault import load_secret, store_secret
from shared.models.orm import WorkspaceConnector
from shared.routers._schemas import ConnectorOut
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

connectors_resource_router = APIRouter()

# Connector kinds the API ACCEPTS — the validation set. Wider than the catalog
# below: it keeps kinds that still have live plumbing (azure_repos webhooks and
# normalizers) or belong to another surface entirely (the two SSO kinds).
_KNOWN_KINDS = {
    "azure_devops",
    "jira",
    "github",
    "azure_repos",
    "azure_pipelines",
    "github_actions",
    "slack",
    "ms_teams",
    "sharepoint",
    "figma",
    "confluence",
    "sonarqube",
    "sso_okta",
    "sso_entra",
}

# Connector kinds the product PRESENTS — one tile per kind on the Integrations
# page, and the denominator behind the dashboard's "N available". Deliberately
# narrower than _KNOWN_KINDS: azure_repos is folded into the consolidated
# azure_devops tile (one credential covers boards, repos and CI/CD), and the two
# SSO kinds are identity plumbing, not something a project connects. Counting the
# accept-set here is what made the dashboard say 11 while the page showed 8.
# Keep in step with CATEGORIES in frontend/app/(app)/integrations/page.tsx.
_CATALOG_KINDS = {
    "jira",
    "azure_devops",
    "github",
    "github_actions",
    "slack",
    "ms_teams",
    "sharepoint",
    "figma",
    "confluence",
    "sonarqube",
}

# KV secret names written at callback per connector kind (D-04: disconnect = delete these)
_KIND_KV_SECRETS: Dict[str, List[str]] = {
    "jira": ["jira-access-token", "jira-refresh-token", "jira-cloud-id"],
    "github": ["github-access-token"],
    "slack": ["slack-bot-token"],
    "azure_repos": ["ado-pat"],
    "azure_pipelines": ["ado-pat"],
    "figma": ["figma-access-token"],
}

# secret_store refs written by the "Add credentials" form (POST /credentials).
# Disconnect must delete these too, or the per-tenant overlay keeps showing the
# connector as installed after the user disconnects (KV-only delete misses them).
_KIND_SECRET_STORE_REFS: Dict[str, List[str]] = {
    "azure_devops": ["ado-pat", "ado-org-url"],
    "jira": ["jira-url", "jira-email", "jira-api-token"],
    "confluence": ["confluence-url", "confluence-email", "confluence-api-token", "confluence-space-key"],
    "sonarqube": ["sonarqube-url", "sonarqube-token"],
    "github_actions": ["gha-pat", "gha-owner"],
    # ms_teams and sharepoint SHARE one Entra app registration (msgraph-*), so those
    # three refs are deliberately absent here — see _MSGRAPH_SHARED_REFS below. Only
    # the per-kind marker and routing config are owned by each kind.
    "ms_teams": [
        "msteams-connected",
        "msteams-team-id",
        "msteams-channel-id",
        "msteams-webhook-url",
    ],
    "sharepoint": [
        "sharepoint-connected",
        "sharepoint-site-id",
        "sharepoint-drive-id",
        "sharepoint-folder-path",
    ],
    # Figma accepts EITHER a PAT or an OAuth token, so both refs are listed: whichever
    # shape the tenant used, disconnect must clear it. figma-access-token also appears
    # in _KIND_KV_SECRETS above because the OAuth callback writes it to Key Vault.
    "figma": [
        "figma-connected",
        "figma-pat",
        "figma-access-token",
        "figma-file-key",
    ],
}

# The Entra app registration shared by ms_teams and sharepoint, plus the sibling map
# used to decide when it is safe to delete. Disconnecting ONE Graph connector must not
# tombstone credentials the other is still using.
_MSGRAPH_SHARED_REFS: List[str] = [
    "msgraph-tenant-id",
    "msgraph-client-id",
    "msgraph-client-secret",
]
_MSGRAPH_SIBLING: Dict[str, str] = {"ms_teams": "sharepoint", "sharepoint": "ms_teams"}

# The single secret whose presence means "this tenant connected this kind". On
# disconnect it's tombstoned with DISCONNECTED_MARKER (authoritative); the overlay
# and auth_adapter key off it.
_KIND_PRIMARY_CREDENTIAL: Dict[str, str] = {
    "azure_devops": "ado-pat",
    "jira": "jira-api-token",
    "confluence": "confluence-api-token",
    "sonarqube": "sonarqube-token",
    "github_actions": "gha-pat",
    # A per-kind MARKER, not msgraph-client-secret. The two Graph kinds share one app
    # registration, so naming the shared secret as either kind's primary credential
    # would mean disconnecting Teams tombstones the secret and silently breaks
    # SharePoint. The marker's value is the account label.
    "ms_teams": "msteams-connected",
    "sharepoint": "sharepoint-connected",
    # A per-kind MARKER for the same reason as the Graph pair, but a different one:
    # Figma has TWO credential refs (PAT and OAuth token) and naming either as primary
    # would tombstone one while leaving the other live — a "disconnected" connector
    # that still authenticates. FigmaConnector.auth_adapter checks this marker first.
    "figma": "figma-connected",
}

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
    ("confluence", "confluence-api-token", "confluence-url"),
    ("sonarqube", "sonarqube-token", "sonarqube-url"),
    ("github_actions", "gha-pat", "gha-owner"),
    ("ms_teams", "msteams-connected", "msteams-channel-id"),
    ("sharepoint", "sharepoint-connected", "sharepoint-site-id"),
    ("figma", "figma-connected", "figma-connected"),
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
        # The per-tenant secret is AUTHORITATIVE for credential connectors. It used
        # to also have to override a tenant-blind env probe that marked azure_devops
        # "installed/healthy" for every tenant whenever a platform PAT was set; that
        # env rung is gone now, but this still keeps a connector from showing
        # connected after a disconnect.
        connected = bool(secret) and secret != secret_store.DISCONNECTED_MARKER
        existing = by_kind.get(kind)

        if not connected:
            # No tenant credential (never added, or tombstoned by disconnect) ⇒ force
            # disconnected so the card leaves "Installed" and offers "Add credentials".
            if existing is not None:
                existing.installed = False
                existing.health = "disconnected"
            continue

        # One path for every credential kind. github_actions used to be special-cased
        # here (and in set_connector_credentials) because it had no connector class to
        # resolve through; GitHubActionsConnector.health_check now reuses the very same
        # probe_github_actions helper, so the generic branch covers it.
        health = "degraded"
        try:
            # unrestricted: an org-level health probe acting for no project. Named
            # explicitly because it is the fail-open door and should read as one.
            connector = await get_connector_for_session(
                kind=kind, tenant_id=tenant_id, unrestricted=True,
            )
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
async def list_connectors(
    request: Request,
    workspaceId: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Return a Connector[] reshaped from the connector-health cache (T-M4-04).

    Overlaid with this tenant's pasted credentials. Workspace scope comes from the
    `workspaceId` query param when given, else the `X-Workspace-Id` header — the
    query param lets a caller (the create-project dialog, and Settings' Tools per
    stage) ask about a Business Unit other than the ambient active-workspace
    cookie the header is normally sourced from, mirroring GET /model/availability's
    workspaceId param.

    With a workspace resolved (either source), every entry gets `granted`: whether
    that Business Unit was given this connector by an Org Admin
    (`integration_grants`, see shared/authz/connector_grants.py). `installed`/
    `health` keep their existing, narrower meaning — a real credential exists and
    was health-checked — and additionally require the workspace to have it enabled
    (`workspace_connectors`), same as before.

    An EXPLICIT `workspaceId` query param additionally widens the candidate set to
    every catalogue kind, credentialed or not. Credentials are a project-level,
    later-stage concern — supplied by a project's own members after it exists, per
    the "NO connect / credential action" note on the Integrations hub — so a stage
    picker asking "what may this unit wire up" must not require one up front.
    Ambient header-only callers (dashboards, status widgets) keep today's
    credential-only list unchanged; only a caller that deliberately asks about a
    unit gets the wider, grant-based set.
    """
    tenant_id = getattr(request.state, "tenant_id", "")
    header_workspace_id = request.headers.get("x-workspace-id", "")
    workspace_id = workspaceId or header_workspace_id

    cache = getattr(request.app.state, "connector_health_cache", {})
    connectors = _build_connector_list(cache, tenant_id)
    connectors = await _overlay_tenant_credentials(connectors, tenant_id)

    if workspace_id:
        if workspaceId:
            by_kind = {c.kind: c for c in connectors}
            for kind in _CATALOG_KINDS:
                if kind not in by_kind:
                    connectors.append(
                        ConnectorOut(
                            id=kind, tenantId=tenant_id, kind=kind, name=kind,
                            installed=False, health="disconnected",
                            capabilities=[], lastCheckedAt=None,
                        )
                    )

        enabled_kinds = await _workspace_enabled_kinds(workspace_id, db)
        granted_kinds = await granted_target_refs(
            db, tenant_id=tenant_id, workspace_id=workspace_id, kind="connector",
        )
        for c in connectors:
            c.granted = c.kind in granted_kinds
            if c.installed and c.kind not in enabled_kinds:
                # Credentials exist at tenant level but this workspace hasn't
                # enabled it.
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


# ── Direct credential entry (PAT / API token) ─────────────────────────────────
# For providers where the user pastes a credential rather than running OAuth
# (Azure DevOps PAT, Jira email + API token). Credentials are written to the
# tenant secret store (Key Vault in prod, Fernet-encrypted DB in dev) and a live
# probe verifies them. The secret is never echoed back.

# `github` and `slack` are new entries here, and they had to be. Both used to be
# connectable tenant-wide ONLY through the OAuth flow, so deleting that flow would
# otherwise have left them with no tenant-wide path at all — a project member could
# still paste a personal credential (see _PERSONAL_CREDENTIAL_KINDS in
# project_scoped.py), but an admin could not connect the tenant.
_CREDENTIAL_KINDS = {
    "azure_devops", "jira", "confluence", "sonarqube", "github_actions", "ms_teams",
    "sharepoint", "figma", "github", "slack",
}


class SetCredentialsIn(BaseModel):
    """Paste-credential payload. Every field is optional and kind-specific.

    ADDITIVE ONLY — no existing field may change type or become required, or the
    payloads the Integrations dialog already sends ({base_url,email,api_token},
    {pat,owner}, {org_url,pat}) stop validating.
    """

    # Azure DevOps
    org_url: Optional[str] = None
    pat: Optional[str] = None
    # Jira / Confluence (both Basic auth, same shape — kind decides which secret refs)
    base_url: Optional[str] = None
    email: Optional[str] = None
    api_token: Optional[str] = None
    # GitHub Actions (reuses `pat`; owner/org optional)
    owner: Optional[str] = None
    # Microsoft Graph app registration — ms_teams and sharepoint SHARE one.
    msgraph_tenant_id: Optional[str] = None
    msgraph_client_id: Optional[str] = None
    msgraph_client_secret: Optional[str] = None
    # Microsoft Teams delivery target: webhook_url OR (team_id AND channel_id).
    team_id: Optional[str] = None
    channel_id: Optional[str] = None
    webhook_url: Optional[str] = None
    # SharePoint target. drive_id is resolved from site_url when not supplied.
    site_url: Optional[str] = None
    drive_id: Optional[str] = None
    folder_path: Optional[str] = None
    # Confluence default space (optional convenience — see notification_targets.confluence_target).
    space_key: Optional[str] = None
    # Figma. `pat` is reused for the Personal Access Token shape; figma_access_token
    # is a Figma OAuth access token, which a tenant may still hold and paste even
    # though this platform no longer runs the OAuth flow that used to mint one.
    # file_url is an optional default file (URL or bare key), not a credential.
    figma_access_token: Optional[str] = None
    file_url: Optional[str] = None
    # INBOUND webhook signing secret for this connector, per tenant.
    #
    # This is the value the tenant sets when it creates the webhook in its own GitHub
    # org / Jira site / Slack app, and it is what webhooks/router.py verifies inbound
    # deliveries against. It used to be one process-wide env var per provider, which
    # meant a single secret verified a delivery to EVERY tenant's
    # /webhooks/{connector}/{tenant_id} URL — see _tenant_webhook_secret there.
    #
    # Optional: a tenant that only makes outbound calls never receives a webhook and
    # has nothing to set. Without it, inbound deliveries for that tenant are rejected,
    # which is the correct fail-closed answer rather than a shared key.
    webhook_secret: Optional[str] = None
    # Azure DevOps service hooks carry no HMAC — they authenticate with HTTP Basic, so
    # ADO needs a username alongside the password in `webhook_secret`.
    webhook_user: Optional[str] = None
    # GitHub App, registered by the TENANT in its own org. app_id and installation_id
    # are identifiers, not secrets; the private key is the credential that signs the
    # RS256 JWT github_issues exchanges for an installation token.
    github_app_id: Optional[str] = None
    github_app_private_key: Optional[str] = None
    github_app_installation_id: Optional[str] = None


async def _store_msgraph_app(tenant_id: str, body: SetCredentialsIn, secret_store) -> bool:
    """Persist the shared Entra app registration when supplied. Returns True if stored.

    ms_teams and sharepoint share one registration, so this writes the same three refs
    for either kind and only when all three are present — a partial write would leave a
    half-configured app that fails at token-mint time with a confusing error.
    """
    entra_tenant = (body.msgraph_tenant_id or "").strip()
    client_id = (body.msgraph_client_id or "").strip()
    client_secret = (body.msgraph_client_secret or "").strip()
    if not (entra_tenant and client_id and client_secret):
        return False
    await secret_store.put_secret(tenant_id, "msgraph-tenant-id", entra_tenant)
    await secret_store.put_secret(tenant_id, "msgraph-client-id", client_id)
    await secret_store.put_secret(tenant_id, "msgraph-client-secret", client_secret)
    # A re-registration invalidates any token cached against the old app.
    from config.connectors import msgraph as _msgraph

    _msgraph._clear_token_cache()
    return True


async def _store_ms_teams_credentials(
    tenant_id: str, body: SetCredentialsIn, secret_store
) -> Optional[str]:
    """Store Microsoft Teams credentials + delivery target. Returns the account label.

    TWO ACCEPTED SHAPES:
      1. webhook_url alone — a channel-scoped Incoming Webhook. Needs no Entra app.
      2. the three msgraph_* values PLUS team_id and channel_id — the Graph path.
    """
    webhook_url = (body.webhook_url or "").strip()
    team_id = (body.team_id or "").strip()
    channel_id = (body.channel_id or "").strip()

    if not webhook_url and not (team_id and channel_id):
        raise HTTPException(
            status_code=422,
            detail=(
                "Microsoft Teams needs a delivery target: either 'webhook_url' (a Teams "
                "Incoming Webhook URL), or both 'team_id' and 'channel_id'."
            ),
        )

    stored_app = await _store_msgraph_app(tenant_id, body, secret_store)
    if not webhook_url and not stored_app:
        # The Graph path cannot work without an app registration. Fail loudly here
        # rather than storing a target that can never be reached.
        raise HTTPException(
            status_code=422,
            detail=(
                "Posting to a Teams channel via Microsoft Graph requires an Entra app "
                "registration: 'msgraph_tenant_id', 'msgraph_client_id' and "
                "'msgraph_client_secret'. Alternatively supply 'webhook_url' alone."
            ),
        )

    if webhook_url:
        await secret_store.put_secret(tenant_id, "msteams-webhook-url", webhook_url)
    if team_id:
        await secret_store.put_secret(tenant_id, "msteams-team-id", team_id)
    if channel_id:
        await secret_store.put_secret(tenant_id, "msteams-channel-id", channel_id)

    account = channel_id or "Incoming webhook"
    # The marker is what makes the catalogue read this tenant as connected, and what
    # disconnect tombstones. Its value doubles as the account label.
    await secret_store.put_secret(tenant_id, "msteams-connected", account)
    return account


async def _store_sharepoint_credentials(
    tenant_id: str, body: SetCredentialsIn, secret_store
) -> Optional[str]:
    """Store SharePoint credentials + target library. Returns the account label.

    Requires the shared Entra app registration and a site_url. drive_id is optional —
    it is resolved from the site during the verify probe and cached, so later calls
    need no lookup. An operator can also supply it directly to bypass resolution
    (useful for /teams/ or root-site URLs, which resolve_site does not handle).
    """
    site_url = (body.site_url or "").strip()
    if not site_url and not (body.drive_id or "").strip():
        raise HTTPException(
            status_code=422,
            detail="SharePoint requires 'site_url' (or an explicit 'drive_id').",
        )
    if not await _store_msgraph_app(tenant_id, body, secret_store):
        raise HTTPException(
            status_code=422,
            detail=(
                "SharePoint requires an Entra app registration: 'msgraph_tenant_id', "
                "'msgraph_client_id' and 'msgraph_client_secret'."
            ),
        )

    if body.drive_id:
        await secret_store.put_secret(tenant_id, "sharepoint-drive-id", body.drive_id.strip())
    folder = (body.folder_path or "").strip() or "SDLC Documentation"
    await secret_store.put_secret(tenant_id, "sharepoint-folder-path", folder)

    account = site_url or (body.drive_id or "").strip()
    await secret_store.put_secret(tenant_id, "sharepoint-connected", account)
    return account


def _clear_figma_auth_cache(tenant_id: str) -> None:
    """Invalidate FigmaConnector's per-tenant credential cache. Never raises.

    The connector caches resolved credentials for a short TTL to avoid re-walking the
    secret-store/Key-Vault ladder on every REST call. Any write to a Figma credential
    must drop that entry, or the old credential stays live until it expires.
    """
    try:
        from config.connectors.figma import clear_auth_cache

        clear_auth_cache(tenant_id)
    except Exception as exc:  # noqa: BLE001 — cache invalidation must not fail a write
        logger.warning("figma auth-cache invalidation failed: %s", type(exc).__name__)


async def _store_figma_credentials(
    tenant_id: str, body: SetCredentialsIn, secret_store
) -> Optional[str]:
    """Store Figma credentials + an optional default file. Returns the account label.

    TWO ACCEPTED SHAPES, matching the connector's two auth headers:
      1. `pat` — a Personal Access Token. Simple, no app registration.
      2. `figma_access_token` — an OAuth2 access token. Normally written by the OAuth
         callback rather than pasted, but accepted here so an operator holding a token
         can configure a tenant without running the redirect flow.

    Only ONE is stored. Writing both would leave auth_adapter's precedence rule
    (OAuth wins) deciding which credential a tenant actually uses, which is not a
    decision that should depend on the order someone filled in a form.
    """
    from config.connectors.figma import extract_file_key

    pat = (body.pat or "").strip()
    oauth_token = (body.figma_access_token or "").strip()

    if not pat and not oauth_token:
        raise HTTPException(
            status_code=422,
            detail=(
                "Figma needs a credential: either 'pat' (a Personal Access Token from "
                "figma.com → Settings → Security) or 'figma_access_token' (an OAuth2 "
                "access token)."
            ),
        )

    # An explicit OAuth token wins — it is user-scoped and revocable from Figma's side.
    if oauth_token:
        await secret_store.put_secret(tenant_id, "figma-access-token", oauth_token)
        await secret_store.delete_secret(tenant_id, "figma-pat")
        account = "OAuth token"
    else:
        await secret_store.put_secret(tenant_id, "figma-pat", pat)
        await secret_store.delete_secret(tenant_id, "figma-access-token")
        account = "Personal Access Token"

    # Optional default file. Rejected loudly when unparseable rather than silently
    # stored — a bad key here surfaces later as a confusing 404 from Figma.
    file_url = (body.file_url or "").strip()
    if file_url:
        file_key = extract_file_key(file_url)
        if not file_key:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Could not read a Figma file key from {file_url!r}. Paste the file "
                    "URL (https://www.figma.com/design/<key>/<name>) or the key itself."
                ),
            )
        await secret_store.put_secret(tenant_id, "figma-file-key", file_key)

    # The marker is what makes the catalogue read this tenant as connected, and what
    # disconnect tombstones. Its value doubles as the account label.
    await secret_store.put_secret(tenant_id, "figma-connected", account)
    # The connector caches resolved credentials per tenant; without this the verify
    # probe immediately below would run against whatever was cached beforehand.
    _clear_figma_auth_cache(tenant_id)
    return account


async def _resolve_and_cache_sharepoint_ids(tenant_id: str, site_url: str) -> Optional[str]:
    """Resolve site → drive after a successful probe and cache both ids.

    Returns an error token on failure, or None on success. Never raises: a resolution
    failure downgrades the credential result to "invalid" with an actionable reason
    rather than surfacing a 500.
    """
    if not site_url:
        return None
    try:
        from shared.services import secret_store
        from config.connector_factory import get_connector_for_session

        # `unrestricted` NAMED, same reasoning as the other org-level probes in this
        # router (see the connect/verify handlers): this runs during ADMIN SETUP of the
        # tenant-wide connection, before any project has wired SharePoint to a stage.
        # There is no stage whose grant could be consulted, so gating it on one would
        # deny every first-time setup — the level would be None and
        # `permits(None, "read")` False, making the connect flow fail with an access
        # error about a project that is not part of this operation.
        connector = await get_connector_for_session(
            kind="sharepoint", tenant_id=tenant_id, unrestricted=True,
        )
        site = await connector.read_adapter("resolve_site", site_url=site_url)
        site_id = (site or {}).get("id", "")
        if not site_id:
            return "SiteNotFound"
        await secret_store.put_secret(tenant_id, "sharepoint-site-id", site_id)

        drive = await connector.read_adapter("resolve_drive", site_id=site_id)
        drive_id = (drive or {}).get("id", "")
        if not drive_id:
            return "DriveNotFound"
        await secret_store.put_secret(tenant_id, "sharepoint-drive-id", drive_id)
        return None
    except Exception as exc:  # noqa: BLE001 — a resolution failure is a result, not a 500
        logger.warning(
            "sharepoint id resolution failed for tenant=%r: %s", tenant_id, type(exc).__name__
        )
        return type(exc).__name__


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

    # The inbound webhook secret is stored under the ref webhooks/router.py reads, and
    # is orthogonal to the outbound credential below: a tenant may set either, both or
    # neither. Kinds absent from this map have no inbound webhook at all.
    _WEBHOOK_SECRET_REF = {
        "azure_devops": "ado-webhook-password",
        "jira": "jira-webhook-secret",
        "github": "github-webhook-secret",
        "github_actions": "gha-webhook-secret",
        "slack": "slack-signing-secret",
        "ms_teams": "msgraph-webhook-client-state",
        "sharepoint": "msgraph-webhook-client-state",
    }

    try:
        webhook_secret = (body.webhook_secret or "").strip()
        if webhook_secret:
            ref = _WEBHOOK_SECRET_REF.get(kind)
            if not ref:
                raise HTTPException(
                    status_code=422,
                    detail=f"Connector '{kind}' does not receive inbound webhooks.",
                )
            await secret_store.put_secret(tenant_id, ref, webhook_secret)
            if kind == "azure_devops" and (body.webhook_user or "").strip():
                await secret_store.put_secret(
                    tenant_id, "ado-webhook-user", body.webhook_user.strip()
                )

        if kind == "azure_devops":
            org_url = (body.org_url or "").strip()
            pat = (body.pat or "").strip()
            if not org_url or not pat:
                raise HTTPException(status_code=422, detail="Azure DevOps requires 'org_url' and 'pat'.")
            await secret_store.put_secret(tenant_id, "ado-org-url", org_url)
            await secret_store.put_secret(tenant_id, "ado-pat", pat)
            account = org_url
        elif kind == "sonarqube":
            server_url = (body.org_url or "").strip()
            token = (body.pat or "").strip()
            if not server_url or not token:
                raise HTTPException(
                    status_code=422, detail="SonarQube requires 'org_url' (server URL) and 'pat' (token)."
                )
            await secret_store.put_secret(tenant_id, "sonarqube-url", server_url)
            await secret_store.put_secret(tenant_id, "sonarqube-token", token)
            account = server_url
        elif kind == "github_actions":
            pat = (body.pat or "").strip()
            owner = (body.owner or "").strip()
            if not pat:
                raise HTTPException(status_code=422, detail="GitHub Actions requires 'pat'.")
            await secret_store.put_secret(tenant_id, "gha-pat", pat)
            if owner:
                await secret_store.put_secret(tenant_id, "gha-owner", owner)
            account = owner or None
        elif kind == "slack":
            bot_token = (body.pat or "").strip()
            if not bot_token:
                raise HTTPException(
                    status_code=422,
                    detail="Slack requires 'pat' (a bot token, xoxb-...).",
                )
            await secret_store.put_secret(tenant_id, "slack-bot-token", bot_token)
            account = None
        elif kind == "github":
            # A GitHub App registered by THIS tenant in its own org. The platform used
            # to register one App for everybody and hold its private key in env; that
            # key signs as the App across every installation it has, so it is now the
            # tenant's to hold. installation_id selects the tenant's own installation.
            app_id = (body.github_app_id or "").strip()
            private_key = (body.github_app_private_key or "").strip()
            installation_id = (body.github_app_installation_id or "").strip()
            if not app_id or not private_key or not installation_id:
                raise HTTPException(
                    status_code=422,
                    detail="GitHub requires 'github_app_id', 'github_app_private_key' "
                    "and 'github_app_installation_id'.",
                )
            await secret_store.put_secret(tenant_id, "github-app-id", app_id)
            await secret_store.put_secret(tenant_id, "github-app-private-key", private_key)
            await secret_store.put_secret(
                tenant_id, "github-app-installation-id", installation_id
            )
            account = app_id
        elif kind == "ms_teams":
            account = await _store_ms_teams_credentials(tenant_id, body, secret_store)
        elif kind == "sharepoint":
            account = await _store_sharepoint_credentials(tenant_id, body, secret_store)
        elif kind == "figma":
            account = await _store_figma_credentials(tenant_id, body, secret_store)
        elif kind == "confluence":
            base_url = (body.base_url or "").strip()
            email = (body.email or "").strip()
            api_token = (body.api_token or "").strip()
            if not base_url or not email or not api_token:
                raise HTTPException(
                    status_code=422,
                    detail="Confluence requires 'base_url', 'email', and 'api_token'.",
                )
            await secret_store.put_secret(tenant_id, "confluence-url", base_url)
            await secret_store.put_secret(tenant_id, "confluence-email", email)
            await secret_store.put_secret(tenant_id, "confluence-api-token", api_token)
            if (body.space_key or "").strip():
                await secret_store.put_secret(
                    tenant_id, "confluence-space-key", body.space_key.strip()
                )
            account = base_url
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

    # Live verify probe with the just-stored credentials. One path for every kind —
    # github_actions no longer needs a bespoke branch now that GitHubActionsConnector
    # exists and its health_check reuses the same probe_github_actions helper.
    status = "invalid"
    error: Optional[str] = None
    try:
        # unrestricted: an org-level health probe acting for no project. Named
        # explicitly because it is the fail-open door and should read as one.
        connector = await get_connector_for_session(
            kind=kind, tenant_id=tenant_id, unrestricted=True,
        )
        health = await connector.health_check()
        status = "valid" if getattr(health, "status", "") == "healthy" else "invalid"
        error = getattr(health, "error", None)
    except Exception as exc:  # noqa: BLE001 — probe failure must not 500; report invalid.
        status = "invalid"
        error = type(exc).__name__

    # SharePoint only: once the credential verifies, resolve the site and its default
    # document library and cache both ids so no later call has to look them up.
    if kind == "sharepoint" and status == "valid":
        resolve_error = await _resolve_and_cache_sharepoint_ids(
            tenant_id, (body.site_url or "").strip()
        )
        if resolve_error:
            status = "invalid"
            error = resolve_error

    # Enable connector for the active workspace (if present)
    workspace_id = request.headers.get("x-workspace-id", "")
    if workspace_id and status == "valid":
        try:
            await _upsert_workspace_connector(workspace_id, tenant_id, kind, db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_connector_credentials: failed to upsert workspace row: %s", exc)

    logger.info("connector credentials set: kind=%r tenant=%r status=%r error=%r", kind, tenant_id, status, error)
    return {"kind": kind, "status": status, "account": account, "error": error}


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

    # The Entra app registration is shared by ms_teams and sharepoint, so it may only
    # be deleted once BOTH are disconnected.
    await _maybe_purge_shared_graph_secrets(tenant_id, kind)

    # Figma caches resolved credentials per tenant — without this the connector keeps
    # authenticating with the just-revoked token until the TTL expires, which is
    # exactly the fail-open a disconnect must not have.
    if kind == "figma":
        _clear_figma_auth_cache(tenant_id)

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


async def _maybe_purge_shared_graph_secrets(tenant_id: str, kind: str) -> None:
    """Delete the shared Entra app credentials — but only when nothing still needs them.

    ms_teams and sharepoint authenticate through ONE app registration. Deleting it on
    the first disconnect would silently break whichever Graph connector remains, so the
    sibling's `*-connected` marker is checked first. When the sibling is still
    connected the shared refs are left exactly as they are.

    Best-effort and never raises — a disconnect must not hard-fail on a vault hiccup.
    """
    sibling = _MSGRAPH_SIBLING.get(kind)
    if not sibling:
        return
    try:
        from shared.services import secret_store

        sibling_marker = _KIND_PRIMARY_CREDENTIAL.get(sibling, "")
        still_connected = False
        if sibling_marker:
            value = await secret_store.get_secret(tenant_id, sibling_marker)
            still_connected = bool(value) and value != secret_store.DISCONNECTED_MARKER

        if still_connected:
            logger.info(
                "disconnect %r: keeping shared Microsoft Graph credentials — %r is still connected",
                kind,
                sibling,
            )
            return

        for ref in _MSGRAPH_SHARED_REFS:
            try:
                await secret_store.delete_secret(tenant_id, ref)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "disconnect %r: failed to delete shared ref %r: %s",
                    kind,
                    ref,
                    type(exc).__name__,
                )
        from config.connectors import msgraph as _msgraph

        _msgraph._clear_token_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "disconnect %r: shared Graph credential cleanup failed: %s", kind, type(exc).__name__
        )


# ── Internal helpers ─────────────────────────────────────────────────
#
# REMOVED: the five OAuth code-for-token exchanges (_jira/_github/_slack/_figma/
# _azure_repos) and their token-endpoint helpers. Each one POSTed the PLATFORM's
# client_id + client_secret to the provider, which is what made those credentials
# process configuration in the first place.
#
# Every provider they served is reachable with a credential the tenant already pastes
# on the Integrations page — Jira email + API token, a GitHub PAT, a Slack bot token,
# a Figma PAT, an ADO PAT — all stored per tenant in that tenant's secret store. The
# OAuth path was a second way to obtain the same access that additionally required the
# platform to hold a credential on every tenant's behalf.


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
