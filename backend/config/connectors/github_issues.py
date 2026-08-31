"""GitHub Issues connector implementing the full BaseConnector contract.

Auth uses GitHub App installation tokens (JWT RS256 → POST access_tokens endpoint)
with a class-level cache keyed by installation_id. Tokens are refreshed proactively
when within 5 minutes of their 1-hour expiry (Pitfall 3 avoidance — REQ-M6-14).

The App id and private key are PLATFORM-level: one registered GitHub App serves
every tenant. github-app-installation-id is tenant-scoped — it selects which
tenant's installation of that App is being used. Nothing is stored on self except
the short-lived (token, expires_at) cache entry (REQ-M6-14).

Per-tenant rate-limit backoff with retry_count auditing follows the same pattern as
JiraConnector (Plan 03). GitHub rate-limit headers X-RateLimit-Remaining and
X-RateLimit-Reset are honoured where present (A4 ASSUMED — see below).

NOTE (A4 — ASSUMED): GitHub REST API rate-limit header names
(X-RateLimit-Remaining / X-RateLimit-Reset) have NOT been verified against a live
GitHub 429 payload in this environment. The implementation honours these headers if
present; falls back to exponential backoff otherwise. Must be validated against a
real GitHub tenant before production use.
"""
from __future__ import annotations


import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
import jwt  # PyJWT — already installed; RS256 support via cryptography package

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
from shared.services.metrics import CONNECTOR_RATE_LIMIT_BACKOFFS

logger = logging.getLogger(__name__)

_GH_API_BASE = "https://api.github.com"
_GH_API_VERSION = "2022-11-28"
# Proactive refresh window: refresh a cached token if it expires within this many seconds.
_TOKEN_REFRESH_MARGIN_S = 300  # 5 minutes


# REMOVED: _resolve_private_key_env(), which read the App's private key from
# GITHUB_APP_PRIVATE_KEY or a path in GITHUB_APP_PRIVATE_KEY_PATH. It carried the
# comment "Never use these in production — use Key Vault", which nothing enforced.
# A GitHub App private key signs as the App itself, so a platform-wide one let any
# tenant's run act on every installation the App had — the same cross-tenant reach the
# env-var connector credentials had. A tenant that wants App auth registers its own
# App and stores github-app-id / github-app-private-key in its own secret store.


