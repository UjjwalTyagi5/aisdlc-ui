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


NOT_CONNECTED_MESSAGE = (
    "Confluence is not connected for you on this project. Confluence credentials are "
    "personal: save your own Atlassian site URL, email and API token under the "
    "project's Integrations page (Confluence), then try again."
)


class ConfluenceNotConnected(RuntimeError):
    """Raised before any request leaves when the acting user has no Confluence
    credential for this project.

    Confluence is a PERSONAL credential (base.PERSONAL_CREDENTIAL_KINDS): there is no
    tenant token to borrow, so an unconnected user resolves to a blank token and,
    often, a blank site URL — and httpx reported that as `UnsupportedProtocol`, which
    told the Design agent nothing it could relay. This names the cause and the fix.
    """

    def __init__(self) -> None:
        super().__init__(NOT_CONNECTED_MESSAGE)


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

        email = await _tenant_secret("confluence-email")
        if not email:
            email = await _keyvault.load_secret("confluence-email", tenant_id=tenant_id)

        # Project-scoped personal override, checked first: a credential this project
        # member set for themselves — or the ad-hoc value Test Connection is
        # validating — wins over the tenant-wide token below.
        override = await self._resolve_credential_override(tenant_id, "confluence")
        if override and override.token:
            # Their own site and email win: those were typed alongside the token
            # and are the pair that authenticates. Blank falls through to the
            # tenant-wide values resolved above, so an older credential that
            # carried only a token behaves exactly as it did.
            return {
                "confluence_url": _normalize_base_url(
                    override.base_url or site_url or self._org_url
                ),
                "email": override.account or email or "",
                "token": override.token,
            }

        if not self._tenant_fallback_allowed():
        # NO TENANT FALLBACK. This credential belongs to a person
        # (base.PERSONAL_CREDENTIAL_KINDS). Without one for the acting user
        # this connector is NOT connected — borrowing a shared token would make
        # it work for a project that never configured it, and record the work
        # against whoever minted that token.
            return {
                "confluence_url": _normalize_base_url(site_url or self._org_url),
                "email": email or "",
                "token": "",
            }

        from shared.services import secret_store as _ss  # lazy: avoid import cycle
        token_raw = await _tenant_secret("confluence-api-token")
        disconnected = token_raw == _ss.DISCONNECTED_MARKER  # explicitly disconnected
        token = "" if disconnected else token_raw
        if not disconnected:
            if not token:
                token = await _keyvault.load_secret("confluence-api-token", tenant_id=tenant_id)

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
                "create_space": CapabilityEntry(
                    status="implemented",
                    description="v1 /rest/api/space — v2 exposes spaces read-only",
                ),
                "upload_attachment": CapabilityEntry(
                    status="implemented",
                    description="v1 multipart upload; re-uploading a filename versions the existing attachment",
                ),
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
        """Probe the credential, not just the site.

        MUST hit an endpoint Confluence refuses anonymously. This used to call
        `list_spaces()` (GET /wiki/api/v2/spaces), which a site with anonymous
        browsing enabled answers with HTTP 200 and an empty result set — so
        `raise_for_status()` never fired and any token, including a garbage one,
        reported healthy. The identical bug was confirmed live on Jira before
        being fixed the same way; see JiraConnector.health_check.

        /wiki/rest/api/user/current (v1 — v2 has no equivalent) is the account
        behind the credential: 401 when the email/token pair is wrong, 404 when
        the site URL is, and unreachable anonymously.
        """
        start = time.time()
        try:
            await self._confluence_request_with_retry("GET", "/user/current", v1=True)
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
            "create_space": self.create_space,
            "upload_attachment": self.upload_attachment,
            "publish_document": self.upload_attachment,
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
        if not auth.get("token"):
            raise ConfluenceNotConnected()
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

        if resp.status_code >= 400:
            # The status and Confluence's own message, so an operator reading the log
            # can tell a duplicate title from a permission denial. The tools report
            # only the exception's type name to the model — this is the other half.
            # No headers and no URL query: the message body carries no credential.
            logger.warning(
                "confluence %s %s%s -> %s: %s",
                method, prefix, path, resp.status_code, " ".join(resp.text[:400].split()),
            )
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
        space = space.strip()
        if space.isdigit():
            return space
        try:
            spaces = await self.list_spaces()
        except Exception:
            return space
        # CASE-INSENSITIVE, AND BY NAME TOO. Confluence keys are uppercase by
        # construction (`create_space` below uppercases them), and nobody types them
        # that way: an agent asked to publish "to the Quicklink space" sent
        # "Quicklink", the exact-match below found nothing, and the literal string went
        # to the API as `spaceId` — an opaque HTTPStatusError the agent then explained
        # as a permissions problem. Keys are tried first, names second, so a name that
        # happens to equal another space's key cannot hijack it.
        wanted = space.casefold()
        for s in spaces:
            if space == s.get("id") or wanted == (s.get("key") or "").casefold():
                return s.get("id") or space
        for s in spaces:
            if wanted == (s.get("name") or "").strip().casefold():
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
        """PUT /wiki/api/v2/pages/{id} — Confluence requires the NEXT version number
        AND the title, on every update.

        When `version` is not supplied, the current page is fetched first to derive
        it — a stale or guessed version number is rejected by the API with a 409.

        THE TITLE IS NEVER OMITTED. v2 answers a body-only update with
        `400 "Only a Page with a status of DRAFT can have an empty title."`, which is
        how a publisher that rewrote a page body after attaching a file left the page
        saying nothing about the file. A caller that passes no title gets the page's
        current one, from the same fetch that supplies the version.
        """
        current: Dict[str, Any] = {}
        if not version or not title:
            current = await self.fetch_page_detail(page_id)
        if not version:
            version = int(current.get("version", 1)) + 1
        if not title:
            title = current.get("title") or ""
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

    async def create_space(
        self,
        key: str,
        name: str = "",
        description: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST /wiki/rest/api/space — create a new space.

        V1, AND NOT BY OVERSIGHT. The v2 API exposes spaces read-only; there is no
        `POST /api/v2/spaces`, so creation has to go through the v1 content API. Same
        reason `add_comment` and `search_content` are v1.

        The key is what every later call resolves against (`_resolve_space_id`), and
        Confluence will not let you change it afterwards — so it is normalised to the
        uppercase alphanumeric form the API accepts rather than being passed through to
        fail server-side with a message about a regex.
        """
        space_key = "".join(ch for ch in (key or "").upper() if ch.isalnum())
        if not space_key:
            raise ValueError(
                "a space key is required, and must contain at least one letter or digit"
            )
        payload: Dict[str, Any] = {"key": space_key, "name": name or space_key}
        if description:
            payload["description"] = {
                "plain": {"value": description, "representation": "plain"}
            }
        data, _ = await self._confluence_request_with_retry(
            "POST", "/space", v1=True, json=payload
        )
        return {
            "id": str(data.get("id", "")),
            "key": data.get("key", space_key),
            "name": data.get("name", ""),
            "url": ((data.get("_links") or {}).get("webui") or ""),
        }

    async def _attachment_id(self, page_id: str, filename: str) -> str:
        """The id of the attachment named `filename` on the page, or ""."""
        data, _ = await self._confluence_request_with_retry(
            "GET", f"/content/{page_id}/child/attachment", v1=True,
            params={"filename": filename, "limit": 5},
        )
        for row in (data or {}).get("results") or []:
            if row.get("title") == filename:
                return str(row.get("id", ""))
        return ""

    async def upload_attachment(
        self,
        page_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST /wiki/rest/api/content/{id}/child/attachment — attach a file to a page.

        TWO THINGS THIS ENDPOINT REQUIRES that no other call here does, and both are
        silent failures if missed:

          `X-Atlassian-Token: nocheck` — without it Confluence rejects the upload as a
          suspected XSRF attempt, with a 403 whose body talks about tokens rather than
          attachments.

          multipart/form-data — the body is a file part named `file`, not JSON. The
          request helper forwards **kwargs to httpx untouched, so `files=` works.

        `allowDuplicated` is deliberately NOT set: re-uploading the same filename then
        creates a SECOND attachment rather than a new version of the first, and a page
        accumulating `report.docx` four times is worse than an error. Instead, when
        Confluence answers `400 Cannot add a new attachment with same file name`, the
        existing attachment is found and its data updated — a new VERSION of the same
        file, which is what a re-publish should do.
        """
        if not page_id:
            raise ValueError("a page id is required to attach a file")
        if not content:
            raise ValueError(f"{filename or 'the file'} is empty — nothing to attach")

        upload = dict(
            v1=True,
            headers={"X-Atlassian-Token": "nocheck"},
            files={"file": (filename, content, content_type)},
        )
        try:
            data, _ = await self._confluence_request_with_retry(
                "POST", f"/content/{page_id}/child/attachment", **upload,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            existing_id = await self._attachment_id(page_id, filename)
            if not existing_id:
                raise
            data, _ = await self._confluence_request_with_retry(
                "POST", f"/content/{page_id}/child/attachment/{existing_id}/data", **upload,
            )
            # the /data endpoint answers with the attachment itself, not a results list
            if isinstance(data, dict) and "results" not in data:
                data = {"results": [data]}
        results = (data or {}).get("results") or []
        first = results[0] if results else {}
        return {
            "id": str(first.get("id", "")),
            "title": first.get("title", filename),
            "page_id": page_id,
            "url": ((first.get("_links") or {}).get("download") or ""),
        }

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
