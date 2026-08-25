"""GitHub Actions connector implementing the full BaseConnector contract.

Distinct from GitHubIssuesConnector: that one speaks to the Issues API with a
GitHub App installation token; this one drives the Actions API with a Personal
Access Token (scopes: repo, workflow) pasted through the Integrations
"Add credentials" form. They are separate connector KINDS in the product — one is
work tracking, the other is CI/CD — and a tenant may connect either without the
other, so they do not share credentials.

Credentials resolve through the standard ladder (mirrors AzureDevOpsConnector):
    secret_store(tenant, "gha-pat")   ← the Integrations form writes here
      → Key Vault "{tenant}-gha-pat"
      → Key Vault "gha-pat"
      → config.env.GHA_PAT             ← local dev only
A DISCONNECTED_MARKER in the secret store short-circuits with NO fallback, so a
disconnect is honoured rather than silently falling through to a global credential.

The PAT is never stored on self and never logged.

NOTE (A4 — ASSUMED, inherited from github_issues.py): GitHub REST rate-limit header
names (X-RateLimit-Remaining / X-RateLimit-Reset) have not been verified against a
live 429 in this environment. Honoured when present; exponential backoff otherwise.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

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
from config.env import GHA_OWNER, GHA_PAT
from shared.services.metrics import CONNECTOR_RATE_LIMIT_BACKOFFS

logger = logging.getLogger(__name__)

_GH_API_BASE = "https://api.github.com"
_GH_API_VERSION = "2022-11-28"

# The rate-limit bucket name used when a caller does not name a tenant. It is a
# bucket key, NOT a tenant id — credential resolution must never treat it as one.
_DEFAULT_BUCKET = "default"
# Platform convention: the global health probe passes this instead of a real tenant.
_HEALTH_PROBE_TENANT = "__health_probe__"


class GitHubActionsConnector(BaseConnector):
    """GitHub Actions connector — workflow dispatch and run inspection over REST.

    Per-tenant rate-limit state is class-level so one tenant's 429 backoff never
    blocks another (REQ-M6-12, REQ-M3-11).
    """

    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        """Constructor stores only non-secret config — never the PAT (REQ-M3-10).

        Args:
            org_url:   Optional "{owner}" or "{owner}/{repo}" hint. Credentials and
                       the owner are resolved in auth_adapter(); this is a fallback.
            tenant_id: Run context, NOT a credential. Set by the connector factory so
                       auth_adapter() resolves the tenant-scoped PAT without every
                       call site threading it through (REQ-M7-01).
        """
        self._org_url = (org_url or "").rstrip("/")
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "github_actions"

    @property
    def display_name(self) -> str:
        return "GitHub Actions"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def _resolve_ref(self, tenant_id: str, ref: str, env_fallback: str) -> Tuple[Optional[str], bool]:
        """Resolve one credential ref through the standard ladder.

        Returns (value, disconnected). When `disconnected` is True the tenant has
        explicitly disconnected this connector and NO fallback tier may be consulted —
        otherwise a disconnect would silently revert to a global credential.
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

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve the PAT and owner ephemerally.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        An explicit argument wins; otherwise the instance tenant_id set by the factory
        is used. The return value is never stored on self and must not be logged
        (REQ-M3-10, REQ-M6-14).
        """
        tid = tenant_id or self._tenant_id
        if not tid:
            raise ValueError(
                "tenant_id is required for GitHubActionsConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )

        owner, _ = await self._resolve_ref(tid, "gha-owner", GHA_OWNER)
        if not owner and self._org_url:
            owner = self._org_url.split("/")[0]

        # Project-scoped personal override, checked first: a credential this project
        # member set for themselves — or the ad-hoc value Test Connection is
        # validating — wins over the tenant-wide PAT below.
        override = await self._resolve_credential_override(tid, "github_actions")
        if override and override.token:
            # `account` carries the owner/org here — a PAT is only meaningful
            # against the account it was issued for, so the member's own value
            # wins over the tenant-wide `gha-owner` resolved above.
            return {"pat": override.token, "owner": override.account or owner or ""}

        pat, disconnected = await self._resolve_ref(tid, "gha-pat", GHA_PAT)
        if disconnected:
            return {"pat": "", "owner": ""}
        return {"pat": pat or "", "owner": owner or ""}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="github_actions",
            read_capabilities={
                "list_workflows": CapabilityEntry(
                    status="implemented",
                    description="GET /repos/{owner}/{repo}/actions/workflows",
                ),
                "list_workflow_runs": CapabilityEntry(
                    status="implemented",
                    description="GET /repos/{owner}/{repo}/actions/runs, filterable by branch/event",
                ),
                "get_workflow_run": CapabilityEntry(
                    status="implemented",
                    description="GET /repos/{owner}/{repo}/actions/runs/{run_id}",
                ),
                "get_run_jobs": CapabilityEntry(
                    status="not_supported", description="Deferred — no consumer yet"
                ),
                "download_run_logs": CapabilityEntry(
                    status="not_supported",
                    description="Returns a redirect to a zip archive; out of scope",
                ),
            },
            write_capabilities={
                "dispatch_workflow": CapabilityEntry(
                    status="implemented",
                    description=(
                        "POST .../workflows/{id}/dispatches — requires the workflow to "
                        "declare a workflow_dispatch trigger. Returns 204 with no run id; "
                        "callers poll list_workflow_runs to correlate."
                    ),
                ),
                "cancel_workflow_run": CapabilityEntry(
                    status="implemented",
                    description="POST /repos/{owner}/{repo}/actions/runs/{run_id}/cancel",
                ),
                "rerun_workflow": CapabilityEntry(
                    status="not_supported", description="Deferred — no consumer yet"
                ),
            },
            listen_capabilities={
                "workflow_run": CapabilityEntry(
                    status="implemented",
                    description="Inbound via POST /webhooks/github_actions/{tenant_id}",
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "GitHubActionsConnector.webhook_receiver: inbound workflow_run events are "
            "handled by webhooks.router POST /webhooks/github_actions/{tenant_id}"
        )

    # ── Rate limiting (per-tenant) ────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        """Wait out any active backoff window for this tenant (REQ-M6-12)."""
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe the PAT via the existing deployment verify probe.

        Reuses shared.services.deployment_probe.probe_github_actions rather than
        duplicating a probe: that helper already never raises and already reports
        type-names-only errors, and reusing it is what lets the bespoke github_actions
        branches in shared/routers/connectors.py be deleted.

        MUST NOT raise. A connector whose probe raises is dropped from the health
        cache, which makes GET /connectors/health re-probe inline on every request.
        """
        from shared.services.deployment_probe import probe_github_actions

        start = time.time()
        try:
            auth = await self.auth_adapter(self._tenant_id or _HEALTH_PROBE_TENANT)
            ok, _account, err = await probe_github_actions(
                auth.get("pat", ""), auth.get("owner") or None
            )
            return ConnectorHealth(
                connector_name="github_actions",
                status="healthy" if ok else "unhealthy",
                latency_ms=(time.time() - start) * 1000,
                error=None if ok else (err or "ProbeFailed"),
            )
        except Exception as exc:  # noqa: BLE001 — a probe failure is a result, not a crash
            return ConnectorHealth(
                connector_name="github_actions",
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
            "list_workflows": self.list_workflows,
            "list_workflow_runs": self.list_workflow_runs,
            "get_workflow_run": self.get_workflow_run,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "dispatch_workflow": self.dispatch_workflow,
            "cancel_workflow_run": self.cancel_workflow_run,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Internal HTTP helper ──────────────────────────────────────────────

    @staticmethod
    def _gha_headers(token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GH_API_VERSION,
        }

    def _auth_tenant(self, tenant_id: str) -> str:
        """Map a rate-limit bucket name onto the tenant used for credential lookup.

        `_DEFAULT_BUCKET` is a bucket key, not a tenant, so it must not be passed to
        auth_adapter as one — otherwise the tenant-scoped secret tier is skipped and
        the call silently uses a global credential.
        """
        if tenant_id and tenant_id != _DEFAULT_BUCKET:
            return tenant_id
        return self._tenant_id

    async def _gha_request(
        self,
        method: str,
        path: str,
        tenant_id: str = _DEFAULT_BUCKET,
        **kwargs: Any,
    ) -> Any:
        """Execute one GitHub Actions REST call with per-tenant rate-limit backoff.

        Handles HTTP 429 by recording the hit and raising so callers can retry;
        non-429 errors raise immediately. 204 (which every dispatch/cancel returns)
        and empty bodies come back as {}.
        """
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

        auth = await self.auth_adapter(self._auth_tenant(tenant_id))
        url = f"{_GH_API_BASE}{path}"

        client = get_async_client(timeout=30)
        resp = await client.request(
            method, url, headers=self._gha_headers(auth["pat"]), **kwargs
        )

        if resp.status_code == 429:
            # Honour X-RateLimit-Reset if present (epoch seconds — A4 ASSUMED).
            retry_after: Optional[float] = None
            reset_raw = resp.headers.get("X-RateLimit-Reset") or resp.headers.get("x-ratelimit-reset")
            if reset_raw:
                try:
                    retry_after = max(0.0, float(reset_raw) - time.time())
                except (ValueError, TypeError):
                    pass
            if retry_after is None:
                retry_after_header = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
                if retry_after_header:
                    try:
                        retry_after = float(retry_after_header)
                    except (ValueError, TypeError):
                        pass

            record_rate_limit_hit(
                self.__class__._tenant_states, tenant_id, retry_after_seconds=retry_after
            )
            CONNECTOR_RATE_LIMIT_BACKOFFS.labels(
                connector="github_actions", tenant_id=tenant_id
            ).inc()
            resp.raise_for_status()

        resp.raise_for_status()

        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def _gha_request_with_retry(
        self,
        method: str,
        path: str,
        tenant_id: str = _DEFAULT_BUCKET,
        **kwargs: Any,
    ) -> Tuple[Any, int]:
        """Execute a request, retrying once on 429; return (response_data, retry_count)."""
        for attempt in range(2):
            try:
                data = await self._gha_request(method, path, tenant_id, **kwargs)
                state = self.__class__._tenant_states.get(tenant_id)
                return data, (state.retry_count if state else 0)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt == 0:
                    continue  # backoff already recorded by _gha_request
                raise
        raise RuntimeError("GitHub Actions request retry exhausted")  # pragma: no cover

    async def _resolve_repo(self, repo: str, tenant_id: str) -> str:
        """Return a fully-qualified "{owner}/{repo}" from a bare or qualified name."""
        repo = (repo or "").strip("/")
        if not repo:
            raise ValueError("repo is required — pass '{owner}/{repo}' or a bare repo name.")
        if "/" in repo:
            return repo
        auth = await self.auth_adapter(self._auth_tenant(tenant_id))
        owner = (auth.get("owner") or "").strip("/")
        if not owner:
            raise ValueError(
                f"repo '{repo}' has no owner and no gha-owner is configured for this "
                "tenant. Pass '{owner}/{repo}' or set the owner on the connector."
            )
        return f"{owner}/{repo}"

    # ── Read operations ───────────────────────────────────────────────────

    async def list_workflows(
        self, repo: str, tenant_id: str = _DEFAULT_BUCKET
    ) -> List[Dict[str, Any]]:
        """GET /repos/{owner}/{repo}/actions/workflows → the repo's workflow definitions."""
        owner_repo = await self._resolve_repo(repo, tenant_id)
        data, _ = await self._gha_request_with_retry(
            "GET", f"/repos/{owner_repo}/actions/workflows", tenant_id,
            params={"per_page": 100},
        )
        return data.get("workflows", []) if isinstance(data, dict) else []

    async def list_workflow_runs(
        self,
        repo: str,
        branch: str = "",
        event: str = "",
        per_page: int = 20,
        tenant_id: str = _DEFAULT_BUCKET,
    ) -> List[Dict[str, Any]]:
        """GET /repos/{owner}/{repo}/actions/runs → recent runs, newest first."""
        owner_repo = await self._resolve_repo(repo, tenant_id)
        params: Dict[str, Any] = {"per_page": max(1, min(per_page, 100))}
        if branch:
            params["branch"] = branch
        if event:
            params["event"] = event
        data, _ = await self._gha_request_with_retry(
            "GET", f"/repos/{owner_repo}/actions/runs", tenant_id, params=params
        )
        return data.get("workflow_runs", []) if isinstance(data, dict) else []

    async def get_workflow_run(
        self, repo: str, run_id: str, tenant_id: str = _DEFAULT_BUCKET
    ) -> Dict[str, Any]:
        """GET /repos/{owner}/{repo}/actions/runs/{run_id} → one run's current state."""
        owner_repo = await self._resolve_repo(repo, tenant_id)
        data, _ = await self._gha_request_with_retry(
            "GET", f"/repos/{owner_repo}/actions/runs/{run_id}", tenant_id
        )
        return data if isinstance(data, dict) else {}

    # ── Write operations ──────────────────────────────────────────────────

    async def dispatch_workflow(
        self,
        repo: str,
        workflow: str,
        ref: str = "main",
        inputs: Optional[Dict[str, Any]] = None,
        tenant_id: str = _DEFAULT_BUCKET,
    ) -> Dict[str, Any]:
        """POST .../actions/workflows/{workflow}/dispatches — trigger a run.

        `workflow` is either a numeric workflow id or a filename ("deploy.yml"); the
        GitHub API accepts both in the same path position.

        Returns {"dispatched": True, "repo": ..., "workflow": ..., "ref": ...}. The
        API responds 204 with an EMPTY body — there is no run id to return. Callers
        that need one poll list_workflow_runs(event="workflow_dispatch", branch=ref)
        and correlate on created_at.
        """
        owner_repo = await self._resolve_repo(repo, tenant_id)
        payload: Dict[str, Any] = {"ref": ref or "main"}
        if inputs:
            payload["inputs"] = inputs
        await self._gha_request_with_retry(
            "POST",
            f"/repos/{owner_repo}/actions/workflows/{workflow}/dispatches",
            tenant_id,
            json=payload,
        )
        return {
            "dispatched": True,
            "repo": owner_repo,
            "workflow": workflow,
            "ref": payload["ref"],
        }

    async def cancel_workflow_run(
        self, repo: str, run_id: str, tenant_id: str = _DEFAULT_BUCKET
    ) -> Dict[str, Any]:
        """POST /repos/{owner}/{repo}/actions/runs/{run_id}/cancel."""
        owner_repo = await self._resolve_repo(repo, tenant_id)
        await self._gha_request_with_retry(
            "POST", f"/repos/{owner_repo}/actions/runs/{run_id}/cancel", tenant_id
        )
        return {"cancelled": True, "repo": owner_repo, "run_id": str(run_id)}