class GitHubIssuesConnector(BaseConnector):
    """Full GitHub Issues connector backed by GitHub REST API v3 over httpx.

    Auth: GitHub App installation tokens (JWT RS256 → POST /app/installations/{id}/access_tokens).
    project (owner_repo): "{owner}/{repo}" — passed to every CRUD method.
    """

    # Per-tenant backoff state — class-level so one tenant's 429 never blocks another.
    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    # Installation token cache: installation_id → (token, expires_at_epoch)
    # Class-level so tokens are reused across method calls within the same process.
    # Never persisted; resets on process restart.
    _installation_token_cache: Dict[str, Tuple[str, float]] = {}

    def __init__(
        self,
        app_id: str = "",
        installation_id: str = "",
        org_url: str = "",
        tenant_id: str = "",
    ) -> None:
        """Constructor stores only non-secret config; no PAT/key on self.

        Args:
            app_id:          GitHub App ID (may also be loaded from Key Vault / env).
            installation_id: GitHub App installation ID for the org/repo.
            org_url:         Optional "{owner}/{repo}" — convenience alias; ignored
                             if project is passed directly to CRUD methods.
            tenant_id:       Run context, NOT a credential. The factory sets it so
                             credential resolution is tenant-scoped without every call
                             site threading it through (REQ-M7-01).
        """
        # Neither the private key nor any raw credential is stored here.
        self._app_id_hint = app_id  # hint only; KV value takes precedence in auth_adapter
        self._installation_id_hint = installation_id
        self._org_url = org_url.rstrip("/") if org_url else ""
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "github_issues"

    @property
    def display_name(self) -> str:
        return "GitHub Issues"

    # ── Auth (ephemeral installation token) ───────────────────────────────

    async def _get_installation_token(self, tenant_id: str = "") -> str:
        """Return a valid GitHub App installation token, refreshing if needed.

        Flow:
        1. Load secrets (tenant-scoped KV; the App id/PEM are platform-level).
        2. Check class-level cache for a non-expiring token.
        3. If absent or within 5 min of expiry, generate a short-lived RS256 JWT
           and exchange it at POST /app/installations/{id}/access_tokens.
        4. Cache (token, expires_at) and return the token.

        The private key PEM and JWT are never stored on self or logged.
        """
        # Fall back to the instance tenant set by the factory. Without this the
        # tenant-scoped KV tier below is skipped whenever a caller relies on the
        # instance context rather than passing the tenant explicitly.
        tenant_id = tenant_id or self._tenant_id

        # ── A project member's own PAT wins over the shared App installation ──
        # Checked HERE rather than in auth_adapter because this method is the
        # single place a token is chosen (three call sites, including
        # health_check); putting it above them means no path can quietly keep
        # using the org-wide App identity.
        #
        # A PAT and an installation token are both bearer credentials to GitHub,
        # so nothing downstream changes — _gh_headers sends either unaltered.
        override = await self._resolve_credential_override(tenant_id, "github")
        if override and override.token:
            return override.token

        # Every rung tenant-scoped. The untenanted load_secret(...) global-vault rung
        # and the env fallback that used to sit under each of these are gone: they made
        # ONE App registration serve every tenant, so a tenant that never connected
        # GitHub still transacted as the platform's App.
        #
        # The secret store is checked FIRST, matching jira/slack. It is where the
        # Integrations form writes, and in local dev it is a Fernet-encrypted DB table
        # rather than Key Vault — so a KV-only lookup here found nothing an admin had
        # just entered.
        async def _tenant_secret(ref: str) -> Optional[str]:
            if not tenant_id or tenant_id == "__health_probe__":
                return None
            try:
                from shared.services import secret_store  # lazy: avoid import cycle
                value = await secret_store.get_secret(tenant_id, ref)
                if value:
                    return value
            except Exception:  # noqa: BLE001 — fall through to Key Vault
                pass
            return await _keyvault.load_secret(ref, tenant_id=tenant_id)

        app_id = await _tenant_secret("github-app-id") or self._app_id_hint
        private_key_pem = await _tenant_secret("github-app-private-key")
        installation_id = (
            await _tenant_secret("github-app-installation-id")
            or self._installation_id_hint
        )

        now = int(time.time())

        # Check cache — reuse if still valid beyond the refresh margin.
        cached = self.__class__._installation_token_cache.get(installation_id or "")
        if cached:
            cached_token, expires_at = cached
            if expires_at - now > _TOKEN_REFRESH_MARGIN_S:
                return cached_token

        # Generate a short-lived RS256 JWT (iat=now-60 for clock-drift; exp=now+540).
        # GitHub's hard cap is 600s; we use 540 so even a few seconds of local
        # clock skew ahead of GitHub does not trip "'exp' too far in the future".
        # per github.com/apps/creating-github-apps/authenticating-with-a-github-app
        jwt_payload = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": str(app_id),
        }
        if private_key_pem:
            jwt_token = jwt.encode(jwt_payload, private_key_pem, algorithm="RS256")
            # PyJWT ≥2 returns str directly; older versions returned bytes — normalise.
            if isinstance(jwt_token, bytes):
                jwt_token = jwt_token.decode("utf-8")  # pragma: no cover
        else:
            # No private key available (local dev / test environment without credentials).
            # The access_tokens endpoint call below will fail with 401 in production;
            # in tests the respx mock intercepts the call so the empty JWT is harmless.
            logger.debug(
                "github-app-private-key not configured — JWT will be empty (test/dev only)"
            )
            jwt_token = ""

        client = get_async_client(timeout=30)
        resp = await client.post(
            f"{_GH_API_BASE}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _GH_API_VERSION,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        token: str = data["token"]

        # Parse expires_at: GitHub returns ISO 8601 e.g. "2099-01-01T00:00:00Z".
        expires_at_epoch: float = now + 3600  # fallback: assume 1h TTL
        expires_at_raw: str = data.get("expires_at", "")
        if expires_at_raw:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
                expires_at_epoch = dt.timestamp()
            except (ValueError, OSError):
                pass  # fallback epoch already set

        if installation_id:
            self.__class__._installation_token_cache[installation_id] = (token, expires_at_epoch)

        return token

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Return an ephemeral credential context for GitHub API calls.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        The installation id is tenant-scoped; the App id and PEM are platform-level,
        then to env vars (local development only).
        Return value is never stored on self beyond the token cache entry.
        """
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for GitHubIssuesConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )
        token = await self._get_installation_token(tenant_id=tenant_id)
        return {"token": token, "owner_repo": self._org_url}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="github_issues",
            read_capabilities={
                "list_stories": CapabilityEntry(status="implemented"),
                "fetch_item_detail": CapabilityEntry(status="implemented"),
                "list_projects": CapabilityEntry(
                    status="not_supported",
                    description="GitHub Issues is per-repo; list_stories covers project scope",
                ),
                "list_states": CapabilityEntry(
                    status="not_supported",
                    description="GitHub Issues uses open/closed only; no configurable workflow states",
                ),
                "list_teams": CapabilityEntry(
                    status="not_supported",
                    description="Teams are org-level; out of M6 GitHub Issues scope",
                ),
                "fetch_hierarchy": CapabilityEntry(
                    status="not_supported",
                    description="GitHub Projects v2 hierarchy out of M6 scope",
                ),
            },
            write_capabilities={
                "create_item": CapabilityEntry(status="implemented"),
                "add_comment": CapabilityEntry(status="implemented"),
                "move_item_state": CapabilityEntry(
                    status="not_supported",
                    description="GitHub Issues uses open/close only; use close_issue/reopen_issue",
                ),
                "update_item_fields": CapabilityEntry(
                    status="not_supported",
                    description="Deferred to future plan",
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        # GitHub inbound webhooks are handled by the webhooks router layer; the
        # connector CRUD layer itself does not need a receiver here.
        raise NotImplementedError(
            "GitHubIssuesConnector.webhook_receiver: use webhooks.router POST /webhooks/github/{tenant_id}"
        )

    # ── Rate limiting (per-tenant with backoff) ───────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        """Wait out any active per-tenant backoff window.

        Delegates to await_backoff (config.connectors.rate_limit) which holds a
        mutable retry_ref. This override satisfies the BaseConnector abstract member;
        individual CRUD methods call the helper directly for retry_ref access.
        """
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe the credential, not just GitHub's availability.

        MUST hit an endpoint GitHub refuses anonymously. This used to call
        /rate_limit, which answers 200 to an ANONYMOUS caller (it just reports
        the 60/hour unauthenticated allowance) — so `raise_for_status()` never
        fired and any token reported healthy. The identical bug was confirmed
        live on Jira and fixed the same way; see JiraConnector.health_check.

        The endpoint that proves authentication differs by credential type: a
        PAT belongs to a person, so /user answers it; a GitHub App installation
        token belongs to no user at all and 403s there, so it is proved by the
        repositories its installation can see instead.
        """
        start = time.time()
        try:
            override = await self._resolve_credential_override(self._tenant_id, "github")
            probe = "/user" if (override and override.token) else "/installation/repositories"
            token = await self._get_installation_token()
            client = get_async_client(timeout=30)
            resp = await client.get(
                f"{_GH_API_BASE}{probe}",
                headers=self._gh_headers(token),
            )
            resp.raise_for_status()
            latency_ms = (time.time() - start) * 1000
            return ConnectorHealth(
                connector_name="github_issues",
                status="healthy",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.time() - start) * 1000
            # NEVER str(exc) — credential leakage risk. HTTP status is safe and
            # diagnostic: 401 = bad token, 403 = token lacks the scope.
            err = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                err = f"HTTP {exc.response.status_code}"
            return ConnectorHealth(
                connector_name="github_issues",
                status="unhealthy",
                latency_ms=latency_ms,
                error=err,
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
            "list_stories": self.list_stories,
            "fetch_item_detail": self.fetch_item_detail,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "create_item": self.create_item,
            "add_comment": self.add_comment,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Internal HTTP helper ──────────────────────────────────────────────

    @staticmethod
    def _gh_headers(token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GH_API_VERSION,
        }

    async def _gh_request(
        self,
        method: str,
        path: str,
        tenant_id: str = "default",
        **kwargs: Any,
    ) -> Any:
        """Execute one GitHub REST API v3 call with per-tenant rate-limit backoff.

        Handles HTTP 429 by recording the hit and raising so callers can retry;
        non-429 errors raise immediately.

        NOTE (A4 — ASSUMED): GitHub 429 payloads may carry X-RateLimit-Remaining
        and X-RateLimit-Reset headers. The implementation reads them if present;
        falls back to exponential backoff otherwise. Verify against a live GitHub
        tenant before production use.
        """
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

        # Thread the tenant into credential resolution. This previously called
        # _get_installation_token() with no argument, so every "tenant-scoped" call
        # silently resolved a platform-wide credential and the per-tenant tier was
        # dead code. "default" is the rate-limit bucket sentinel, not a tenant, so it
        # is not treated as one; the instance tenant set by the factory wins there.
        auth_tenant = tenant_id if tenant_id and tenant_id != "default" else self._tenant_id
        token = await self._get_installation_token(tenant_id=auth_tenant)
        url = f"{_GH_API_BASE}{path}"

        client = get_async_client(timeout=30)
        resp = await client.request(
            method,
            url,
            headers=self._gh_headers(token),
            **kwargs,
        )

        if resp.status_code == 429:
            # Honour X-RateLimit-Reset if present (epoch seconds — A4 ASSUMED).
            retry_after: Optional[float] = None
            reset_raw = resp.headers.get("X-RateLimit-Reset") or resp.headers.get("x-ratelimit-reset")
            if reset_raw:
                try:
                    reset_epoch = float(reset_raw)
                    retry_after = max(0.0, reset_epoch - time.time())
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
                self.__class__._tenant_states,
                tenant_id,
                retry_after_seconds=retry_after,
            )
            CONNECTOR_RATE_LIMIT_BACKOFFS.labels(
                connector="github_issues", tenant_id=tenant_id
            ).inc()
            resp.raise_for_status()

        resp.raise_for_status()

        if resp.status_code == 204 or not resp.content:
            return {}

        return resp.json()

    async def _gh_request_with_retry(
        self,
        method: str,
        path: str,
        tenant_id: str = "default",
        **kwargs: Any,
    ) -> Tuple[Any, int]:
        """Execute a GitHub request, retrying once on 429; return (response_data, retry_count)."""
        for attempt in range(2):
            try:
                data = await self._gh_request(method, path, tenant_id, **kwargs)
                state = self.__class__._tenant_states.get(tenant_id)
                retry_count = state.retry_count if state else 0
                return data, retry_count
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt == 0:
                    # Backoff already recorded by _gh_request; try once more.
                    continue
                raise
        raise RuntimeError("GitHub request retry exhausted")  # pragma: no cover

    # ── Canonicalization helpers ──────────────────────────────────────────

    def _canonical_issue(self, issue: Dict[str, Any], project: str) -> Dict[str, Any]:
        """Map a GitHub issue object to a CanonicalWorkItem dict.

        GitHub fields: number (int) → id/source_key, title, body → description,
        state (open/closed), labels[].name → tags, html_url → url,
        assignee.login → assigned_to.
        Pull requests are excluded by callers (objects carrying a pull_request key).
        """
        assignee = issue.get("assignee") or {}
        assigned_to = assignee.get("login", "")
        tags = [lbl["name"] for lbl in issue.get("labels", []) if isinstance(lbl, dict)]
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=issue.get("number"),
            source_key=str(issue.get("number", "")),
            title=issue.get("title", ""),
            item_type="Issue",
            state=issue.get("state", ""),
            description=issue.get("body", "") or "",
            assigned_to=assigned_to,
            tags=tags,
            url=issue.get("html_url", ""),
            project=project,
            raw=issue,
        )

    # ── CRUD operations ───────────────────────────────────────────────────

    async def list_stories(
        self,
        project: str,
        state: str = "open",
        team: Optional[str] = None,
        tenant_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """GET /repos/{owner}/{repo}/issues → list of CanonicalWorkItem dicts.

        Pull requests appear in the issues endpoint but are excluded — any issue
        object carrying a `pull_request` key is filtered out.
        """
        owner_repo = project.strip("/")
        params: Dict[str, Any] = {"state": state or "open", "per_page": 100}
        data, _ = await self._gh_request_with_retry(
            "GET", f"/repos/{owner_repo}/issues", tenant_id, params=params
        )
        issues = data if isinstance(data, list) else []
        return [
            self._canonical_issue(issue, project)
            for issue in issues
            if "pull_request" not in issue  # exclude PRs from issue list
        ]

    async def fetch_item_detail(
        self,
        project: str,
        issue_number: str,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """GET /repos/{owner}/{repo}/issues/{number} → canonical detail dict."""
        owner_repo = project.strip("/")
        data, _ = await self._gh_request_with_retry(
            "GET", f"/repos/{owner_repo}/issues/{issue_number}", tenant_id
        )
        return self._canonical_issue(data, project)

    async def create_item(
        self,
        project: str,
        title: str = "",
        body: str = "",
        description: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        tenant_id: str = "default",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST /repos/{owner}/{repo}/issues → canonical dict with created number."""
        owner_repo = project.strip("/")
        payload: Dict[str, Any] = {"title": title}
        # Accept either `body` or `description` for the issue body.
        issue_body = body or description
        if issue_body:
            payload["body"] = issue_body
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        data, _ = await self._gh_request_with_retry(
            "POST", f"/repos/{owner_repo}/issues", tenant_id, json=payload
        )
        return self._canonical_issue(data, project)

    async def add_comment(
        self,
        project: str,
        issue_number: str,
        text: str,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """POST /repos/{owner}/{repo}/issues/{number}/comments."""
        owner_repo = project.strip("/")
        data, _ = await self._gh_request_with_retry(
            "POST",
            f"/repos/{owner_repo}/issues/{issue_number}/comments",
            tenant_id,
            json={"body": text},
        )
        return data
