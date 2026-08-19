"""SharePoint connector — read + write over Microsoft Graph drives.

Serves the Integrations catalogue's "Documents & knowledge" category: read
specifications in, file generated documentation where the business looks for it.

Auth is delegated ENTIRELY to config.connectors.msgraph — this connector declares no
credential ladder of its own, because it shares one Entra app registration per tenant
with the ms_teams connector and a second ladder would mean a second token mint.

Graph application permissions required:
    Sites.Read.All       — resolve sites/drives, list and download
    Sites.ReadWrite.All  — publish documents, create change subscriptions
`Sites.Selected` plus a per-site grant is the least-privilege production choice and
works with exactly this code; only the tenant admin consent differs.

UPLOAD LIMIT: publish_document uses the single-PUT endpoint, which Graph caps at 4 MB.
The chunked createUploadSession path is NOT implemented — generated docs are Markdown,
so the cap is documented and enforced rather than silently truncating.

NOTE (A6 — ASSUMED): Graph throttling shapes are unverified here; see msgraph.py.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from config.connectors.base import BaseConnector
from config.connectors.models import (
    CapabilityEntry,
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
)
from config.connectors.msgraph import (
    GraphCredentialsMissing,
    get_graph_token,
    graph_request,
    graph_request_with_retry,
)
from config.connectors.rate_limit import _TenantRateLimitState, await_backoff

logger = logging.getLogger(__name__)

_HEALTH_PROBE_TENANT = "__health_probe__"

# Graph's single-PUT upload ceiling. Above this a createUploadSession is required.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024


class SharePointConnector(BaseConnector):
    """SharePoint document-library connector backed by Microsoft Graph.

    Per-tenant rate-limit state is class-level to isolate tenants (REQ-M6-12).
    """

    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        """Constructor stores only non-secret config.

        Args:
            org_url:   Optional SharePoint site URL hint. The configured
                       sharepoint-site-id ref takes precedence.
            tenant_id: Run context, NOT a credential (REQ-M7-01).
        """
        self._org_url = (org_url or "").rstrip("/")
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "sharepoint"

    @property
    def display_name(self) -> str:
        return "SharePoint"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve a Graph bearer token plus the configured site/drive target.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        Never stored on self, never logged (REQ-M6-14).
        """
        tid = tenant_id or self._tenant_id
        if not tid:
            raise ValueError(
                "tenant_id is required for SharePointConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )
        token = await get_graph_token(tid)
        from shared.services.notification_targets import sharepoint_target

        target = (await sharepoint_target(tid)) or {}
        return {"token": token, **target}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="sharepoint",
            read_capabilities={
                "resolve_site": CapabilityEntry(
                    status="implemented", description="GET /sites/{hostname}:/{site-path}"
                ),
                "resolve_drive": CapabilityEntry(
                    status="implemented", description="GET /sites/{site-id}/drive"
                ),
                "list_documents": CapabilityEntry(
                    status="implemented",
                    description="GET /drives/{drive-id}/root:/{folder}:/children",
                ),
                "download_document": CapabilityEntry(
                    status="implemented",
                    description="GET /drives/{drive-id}/items/{item-id}/content",
                ),
            },
            write_capabilities={
                "publish_document": CapabilityEntry(
                    status="implemented",
                    description=(
                        "PUT /drives/{drive-id}/root:/{path}:/content — single-PUT upload, "
                        "capped at 4 MB. Chunked createUploadSession is not implemented."
                    ),
                ),
                "create_subscription": CapabilityEntry(
                    status="implemented",
                    description=(
                        "POST /subscriptions for driveItem change notifications. NOTE: "
                        "subscriptions expire (~4230 minutes) and are NOT auto-renewed."
                    ),
                ),
            },
            listen_capabilities={
                "driveItem_change": CapabilityEntry(
                    status="implemented",
                    description="Inbound via POST /webhooks/msgraph/{tenant_id}",
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "SharePointConnector.webhook_receiver: Microsoft Graph change notifications "
            "are handled by webhooks.router POST /webhooks/msgraph/{tenant_id}"
        )

    # ── Rate limiting (per-tenant) ────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe Graph reachability via GET /sites/root.

        A 403 with a valid token means the app registration exists but Sites.* has not
        been consented — that is `degraded` (fixable by an admin), not `unhealthy`.

        MUST NOT raise — a raising probe is dropped from the health cache, which makes
        GET /connectors/health re-probe inline on every request.
        """
        import httpx

        start = time.time()
        tid = self._tenant_id or _HEALTH_PROBE_TENANT
        try:
            await graph_request(
                "GET", "/sites/root", tenant_id=tid, connector_name="sharepoint"
            )
            return ConnectorHealth(
                connector_name="sharepoint",
                status="healthy",
                latency_ms=(time.time() - start) * 1000,
            )
        except httpx.HTTPStatusError as exc:
            status = "degraded" if exc.response.status_code == 403 else "unhealthy"
            return ConnectorHealth(
                connector_name="sharepoint",
                status=status,
                latency_ms=(time.time() - start) * 1000,
                error=f"http_{exc.response.status_code}",
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorHealth(
                connector_name="sharepoint",
                status="unhealthy",
                latency_ms=(time.time() - start) * 1000,
                error=type(exc).__name__,  # NEVER str(exc) — credential leakage risk
            )

    # ── Audit ─────────────────────────────────────────────────────────────

    async def audit_emitter(self, event: ConnectorAuditEvent) -> None:
        # Route through AuditEventService (D-02) — single write path for all audit
        # inserts; PII redaction + dead-letter retry happen inside the service.
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
            "resolve_site": self.resolve_site,
            "resolve_drive": self.resolve_drive,
            "list_documents": self.list_documents,
            "download_document": self.download_document,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "publish_document": self.publish_document,
            "create_subscription": self.create_subscription,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Site / drive resolution ───────────────────────────────────────────

    async def resolve_site(self, site_url: str, tenant_id: str = "") -> Dict[str, Any]:
        """Resolve a SharePoint site URL to a Graph site object.

        Accepts "https://contoso.sharepoint.com/sites/Delivery". Graph addresses such a
        site as /sites/{hostname}:/sites/{path}.

        [ASSUMED] A8 — correct for classic /sites/X URLs. Root sites and /teams/X URLs
        take different shapes; callers can supply a drive_id directly to bypass this.
        """
        tid = tenant_id or self._tenant_id
        parsed = urllib.parse.urlparse((site_url or "").strip())
        hostname, path = parsed.netloc, parsed.path.strip("/")
        if not hostname:
            raise ValueError(
                f"site_url must be an absolute SharePoint URL, got {site_url!r}."
            )
        graph_path = f"/sites/{hostname}:/{path}" if path else "/sites/{}".format(hostname)
        data, _ = await graph_request_with_retry(
            "GET", graph_path, tenant_id=tid, connector_name="sharepoint"
        )
        return data if isinstance(data, dict) else {}

    async def resolve_drive(self, site_id: str, tenant_id: str = "") -> Dict[str, Any]:
        """GET /sites/{site_id}/drive → the site's default document library."""
        tid = tenant_id or self._tenant_id
        data, _ = await graph_request_with_retry(
            "GET", f"/sites/{site_id}/drive", tenant_id=tid, connector_name="sharepoint"
        )
        return data if isinstance(data, dict) else {}

    # ── Read operations ───────────────────────────────────────────────────

    async def list_documents(
        self, drive_id: str, folder: str = "", tenant_id: str = ""
    ) -> List[Dict[str, Any]]:
        """List the children of a drive folder (root when `folder` is empty)."""
        tid = tenant_id or self._tenant_id
        folder = (folder or "").strip("/")
        path = (
            f"/drives/{drive_id}/root:/{urllib.parse.quote(folder)}:/children"
            if folder
            else f"/drives/{drive_id}/root/children"
        )
        data, _ = await graph_request_with_retry(
            "GET", path, tenant_id=tid, connector_name="sharepoint"
        )
        return data.get("value", []) if isinstance(data, dict) else []

    async def download_document(
        self, drive_id: str, item_id: str, tenant_id: str = ""
    ) -> bytes:
        """GET /drives/{drive_id}/items/{item_id}/content → raw bytes.

        Graph answers with a 302 to a short-lived download URL; the shared client
        follows redirects, so the bytes come back directly.
        """
        tid = tenant_id or self._tenant_id
        return await graph_request(
            "GET",
            f"/drives/{drive_id}/items/{item_id}/content",
            tenant_id=tid,
            connector_name="sharepoint",
            expect_bytes=True,
        )

    # ── Write operations ──────────────────────────────────────────────────

    async def publish_document(
        self,
        drive_id: str,
        path: str,
        content: bytes,
        content_type: str = "text/markdown",
        tenant_id: str = "",
    ) -> Dict[str, Any]:
        """Upload a document to the drive at `path`, creating or replacing it.

        Returns {"id", "name", "webUrl", "size"} from the resulting driveItem.

        Raises:
            ValueError: content exceeds the 4 MB single-PUT limit, or path is empty.
        """
        tid = tenant_id or self._tenant_id
        path = (path or "").strip("/")
        if not path:
            raise ValueError("path is required — e.g. 'SDLC Documentation/brd.md'.")
        if isinstance(content, str):
            content = content.encode("utf-8")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"Document is {len(content)} bytes, which exceeds the "
                f"{MAX_UPLOAD_BYTES}-byte single-PUT limit. Chunked upload "
                "(createUploadSession) is not implemented — split the document or "
                "publish it through the repository instead."
            )
        quoted = "/".join(urllib.parse.quote(seg) for seg in path.split("/"))
        data, _ = await graph_request_with_retry(
            "PUT",
            f"/drives/{drive_id}/root:/{quoted}:/content",
            tenant_id=tid,
            connector_name="sharepoint",
            content=content,
            headers={"Content-Type": content_type},
        )
        if not isinstance(data, dict):
            return {}
        return {
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "webUrl": data.get("webUrl", ""),
            "size": data.get("size", len(content)),
        }

    async def create_subscription(
        self,
        drive_id: str,
        notification_url: str,
        client_state: str,
        minutes: int = 4230,
        tenant_id: str = "",
    ) -> Dict[str, Any]:
        """Create a Graph change-notification subscription for a drive.

        IMPORTANT: Graph subscriptions expire (driveItem maxes at ~4230 minutes) and
        this platform does NOT auto-renew them — notifications stop silently once the
        expiry passes. Renewal is a named follow-on, deliberately not half-built. The
        returned `expirationDateTime` is the operator's cue.
        """
        from datetime import datetime, timedelta, timezone

        tid = tenant_id or self._tenant_id
        if not client_state or len(client_state) < 32:
            raise ValueError(
                "client_state must be at least 32 characters — it is the ONLY "
                "authentication Microsoft Graph provides on inbound notifications."
            )
        expiry = datetime.now(timezone.utc) + timedelta(minutes=max(1, minutes))
        data, _ = await graph_request_with_retry(
            "POST",
            "/subscriptions",
            tenant_id=tid,
            connector_name="sharepoint",
            json={
                "changeType": "updated",
                "notificationUrl": notification_url,
                "resource": f"drives/{drive_id}/root",
                "clientState": client_state,
                "expirationDateTime": expiry.isoformat().replace("+00:00", "Z"),
            },
        )
        return data if isinstance(data, dict) else {}
