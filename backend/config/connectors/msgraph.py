"""Shared Microsoft Graph auth + HTTP helper for the ms_teams and sharepoint connectors.

Both connectors authenticate through ONE Entra app registration per platform tenant
(client-credentials flow), so they must share one token cache: two class-level caches
would mint two tokens for the same (directory, client_id) pair and double the
login.microsoftonline.com round-trips and throttling exposure. The cache here is
module-level and keyed on "{entra_tenant_id}:{client_id}", so a single mint serves
both connectors.

This lives in config/connectors/ rather than shared/services/ because it is a
connector-layer credential resolver — config/connectors/rate_limit.py is the existing
precedent for a shared helper module inside the connectors package.

NAMING HAZARD, held deliberately in the parameter names:
    tenant_id       = the PLATFORM tenant UUID (what this codebase means everywhere)
    entra_tenant_id = the MICROSOFT Entra directory id
Never spell both "tenant" — conflating them silently crosses tenants.

Raw httpx is used rather than msgraph-sdk/msal, matching
GitHubIssuesConnector._get_installation_token. No new dependency is introduced.

NOTE (A6 — ASSUMED): Graph throttling responses have not been verified against a live
tenant here. Graph documents 429 with Retry-After (seconds); SharePoint-backed
resources can also return 503 with Retry-After. Both are honoured when present, with
exponential backoff otherwise. Verify against a real tenant before production use.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx

import shared.keyvault as _keyvault
from config.connectors.rate_limit import (
    await_backoff,
    record_rate_limit_hit,
)
from config.env import (
    MSGRAPH_CLIENT_ID,
    MSGRAPH_CLIENT_SECRET,
    MSGRAPH_TENANT_ID,
)
from shared.services.metrics import CONNECTOR_RATE_LIMIT_BACKOFFS

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Refresh a cached token when it expires within this many seconds (same margin as
# GitHubIssuesConnector — Graph access tokens are ~1h).
_TOKEN_REFRESH_MARGIN_S = 300

# Platform convention: the global health probe passes this instead of a real tenant.
_HEALTH_PROBE_TENANT = "__health_probe__"

# Secret-store / Key Vault refs for the shared Entra app registration. Written by
# POST /connectors/{ms_teams|sharepoint}/credentials.
REF_ENTRA_TENANT_ID = "msgraph-tenant-id"
REF_CLIENT_ID = "msgraph-client-id"
REF_CLIENT_SECRET = "msgraph-client-secret"

# "{entra_tenant_id}:{client_id}" -> (access_token, expires_at_epoch).
# Module-level so ms_teams and sharepoint share one mint. Never persisted.
_token_cache: Dict[str, Tuple[str, float]] = {}

# Per-platform-tenant backoff state, shared by both Graph connectors — they hit the
# same Graph throttle bucket, so isolating them from each other would be a lie.
_graph_tenant_states: Dict[str, Any] = {}


class GraphCredentialsMissing(RuntimeError):
    """No usable Entra app registration is configured for this tenant."""


async def _resolve_ref(tenant_id: str, ref: str, env_fallback: str) -> Tuple[Optional[str], bool]:
    """Resolve one credential ref through the standard connector ladder.

    Returns (value, disconnected). `disconnected` True means the tenant explicitly
    disconnected — NO fallback tier may be consulted, or a disconnect would silently
    revert to a global credential.
    """
    value: Optional[str] = None
    if tenant_id and tenant_id != _HEALTH_PROBE_TENANT:
        try:
            from shared.services import secret_store  # lazy: avoid import cycle

            stored = await secret_store.get_secret(tenant_id, ref)
            if stored == secret_store.DISCONNECTED_MARKER:
                return None, True
            value = stored
        except Exception:  # noqa: BLE001 — degrade to the KV/env tiers
            value = None
    if not value and tenant_id:
        value = await _keyvault.load_secret(ref, tenant_id=tenant_id)
    if not value:
        value = await _keyvault.load_secret(ref)
    if not value:
        value = env_fallback
    return value, False


async def resolve_graph_credentials(tenant_id: str) -> dict[str, str]:
    """Resolve the Entra app registration for a platform tenant.

    Returns {"entra_tenant_id", "client_id", "client_secret"}. Values are ephemeral —
    callers must not store or log them (REQ-M3-10, REQ-M6-14).

    Raises:
        ValueError: tenant_id is empty (REQ-M7-01, SC-02).
        GraphCredentialsMissing: no complete app registration is configured.
    """
    if not tenant_id:
        raise ValueError(
            "tenant_id is required to resolve Microsoft Graph credentials — "
            "connector credentials are per-tenant (REQ-M7-01)."
        )

    entra_tenant_id, disc_a = await _resolve_ref(tenant_id, REF_ENTRA_TENANT_ID, MSGRAPH_TENANT_ID)
    client_id, disc_b = await _resolve_ref(tenant_id, REF_CLIENT_ID, MSGRAPH_CLIENT_ID)
    client_secret, disc_c = await _resolve_ref(tenant_id, REF_CLIENT_SECRET, MSGRAPH_CLIENT_SECRET)

    if disc_a or disc_b or disc_c:
        raise GraphCredentialsMissing("Microsoft Graph is disconnected for this tenant.")
    if not (entra_tenant_id and client_id and client_secret):
        raise GraphCredentialsMissing(
            "No Microsoft Graph app registration is configured for this tenant "
            "(need msgraph-tenant-id, msgraph-client-id, msgraph-client-secret)."
        )
    return {
        "entra_tenant_id": entra_tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
    }


async def get_graph_token(tenant_id: str = "") -> str:
    """Mint (or reuse) a Graph access token via the client-credentials flow.

    Mirrors GitHubIssuesConnector._get_installation_token: check the cache, refresh
    proactively inside the expiry margin, cache the result. Neither the client secret
    nor the token is ever logged.
    """
    creds = await resolve_graph_credentials(tenant_id)
    cache_key = f"{creds['entra_tenant_id']}:{creds['client_id']}"

    now = time.time()
    cached = _token_cache.get(cache_key)
    if cached:
        token, expires_at = cached
        if expires_at - now > _TOKEN_REFRESH_MARGIN_S:
            return token

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{LOGIN_BASE}/{creds['entra_tenant_id']}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "scope": GRAPH_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token", "")
    if not token:
        raise GraphCredentialsMissing("Entra token endpoint returned no access_token.")
    expires_at = time.time() + float(data.get("expires_in", 3600))
    _token_cache[cache_key] = (token, expires_at)
    return token


def _clear_token_cache() -> None:
    """Drop every cached Graph token. Used by disconnect and by tests."""
    _token_cache.clear()


async def graph_request(
    method: str,
    path: str,
    *,
    tenant_id: str,
    connector_name: str,
    base: str = GRAPH_BASE,
    expect_bytes: bool = False,
    **kwargs: Any,
) -> Any:
    """Execute one Graph call with per-tenant backoff and throttle accounting.

    `path` is appended to `base` when it starts with "/", otherwise treated as a full
    URL (Graph @odata.nextLink values are absolute).

    Raises httpx.HTTPStatusError on non-2xx (including 429 after recording the hit),
    so callers can distinguish a throttle from a hard failure. Returns parsed JSON,
    raw bytes when `expect_bytes`, or {} for 204/empty bodies.
    """
    retry_ref = [0]
    await await_backoff(_graph_tenant_states, tenant_id, retry_ref)

    token = await get_graph_token(tenant_id)
    url = path if path.startswith("http") else f"{base}{path}"

    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {token}")
    headers.setdefault("Accept", "application/json")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)

    # Graph throttles with 429; SharePoint-backed resources can also emit 503. Both
    # carry Retry-After in seconds when present (A6 — ASSUMED).
    if resp.status_code in (429, 503):
        retry_after: Optional[float] = None
        raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if raw:
            try:
                retry_after = float(raw)
            except (ValueError, TypeError):
                pass
        record_rate_limit_hit(_graph_tenant_states, tenant_id, retry_after_seconds=retry_after)
        CONNECTOR_RATE_LIMIT_BACKOFFS.labels(
            connector=connector_name, tenant_id=tenant_id
        ).inc()
        resp.raise_for_status()

    resp.raise_for_status()

    if expect_bytes:
        return resp.content
    if resp.status_code == 204 or not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


async def graph_request_with_retry(
    method: str,
    path: str,
    *,
    tenant_id: str,
    connector_name: str,
    **kwargs: Any,
) -> Tuple[Any, int]:
    """Execute a Graph call, retrying once on 429/503; return (data, retry_count)."""
    for attempt in range(2):
        try:
            data = await graph_request(
                method, path, tenant_id=tenant_id, connector_name=connector_name, **kwargs
            )
            state = _graph_tenant_states.get(tenant_id)
            return data, (getattr(state, "retry_count", 0) if state else 0)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 503) and attempt == 0:
                continue  # backoff already recorded by graph_request
            raise
    raise RuntimeError("Graph request retry exhausted")  # pragma: no cover
