"""Azure Repos connector implementing the full BaseConnector contract.

Handles PR webhook normalization and branch operations for Azure Repos
(part of Azure DevOps). Reuses the same ADO credentials as AzureDevOpsConnector
(tenant secret store, then Key Vault "{tenant}-ado-pat" — REQ-M6-14).

SECURITY NOTE (no HMAC):
ADO service hooks do NOT send a cryptographic signature header.  Authentication
for inbound webhooks uses HTTP Basic Auth configured at webhook registration
time.  This limitation is documented in the capability_manifest
listen_capabilities.  The actual Basic-Auth header verification lives in
webhooks/verifiers/azure_repos.py (Plan 06).  This connector owns normalization,
credentials, and outbound operations only.

NOTE (A2 — ASSUMED):
ADO service hook payloads include a top-level "id" field (a GUID) used as the
dedup event_id.  This shape has been assumed from ADO service hook documentation.
Must be verified against a real ADO service hook payload before production use.
"""
from __future__ import annotations


import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import shared.keyvault as _keyvault
from config.connectors.base import BaseConnector, ConnectorNotAvailableError
from config.connectors.http_client import get_async_client
from config.connectors.models import (
    CapabilityEntry,
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
    make_board_item,
)
from config.connectors.rate_limit import (
    _TenantRateLimitState,
    await_backoff,
    record_rate_limit_hit,
)

logger = logging.getLogger(__name__)

# Map ADO PR status values to canonical states.
_STATUS_MAP: dict[str, str] = {
    "active": "open",
    "completed": "closed",
    "abandoned": "abandoned",
    "draft": "draft",
}


