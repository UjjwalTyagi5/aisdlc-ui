"""Confluence connector implementing the full BaseConnector contract.

Read AND write both implemented (list/fetch/search on the read side; create/update/
comment on the write side) — see capability_manifest() below. The access level a
project is actually granted (read / write / read_write) is enforced upstream by
config.connectors.scoped.ScopedConnector; this manifest only declares what the
connector is CAPABLE of, same contract as every other connector in this package.

AUTH LADDER mirrors config.connectors.jira exactly: tenant secret store (the
Integrations "Add credentials" form) -> tenant Key Vault -> global Key Vault ->
env var fallback. Confluence Cloud accepts the same shape of credential as Jira
Cloud (account email + API token, Basic Auth) but the two products are configured
independently here — a tenant may run Jira and Confluence on different sites, or
connect only one of them, so the credential refs are named confluence-* rather
than reused from jira-*. Unlike ms_teams/sharepoint (which genuinely share one
Entra app registration and would break each other if given separate credentials),
there is nothing here that must be shared.

Confluence Cloud REST API v2 (/wiki/api/v2/...) is used for spaces and pages; the
v1 content API (/wiki/rest/api/...) is used for comments, which v2 does not yet
expose a full equivalent for. Both are addressed under the same site base URL.

NOTE (ASSUMED): Confluence's Retry-After header presence and format on 429 have
NOT been verified against a live Confluence Cloud response; the implementation
honors the header if present and falls back to exponential backoff otherwise,
same as JiraConnector.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

import shared.keyvault as _keyvault
from config.connectors.base import BaseConnector
from config.connectors.http_client import get_async_client
from config.connectors.models import (
    CapabilityEntry,
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
)
from config.connectors.rate_limit import (
    _TenantRateLimitState,
    await_backoff,
    record_rate_limit_hit,
)
from config.env import CONFLUENCE_API_TOKEN, CONFLUENCE_EMAIL, CONFLUENCE_URL
from shared.services.metrics import CONNECTOR_RATE_LIMIT_BACKOFFS

logger = logging.getLogger(__name__)


def _normalize_base_url(url: str) -> str:
    """Accept a bare host or a full URL and return a scheme-qualified base with no
    trailing slash — same forgiveness JiraConnector gives, and needed for the same
    reason: httpx raises UnsupportedProtocol on a scheme-less URL."""
    u = (url or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


class ConfluenceConnector(BaseConnector):
    """Full ConfluenceConnector backed by Confluence Cloud REST API v2 over httpx."""

    # Per-tenant backoff state — class-level so one tenant's 429 never blocks another.
    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        # org_url only — no credential stored (REQ-M6-14 parity). tenant_id is run
        # context, stored as the default for auth resolution.
        self._org_url = (org_url or "").rstrip("/")
        self._tenant_id = tenant_id or "default"

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "confluence"

    @property
    def display_name(self) -> str:
        return "Confluence"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve Basic Auth credentials ephemerally. Never stored on self.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        Ladder: tenant secret store -> tenant Key Vault -> global Key Vault -> env.
        """
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for ConfluenceConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )

        async def _tenant_secret(ref: str) -> Optional[str]:
            if tenant_id == "__health_probe__":
                return None
            try:
                from shared.services import secret_store  # lazy: avoid import cycle
                return await secret_store.get_secret(tenant_id, ref)
            except Exception:  # noqa: BLE001
                return None

        site_url = await _tenant_secret("confluence-url")
        if not site_url:
            site_url = await _keyvault.load_secret("confluence-url", tenant_id=tenant_id)
        if not site_url:
            site_url = await _keyvault.load_secret("confluence-url")

        email = await _tenant_secret("confluence-email")
        if not email:
            email = await _keyvault.load_secret("confluence-email", tenant_id=tenant_id)
        if not email:
            email = await _keyvault.load_secret("confluence-email")

        # Project-scoped personal override, checked first: a credential this project
        # member set for themselves — or the ad-hoc value Test Connection is
        # validating — wins over the tenant-wide token below.
        override = await self._resolve_credential_override(tenant_id, "confluence")
        if override:
            return {
                "confluence_url": _normalize_base_url(site_url or self._org_url),
                "email": email or "",
                "token": override,
            }

        from shared.services import secret_store as _ss  # lazy: avoid import cycle
        token_raw = await _tenant_secret("confluence-api-token")
        disconnected = token_raw == _ss.DISCONNECTED_MARKER  # explicitly disconnected
        token = "" if disconnected else token_raw
        if not disconnected:
            if not token:
                token = await _keyvault.load_secret("confluence-api-token", tenant_id=tenant_id)
            if not token:
                token = await _keyvault.load_secret("confluence-api-token")

        # Env-var fallbacks for local development — never use in production.
        if not site_url:
            site_url = CONFLUENCE_URL or self._org_url
        if not email:
            email = CONFLUENCE_EMAIL
        if not token and not disconnected:
            token = CONFLUENCE_API_TOKEN

        return {
            "confluence_url": _normalize_base_url(site_url or self._org_url),
            "email": email or "",
            "token": token or "",
        }

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="confluence",
            read_capabilities={
                "list_spaces": CapabilityEntry(status="implemented"),
                "list_pages": CapabilityEntry(status="implemented"),
                "fetch_page_detail": CapabilityEntry(status="implemented"),
                "search_content": CapabilityEntry(
                    status="implemented",
                    description="CQL search via the v1 content API — v2 has no CQL equivalent yet",
                ),
            },
            write_capabilities={
                "create_page": CapabilityEntry(status="implemented"),
                "update_page": CapabilityEntry(
                    status="implemented",
                    description="Requires the page's current version number; fetched first if not supplied",
                ),
                "add_comment": CapabilityEntry(status="implemented"),
                "delete_page": CapabilityEntry(status="implemented"),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        # No inbound Confluence webhook plumbing exists yet (no verifier/normalizer
        # registered in webhooks/router.py) — deliberately not half-built. A future
        # plan wiring page-updated events should follow the jira.py pattern: a
        # verifier + normalizer pair registered there, not a receiver here.
        raise NotImplementedError(
            "ConfluenceConnector.webhook_receiver: no inbound webhook route is wired yet."
        )

    # ── Rate limiting (per-tenant with backoff) ───────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        start = time.time()
        try:
            await self.list_spaces()
            latency_ms = (time.time() - start) * 1000
            return ConnectorHealth(
                connector_name="confluence",
                status="healthy",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.time() - start) * 1000
            # NEVER str(exc) — credential leakage risk. HTTP status is safe/diagnostic:
            # 401 = bad email/token, 403 = no permission, 404 = wrong site URL.
            err = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                err = f"HTTP {exc.response.status_code}"
            return ConnectorHealth(
                connector_name="confluence",
                status="unhealthy",
                latency_ms=latency_ms,
                error=err,
            )

    # ── Audit ─────────────────────────────────────────────────────────────

    async def audit_emitter(self, event: ConnectorAuditEvent) -> None:
        from shared.services.metrics import observe_connector_call
        observe_connector_call(event)
        from shared.audit.service import audit_service
        from shared.audit.models import AuditEventPayload
        await audit_service.emit(
            AuditEventPayload(
                tenant_id=str(event.tenant_id),
                run_id=event.run_id if hasattr(event, "run_id") else None,
                event_type="connector_call",
                resource_type=event.connector_name,
                resource_id=event.method,
                agent_type=event.connector_name,
                actor_id=f"system:{event.connector_name}",
                payload=event.model_dump(),
            )
        )

    # ── Read / write dispatch ─────────────────────────────────────────────

    async def read_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "list_spaces": self.list_spaces,
            "list_pages": self.list_pages,
            "list_items": self.list_pages,
            "fetch_page_detail": self.fetch_page_detail,
            "fetch_item_detail": self.fetch_page_detail,
            "search_content": self.search_content,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "create_page": self.create_page,
            "create_item": self.create_page,
            "update_page": self.update_page,
            "update_item": self.update_page,
            "add_comment": self.add_comment,
            "delete_page": self.delete_page,
            "delete_item": self.delete_page,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Internal HTTP helper ──────────────────────────────────────────────

    async def _confluence_request(
        self,
        method: str,
        path: str,
        tenant_id: str = "",
        v1: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Execute one Confluence REST call with per-tenant rate-limit backoff.

        `path` is appended to /wiki/api/v2 unless `v1=True`, in which case it is
        appended to /wiki/rest/api (comments and CQL search — no v2 equivalent yet).
        """
        tenant_id = tenant_id or self._tenant_id
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

        auth = await self.auth_adapter(tenant_id=tenant_id)
        base_url = auth["confluence_url"].rstrip("/")
        prefix = "/wiki/rest/api" if v1 else "/wiki/api/v2"
        url = f"{base_url}{prefix}{path}"

        client = get_async_client(timeout=30)
        resp = await client.request(
            method,
            url,
            auth=(auth["email"], auth["token"]),
            **kwargs,
        )

        if resp.status_code == 429:
            retry_after_raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            retry_after: Optional[float] = None
            if retry_after_raw:
                try:
                    retry_after = float(retry_after_raw)
                except (ValueError, TypeError):
                    pass

            record_rate_limit_hit(
                self.__class__._tenant_states,
                tenant_id,
                retry_after_seconds=retry_after,
            )
            CONNECTOR_RATE_LIMIT_BACKOFFS.labels(
                connector="confluence", tenant_id=tenant_id
            ).inc()
            resp.raise_for_status()

        resp.raise_for_status()

        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def _confluence_request_with_retry(
        self,
        method: str,
        path: str,
        tenant_id: str = "",
        v1: bool = False,
        **kwargs: Any,
    ) -> tuple[Any, int]:
        """Execute a Confluence request, retrying once on 429; return (data, retry_count)."""
        tenant_id = tenant_id or self._tenant_id
        retry_count = 0
        for attempt in range(2):
            try:
                data = await self._confluence_request(method, path, tenant_id, v1=v1, **kwargs)
                state = self.__class__._tenant_states.get(tenant_id)
                retry_count = state.retry_count if state else 0
                return data, retry_count
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt == 0:
                    retry_count += 1
                    continue
                raise
        raise RuntimeError("Confluence request retry exhausted")  # pragma: no cover

    # ── Canonicalisation helpers ──────────────────────────────────────────

    def _canonical_page(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Map a v2 page object to a light, provider-agnostic dict.

        Confluence pages are not board work items, so this deliberately does NOT
        go through make_board_item (that shape is for Jira/ADO issues) — it returns
        the fields a documentation-reading agent actually needs.
        """
        body = row.get("body") or {}
        storage = body.get("storage") or {}
        return {
            "id": str(row.get("id", "")),
            "title": row.get("title", ""),
            "status": row.get("status", ""),
            "spaceId": str(row.get("spaceId", "")),
            "version": (row.get("version") or {}).get("number", 1),
            "content": storage.get("value", ""),
            "url": row.get("_links", {}).get("webui", ""),
        }

    # ── CRUD operations ───────────────────────────────────────────────────

    async def list_spaces(self) -> List[Dict[str, Any]]:
        """GET /wiki/api/v2/spaces → space picker [{id, key, name}]."""
        data, _ = await self._confluence_request_with_retry("GET", "/spaces")
        results = data.get("results", []) if isinstance(data, dict) else []
        return [
            {
                "id": str(s.get("id", "")),
                "key": s.get("key", ""),
                "name": s.get("name") or s.get("key", ""),
            }
            for s in results
        ]

    async def _resolve_space_id(self, space: str) -> str:
        """Return the numeric space id for a key-or-id input.

        list_pages requires a numeric space-id; the picker/agent may pass either a
        friendly key ("ENG") or an id. Falls back to the input unchanged when no
        space list is available or nothing matches, same shape as
        JiraConnector._resolve_project_key.
        """
        if not space:
            return space
        if space.isdigit():
            return space
        try:
            spaces = await self.list_spaces()
        except Exception:
            return space
        for s in spaces:
            if space == s.get("key") or space == s.get("id"):
                return s.get("id") or space
        return space

    async def list_pages(self, space: str = "", title: str = "") -> List[Dict[str, Any]]:
        """GET /wiki/api/v2/pages?space-id=... → pages in a space."""
        params: Dict[str, Any] = {"limit": 100}
        if space:
            params["space-id"] = await self._resolve_space_id(space)
        if title:
            params["title"] = title
        data, _ = await self._confluence_request_with_retry("GET", "/pages", params=params)
        results = data.get("results", []) if isinstance(data, dict) else []
        return [self._canonical_page(p) for p in results]

    async def fetch_page_detail(self, page_id: str) -> Dict[str, Any]:
        """GET /wiki/api/v2/pages/{id}?body-format=storage → canonical page dict."""
        data, _ = await self._confluence_request_with_retry(
            "GET", f"/pages/{page_id}", params={"body-format": "storage"}
        )
        return self._canonical_page(data)

    async def search_content(self, cql: str, limit: int = 25) -> List[Dict[str, Any]]:
        """GET /wiki/rest/api/content/search?cql=... — the v1 CQL search endpoint.

        v2 has no full CQL search yet, so this is the one v1-only read.
        """
        data, _ = await self._confluence_request_with_retry(
            "GET", "/content/search", v1=True, params={"cql": cql, "limit": limit}
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        return [
            {
                "id": str(r.get("id", "")),
                "title": r.get("title", ""),
                "type": r.get("type", ""),
                "spaceKey": (r.get("space") or {}).get("key", ""),
                "url": (r.get("_links") or {}).get("webui", ""),
            }
            for r in results
        ]

    async def create_page(
        self,
        space: str,
        title: str = "",
        content: str = "",
        parent_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST /wiki/api/v2/pages → canonical dict with created id."""
        space_id = await self._resolve_space_id(space)
        payload: Dict[str, Any] = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": content},
        }
        if parent_id:
            payload["parentId"] = parent_id
        data, _ = await self._confluence_request_with_retry("POST", "/pages", json=payload)
        return self._canonical_page(data)

    async def update_page(
        self,
        page_id: str,
        title: str = "",
        content: str = "",
        version: int = 0,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """PUT /wiki/api/v2/pages/{id} — Confluence requires the NEXT version number.

        When `version` is not supplied, the current page is fetched first to derive
        it — a stale or guessed version number is rejected by the API with a 409.
        """
        if not version:
            current = await self.fetch_page_detail(page_id)
            version = int(current.get("version", 1)) + 1
        payload: Dict[str, Any] = {
            "id": page_id,
            "status": "current",
            "version": {"number": version},
        }
        if title:
            payload["title"] = title
        if content:
            payload["body"] = {"representation": "storage", "value": content}
        data, _ = await self._confluence_request_with_retry(
            "PUT", f"/pages/{page_id}", json=payload
        )
        return self._canonical_page(data)

    async def delete_page(self, page_id: str, **kwargs: Any) -> Dict[str, Any]:
        """DELETE /wiki/api/v2/pages/{id} — moves the page to trash."""
        await self._confluence_request_with_retry("DELETE", f"/pages/{page_id}")
        return {"page_id": page_id, "deleted": True}

    async def add_comment(self, page_id: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        """POST /wiki/rest/api/content/{id}/child/comment — the v1 comment shape.

        v2 exposes /footer-comments, but the v1 content-comment endpoint is used
        here because it accepts plain `storage` body representation directly
        against a page id with no extra lookup, matching the create_page shape.
        """
        payload = {
            "type": "comment",
            "container": {"id": page_id, "type": "page"},
            "body": {"storage": {"value": text, "representation": "storage"}},
        }
        data, _ = await self._confluence_request_with_retry(
            "POST", "/content", v1=True, json=payload
        )
        return {
            "id": str(data.get("id", "")),
            "page_id": page_id,
        }