class AzureReposConnector(BaseConnector):
    """Azure Repos connector — PR webhook normalization + branch operation stubs.

    Credentials are identical to AzureDevOpsConnector (ADO PAT); no separate
    secret is required unless the tenant uses a different ADO org for Repos.

    Per-tenant rate-limit state is class-level so one tenant's 429 backoff
    never blocks another (REQ-M6-12).
    """

    _tenant_states: Dict[str, _TenantRateLimitState] = {}
    # Per-tenant semaphores for concurrent call limiting (mirrors AzureDevOpsConnector).
    _tenant_semaphores: Dict[str, asyncio.Semaphore] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        # org_url only — no PAT/credential attribute (REQ-M3-10, REQ-M6-14).
        # org_url is defaulted and tenant_id accepted so the connector factory can
        # construct every kind uniformly; tenant_id is run context, not a credential.
        self._org_url = (org_url or "").rstrip("/")
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "azure_repos"

    @property
    def display_name(self) -> str:
        return "Azure Repos"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve PAT ephemerally: Key Vault first, env var fallback.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        An explicit argument wins; otherwise the instance tenant_id set by the factory
        is used (mirrors AzureDevOpsConnector).
        Reuses the same ADO credentials as AzureDevOpsConnector — both
        Azure DevOps (boards) and Azure Repos share a single PAT/org.
        Return value is never stored on self and must not be logged/persisted.
        """
        tenant_id = tenant_id or self._tenant_id
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for AzureReposConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )
        # This connector never consulted the project-scoped credential at all — it
        # only ever read the tenant-wide PAT, so it could not act as a person even
        # when that person had saved one.
        override = await self._resolve_credential_override(tenant_id, "azure_devops")
        if override and override.token:
            return {
                "org_url": (override.base_url or self._org_url or "").rstrip("/"),
                "pat": override.token,
            }

        if not self._tenant_fallback_allowed():
            # NO TENANT FALLBACK. This connector's credential belongs to a
            # person (base.PERSONAL_CREDENTIAL_KINDS). Without one for the
            # acting user it is NOT connected — borrowing a shared token would
            # make the connector work for a project that never configured it,
            # and record the work against whoever minted that token.
            return {"org_url": self._org_url, "pat": ""}

        pat = await _keyvault.load_secret("ado-pat", tenant_id=tenant_id)
        return {"org_url": self._org_url, "pat": pat}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="azure_repos",
            read_capabilities={
                "list_repositories": CapabilityEntry(
                    status="stub",
                    description="List repos via ADO REST API; implementation deferred to Plan 06+",
                ),
                "get_pull_request": CapabilityEntry(
                    status="stub",
                    description="Fetch PR detail; implementation deferred to Plan 06+",
                ),
            },
            write_capabilities={
                "create_pull_request": CapabilityEntry(
                    status="stub",
                    description="Create a PR; implementation deferred to Plan 06+",
                ),
            },
            listen_capabilities={
                "pr_webhook": CapabilityEntry(
                    status="implemented",
                    description=(
                        "Receive ADO PR service-hook payloads and normalize to CanonicalWorkItem. "
                        "NOTE: ADO service hooks use HTTP Basic Auth — there is NO HMAC signature "
                        "header (unlike GitHub/Jira). Basic-Auth verification is performed by "
                        "webhooks/verifiers/azure_repos.py (Plan 06). This connector owns "
                        "normalization and credentials only (Pitfall 4 — A2 ASSUMED: top-level "
                        "'id' field used as dedup event_id; verify against real ADO payload)."
                    ),
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize an ADO PR service-hook payload to a CanonicalWorkItem dict.

        Expected payload shape (git.pullrequest.created / updated / merged):
          {
            "id": "<guid>",            # top-level event GUID — A2 ASSUMED
            "eventType": "git.pullrequest.created",
            "resource": {
              "pullRequestId": 101,
              "title": "...",
              "status": "active",      # active | completed | abandoned | draft
              "sourceRefName": "refs/heads/feature/x",
              "targetRefName": "refs/heads/main",
              "repository": {
                "name": "myrepo",
                "project": {"name": "MyProject"}
              },
              "createdBy": {"displayName": "Dev User"}
            }
          }

        Returns a CanonicalWorkItem dict via make_board_item().
        The top-level "id" (event GUID) is also preserved as event_id for dedup
        (A2 ASSUMED — verify against a real ADO service hook before production).
        """
        resource = payload.get("resource", {})
        repo = resource.get("repository", {})
        project_name = repo.get("project", {}).get("name", "") or repo.get("name", "")
        repo_name = repo.get("name", "")

        pr_id = resource.get("pullRequestId")
        title = resource.get("title", "")
        raw_status = resource.get("status", "")
        canonical_state = _STATUS_MAP.get(raw_status, raw_status)
        assigned_to = (resource.get("createdBy") or {}).get("displayName", "")
        source_ref = resource.get("sourceRefName", "")
        target_ref = resource.get("targetRefName", "")

        # A2: top-level "id" is the ADO service hook event GUID used for dedup.
        event_id = payload.get("id", "")

        item = make_board_item(
            provider_kind=self.connector_name,
            item_id=pr_id,
            source_key=str(pr_id) if pr_id is not None else "",
            title=title,
            item_type="PullRequest",
            state=canonical_state,
            assigned_to=assigned_to,
            project=project_name or repo_name,
            raw=payload,
        )

        # Attach event metadata for dedup and routing (not part of CanonicalWorkItem schema
        # but required by the webhook pipeline — REQ-M6-07 dedup key uses event_id).
        item["event_id"] = event_id
        item["source_ref"] = source_ref
        item["target_ref"] = target_ref
        item["event_type"] = payload.get("eventType", "")
        return item

    # ── Rate limiting (per-tenant) ────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        """Throttle calls per tenant — one tenant's backoff must not block another (REQ-M6-12)."""
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe Azure Repos reachability via the repos list endpoint.

        Uses type(exc).__name__ only — never str(exc) — to avoid leaking
        credentials into health records (M1 decision).
        """
        start = time.time()
        try:
            await self._probe_repos_endpoint()
            latency_ms = (time.time() - start) * 1000
            return ConnectorHealth(
                connector_name="azure_repos",
                status="healthy",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.time() - start) * 1000
            return ConnectorHealth(
                connector_name="azure_repos",
                status="unhealthy",
                latency_ms=latency_ms,
                error=type(exc).__name__,
            )

    async def _probe_repos_endpoint(self) -> None:
        """Light connectivity probe — raises on any error."""
        auth = await self.auth_adapter()
        org_url = auth["org_url"]
        pat = auth["pat"]
        if not org_url or not pat:
            raise ConnectorNotAvailableError("ADO credentials not configured")

        # Probe the collections/_apis endpoint (doesn't need a project name).
        probe_url = f"{org_url}/_apis/projects?api-version=7.1&$top=1"
        client = get_async_client(timeout=10)
        resp = await client.get(probe_url, auth=("", pat))
        resp.raise_for_status()

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
        _MAP: dict[str, Any] = {
            "list_repositories": self.list_repositories,
            "get_pull_request": self.get_pull_request,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP: dict[str, Any] = {
            "create_pull_request": self.create_pull_request,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Branch / PR operation stubs ───────────────────────────────────────
    # Full implementations deferred to Plan 06+.  Stubs raise NotImplementedError
    # to prevent silent no-ops; callers should check capability_manifest first.

    async def list_repositories(self, project: str) -> List[Dict[str, Any]]:
        """List Azure Repos in a project.  Stub — implement in Plan 06+."""
        raise NotImplementedError(
            "AzureReposConnector.list_repositories: deferred to Plan 06+"
        )

    async def get_pull_request(self, project: str, repo: str, pr_id: int) -> Dict[str, Any]:
        """Fetch a single PR by ID.  Stub — implement in Plan 06+."""
        raise NotImplementedError(
            "AzureReposConnector.get_pull_request: deferred to Plan 06+"
        )

    async def create_pull_request(
        self,
        project: str,
        repo: str,
        title: str,
        source_branch: str,
        target_branch: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """Create a PR.  Stub — implement in Plan 06+."""
        raise NotImplementedError(
            "AzureReposConnector.create_pull_request: deferred to Plan 06+"
        )
