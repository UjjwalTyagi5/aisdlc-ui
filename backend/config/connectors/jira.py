"""Jira connector implementing the full BaseConnector contract.

Replaces the stub from Plan 03. Credentials are resolved ephemerally inside
auth_adapter() via Key Vault first, env var fallback — never stored on self
(REQ-M6-14). Per-tenant rate-limit backoff with retry_count auditing wired
per REQ-M6-10 / REQ-M6-12.

NOTE (A3 — ASSUMED): Jira Retry-After header presence and format have NOT been
verified against a live Jira Cloud 429 payload. The implementation honors the
header if present; falls back to exponential backoff otherwise. Must be
validated against a real tenant before production use.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

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
from config.env import JIRA_API_TOKEN, JIRA_EMAIL, JIRA_URL
from shared.services.metrics import CONNECTOR_RATE_LIMIT_BACKOFFS

logger = logging.getLogger(__name__)


def _normalize_base_url(url: str) -> str:
    """Forgive a bare host: accept `yourco.atlassian.net` or a full URL and return a
    scheme-qualified base (`https://yourco.atlassian.net`) with no trailing slash.
    Without this, httpx raises UnsupportedProtocol on a scheme-less URL."""
    u = (url or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


class JiraConnector(BaseConnector):
    """Full JiraConnector backed by Jira Cloud REST API v3 over httpx."""

    # Per-tenant backoff state — class-level so one tenant's 429 never blocks another.
    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    def __init__(self, org_url: str, tenant_id: str = "") -> None:
        # org_url only — no credential stored (REQ-M6-14). tenant_id is run context:
        # stored as the default for auth resolution so read methods (health_check,
        # list_projects, …) resolve THIS tenant's stored credentials instead of falling
        # back to the empty global/env URL (which caused UnsupportedProtocol on verify).
        self._org_url = org_url.rstrip("/")
        self._tenant_id = tenant_id or "default"

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "jira"

    @property
    def display_name(self) -> str:
        return "Jira"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve credentials ephemerally: OAuth Bearer first, then Basic Auth fallback.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).

        OAuth-first path (REQ-M7-20, SC#9):
          When jira-access-token is present in tenant KV, returns an OAuth-mode
          dict with mode="oauth", bearer=<token>, jira_url=api.atlassian.com/ex/jira/{cloud_id}.
          _jira_request() then sends Authorization: Bearer {token} against the
          Atlassian API gateway — no Basic Auth tuple (Pitfall 6, T-7.4-22).

        Basic Auth fallback (M6 backward-compat):
          When jira-access-token is absent, falls through to the existing tenant-scoped
          KV → global KV → env-var resolution path (mode="basic").
          M6 operator-provisioned Basic-Auth tenants keep working unchanged.

        Return value is never stored on self and must not be logged/persisted (T-7.4-22).
        """
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for JiraConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )

        # ── Project-scoped personal override, checked first: a credential this
        # project member set for themselves — or the ad-hoc value Test Connection
        # is validating — always wins, including over the tenant's shared OAuth
        # token (asking for it means asking for THEIR identity to be used).
        override = await self._resolve_credential_override(tenant_id, "jira")
        if override and override.token:
            # The member's OWN site and email win outright: they were typed
            # together with the token and are the pair that authenticates. Only
            # when they were left blank does the tenant-wide chain answer, so a
            # credential saved before those fields existed still works.
            jira_url = override.base_url
            email = override.account
            if not (jira_url and email):
                try:
                    from shared.services import secret_store as _ss_ov  # lazy: avoid import cycle
                    stored_url = await _ss_ov.get_secret(tenant_id, "jira-url")
                    stored_email = await _ss_ov.get_secret(tenant_id, "jira-email")
                except Exception:  # noqa: BLE001
                    stored_url = stored_email = None
                jira_url = jira_url or (
                    stored_url
                    or await _keyvault.load_secret("jira-url", tenant_id=tenant_id)
                    or await _keyvault.load_secret("jira-url")
                    or JIRA_URL
                    or self._org_url
                )
                email = email or (
                    stored_email
                    or await _keyvault.load_secret("jira-email", tenant_id=tenant_id)
                    or await _keyvault.load_secret("jira-email")
                    or JIRA_EMAIL
                )
            return {
                "mode": "basic",
                "jira_url": _normalize_base_url(jira_url or ""),
                "email": email or "",
                "token": override.token,
            }

        # ── OAuth-first: check for tenant-provisioned OAuth 3LO token (REQ-M7-20) ──
        oauth_token = await _keyvault.load_secret("jira-access-token", tenant_id=tenant_id)
        if oauth_token:
            cloud_id = await _keyvault.load_secret("jira-cloud-id", tenant_id=tenant_id)
            # api.atlassian.com/ex/jira/{cloud_id} is the correct gateway for OAuth 3LO
            # tokens. CRUD paths already start with /rest/api/3/... so they append cleanly.
            gateway_url = f"https://api.atlassian.com/ex/jira/{cloud_id}"
            return {
                "mode": "oauth",
                "jira_url": gateway_url,
                "bearer": oauth_token,  # T-7.4-22: never log this value
            }

        # ── Basic Auth fallback: tenant secret store → tenant KV → global KV → env ──
        # The secret store (Fernet-DB in local dev / Key Vault in prod) is the path the
        # Integrations "Add credentials" form writes to. Skipped for the health-probe
        # sentinel; never allowed to raise.
        async def _tenant_secret(ref: str) -> Optional[str]:
            if tenant_id == "__health_probe__":
                return None
            try:
                from shared.services import secret_store  # lazy: avoid import cycle
                return await secret_store.get_secret(tenant_id, ref)
            except Exception:  # noqa: BLE001
                return None

        jira_url = await _tenant_secret("jira-url")
        if not jira_url:
            jira_url = await _keyvault.load_secret("jira-url", tenant_id=tenant_id)
        if not jira_url:
            jira_url = await _keyvault.load_secret("jira-url")

        email = await _tenant_secret("jira-email")
        if not email:
            email = await _keyvault.load_secret("jira-email", tenant_id=tenant_id)
        if not email:
            email = await _keyvault.load_secret("jira-email")

        from shared.services import secret_store as _ss  # lazy: avoid import cycle
        token_raw = await _tenant_secret("jira-api-token")
        disconnected = token_raw == _ss.DISCONNECTED_MARKER  # explicitly disconnected
        token = "" if disconnected else token_raw
        if not disconnected:
            if not token:
                token = await _keyvault.load_secret("jira-api-token", tenant_id=tenant_id)
            if not token:
                token = await _keyvault.load_secret("jira-api-token")

        # Env-var fallbacks for local development — never use in production.
        # Skipped when explicitly disconnected so a disconnected tenant gets no creds.
        if not jira_url:
            jira_url = JIRA_URL or self._org_url
        if not email:
            email = JIRA_EMAIL
        if not token and not disconnected:
            token = JIRA_API_TOKEN

        return {
            "mode": "basic",
            "jira_url": _normalize_base_url(jira_url or self._org_url),
            "email": email or "",
            "token": token or "",
        }

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="jira",
            read_capabilities={
                "list_projects": CapabilityEntry(status="implemented"),
                "fetch_item_detail": CapabilityEntry(status="implemented"),
                "list_stories": CapabilityEntry(status="implemented"),
                "list_states": CapabilityEntry(
                    status="not_supported",
                    description="Jira uses project-specific workflows; use transitions endpoint",
                ),
                "list_teams": CapabilityEntry(
                    status="not_supported",
                    description="Jira teams modelled differently; out of M6 scope",
                ),
                "fetch_hierarchy": CapabilityEntry(
                    status="not_supported",
                    description="Jira hierarchy modelling deferred to future plan",
                ),
            },
            write_capabilities={
                "create_item": CapabilityEntry(status="implemented"),
                "move_item_state": CapabilityEntry(
                    status="implemented",
                    description="Two-step: GET /transitions then POST transition id",
                ),
                "add_comment": CapabilityEntry(status="implemented"),
                "update_item_fields": CapabilityEntry(
                    status="not_supported",
                    description="Deferred to future plan",
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        # Jira inbound webhooks are handled by the webhooks router layer; the
        # connector layer itself does not need a receiver here.
        raise NotImplementedError(
            "JiraConnector.webhook_receiver: use webhooks.router POST /webhooks/jira/{tenant_id}"
        )

    # ── Rate limiting (per-tenant with backoff) ───────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        """Wait out any active per-tenant backoff window.

        Delegates to rate_limit_manager_with_backoff (config.connectors.rate_limit)
        which holds a mutable retry_ref that the caller reads after the await.
        This override satisfies the BaseConnector abstract member; individual
        CRUD methods call the helper directly for retry_ref access.
        """
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe the credential, not just the site.

        MUST hit an endpoint Jira refuses anonymously. This used to call
        `list_projects()` (GET /rest/api/3/project), which a site with anonymous
        browsing enabled answers with HTTP 200 and an empty list — so
        `raise_for_status()` never fired and ANY token, including a garbage one,
        reported healthy. "Test connection" on the project Integrations page runs
        exactly this, so it was confirming credentials it had never checked.

        `/rest/api/3/myself` is the account behind the credential: 401 when the
        email/token pair is wrong, 404 when the site URL is, and it cannot be
        reached anonymously. It works unchanged on the OAuth path too — the
        gateway URL in auth_adapter() prefixes the same /rest/api/3/... paths.
        """
        start = time.time()
        try:
            await self._jira_request_with_retry("GET", "/rest/api/3/myself")
            latency_ms = (time.time() - start) * 1000
            return ConnectorHealth(
                connector_name="jira",
                status="healthy",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.time() - start) * 1000
            # NEVER str(exc) — credential leakage risk. But the HTTP status code is safe
            # and diagnostic: 401 = bad email/token, 403 = no permission, 404 = wrong site URL.
            err = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                err = f"HTTP {exc.response.status_code}"
            return ConnectorHealth(
                connector_name="jira",
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

    @staticmethod
    def _normalize_kwargs(kwargs: dict) -> dict:
        """Map the provider-agnostic tool kwargs to Jira's native method params.

        The agent's board tools (and the ADO connector) use generic names — item_id,
        new_state, comment. Jira's methods use native names — issue_key, target_state,
        text. Normalizing here means ONE tool call works on both providers without the
        tools knowing which board is connected. A native name already present wins.
        """
        alias = {"item_id": "issue_key", "new_state": "target_state", "comment": "text"}
        out = dict(kwargs)
        for generic, native in alias.items():
            if generic in out and native not in out:
                out[native] = out.pop(generic)
        return out

    async def read_adapter(self, operation: str, **kwargs: Any) -> Any:
        kwargs = self._normalize_kwargs(kwargs)
        _MAP = {
            "list_projects": self.list_projects,
            "list_stories": self.list_stories,
            # Board ingest (pull stories) calls "list_all_items" — the ADO operation
            # name. Jira's equivalent is list_stories (all issues in the project), so
            # alias it for a uniform provider contract.
            "list_all_items": self.list_stories,
            "list_items": self.list_stories,
            "fetch_item_detail": self.fetch_item_detail,
            "list_states": self.list_states,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        kwargs = self._normalize_kwargs(kwargs)
        _MAP = {
            "create_project": self.create_project,
            "create_item": self.create_item,
            "update_item": self.update_item,
            "update_item_fields": self.update_item,
            "delete_item": self.delete_item,
            "move_item_state": self.move_item_state,
            "add_comment": self.add_comment,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Internal HTTP helper ──────────────────────────────────────────────

    async def _jira_request(
        self,
        method: str,
        path: str,
        tenant_id: str = "",
        **kwargs: Any,
    ) -> Any:
        """Execute one Jira REST API v3 call with per-tenant rate-limit backoff.

        Handles HTTP 429 by recording the hit and raising so callers can retry;
        non-429 errors raise immediately. retry_count is returned for audit use.
        """
        tenant_id = tenant_id or self._tenant_id
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

        auth = await self.auth_adapter(tenant_id=tenant_id)
        base_url = auth["jira_url"].rstrip("/")
        url = f"{base_url}{path}"

        client = get_async_client(timeout=30)
        if auth.get("mode") == "oauth":
            # OAuth 3LO: Authorization: Bearer {token} — no Basic Auth tuple.
            # T-7.4-22: auth["bearer"] is never logged; error paths use type(exc).__name__.
            resp = await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {auth['bearer']}"},
                **kwargs,
            )
        else:
            # Basic Auth: existing M6 operator-provisioned path (backward-compat).
            resp = await client.request(
                method,
                url,
                auth=(auth["email"], auth["token"]),
                **kwargs,
            )

        if resp.status_code == 429:
            # Honor Retry-After header if present (A3 — ASSUMED; verify against live tenant).
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
                connector="jira", tenant_id=tenant_id
            ).inc()
            # Raise so caller retries; retry_count is now updated in tenant state.
            resp.raise_for_status()

        resp.raise_for_status()

        # 204 No Content — successful but no body.
        if resp.status_code == 204 or not resp.content:
            return {}

        return resp.json()

    async def _jira_request_with_retry(
        self,
        method: str,
        path: str,
        tenant_id: str = "",
        **kwargs: Any,
    ) -> tuple[Any, int]:
        """Execute a Jira request, retrying once on 429; return (response_data, retry_count)."""
        tenant_id = tenant_id or self._tenant_id
        retry_count = 0
        for attempt in range(2):
            try:
                data = await self._jira_request(method, path, tenant_id, **kwargs)
                # Read retry_count from tenant state after successful call.
                state = self.__class__._tenant_states.get(tenant_id)
                retry_count = state.retry_count if state else 0
                return data, retry_count
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt == 0:
                    # Back off already recorded by _jira_request; try once more.
                    retry_count += 1
                    continue
                raise
        # Shouldn't reach here, but satisfy type checker.
        raise RuntimeError("Jira request retry exhausted")  # pragma: no cover

    # ── Canonicalisation helpers ──────────────────────────────────────────

    def _canonical_summary(
        self, row: Dict[str, Any], project: str
    ) -> Dict[str, Any]:
        """Map a Jira project or issue summary row to a CanonicalWorkItem dict."""
        fields = row.get("fields") or {}
        status = (fields.get("status") or {}).get("name", "") or row.get("state", "")
        assignee_obj = fields.get("assignee") or {}
        assigned_to = (
            assignee_obj.get("displayName")
            or assignee_obj.get("emailAddress")
            or row.get("assigned_to", "")
        )
        priority_obj = fields.get("priority") or {}
        priority = priority_obj.get("name", "") or row.get("priority", "")
        issuetype_obj = fields.get("issuetype") or {}
        item_type = issuetype_obj.get("name", "") or row.get("work_item_type", "") or row.get("type", "")
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=row.get("id"),
            source_key=row.get("key") or str(row.get("id", "")),
            title=fields.get("summary", "") or row.get("title", "") or row.get("name", ""),
            item_type=item_type,
            state=status,
            assigned_to=assigned_to,
            tags=fields.get("labels", []) or row.get("tags", []),
            url=row.get("url", ""),
            project=project,
            raw=row,
            priority=priority,
        )

    def _canonical_detail(
        self, row: Dict[str, Any], project: str
    ) -> Dict[str, Any]:
        """Map a full Jira issue object to a detailed CanonicalWorkItem dict."""
        fields = row.get("fields") or {}
        status = (fields.get("status") or {}).get("name", "")
        assignee_obj = fields.get("assignee") or {}
        assigned_to = (
            assignee_obj.get("displayName")
            or assignee_obj.get("emailAddress")
            or ""
        )
        priority_obj = fields.get("priority") or {}
        priority = priority_obj.get("name", "")
        issuetype_obj = fields.get("issuetype") or {}
        item_type = issuetype_obj.get("name", "")
        description_obj = fields.get("description") or {}
        # Description may be Atlassian Document Format (ADF) or plain string.
        if isinstance(description_obj, dict):
            desc = ""  # ADF — not parsed in this implementation
        else:
            desc = str(description_obj) if description_obj else ""
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=row.get("id"),
            source_key=row.get("key") or str(row.get("id", "")),
            title=fields.get("summary", ""),
            item_type=item_type,
            state=status,
            description=desc,
            assigned_to=assigned_to,
            tags=fields.get("labels", []),
            url=row.get("url", ""),
            project=project,
            raw=row,
            priority=priority,
        )

    # ── Preserved helpers (test_board_provider_contracts.py compatibility) ──

    def _canonical(
        self, row: Dict[str, Any], project: str, detail: bool = False
    ) -> Dict[str, Any]:
        """Legacy helper preserved for test_board_provider_contracts.py.

        Calls make_board_item directly with the flat dict shape the contract
        tests use (not nested Jira API fields).
        """
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=row.get("id"),
            source_key=row.get("key") or str(row.get("id", "")),
            title=row.get("title", ""),
            item_type=row.get("work_item_type", ""),
            state=row.get("state", ""),
            description=row.get("description", "") if detail else "",
            acceptance_criteria=row.get("acceptance_criteria", []),
            assigned_to=row.get("assigned_to", ""),
            tags=row.get("tags", []),
            url=row.get("url", ""),
            project=project,
            raw=row,
            priority=row.get("priority", ""),
        )

    def _transition_candidates(self, new_state: str) -> List[str]:
        """Return candidate transition names for fuzzy-matching the target state.

        Preserved from the stub — test_board_provider_contracts.py imports the
        legacy config.jira_ingestion._transition_candidates which is a separate
        function; this method is the connector-level equivalent kept for symmetry.
        """
        base = (new_state or "").strip()
        if not base:
            return []
        seen: List[str] = []
        for cand in (base, base.title(), base.upper(), base.lower()):
            if cand and cand not in seen:
                seen.append(cand)
        return seen

    # ── CRUD operations ───────────────────────────────────────────────────

    async def list_projects(self, project: str = "") -> List[Dict[str, Any]]:
        """GET /rest/api/3/project → board-picker projects [{name, key, id}].

        A Jira project is NOT a work item, so it must NOT go through _canonical_summary
        (that produced work-item dicts with no `name`, so the picker fell back to the
        bare key, e.g. "SCRUM"). Return the human project name ("My Software Team") +
        its key so the picker shows the friendly name and ingest can query by key.
        """
        data, _ = await self._jira_request_with_retry("GET", "/rest/api/3/project")
        if not isinstance(data, list):
            return []
        return [
            {
                "name": p.get("name") or p.get("key", ""),
                "key": p.get("key", ""),
                "id": str(p.get("id", "")),
            }
            for p in data
        ]

    async def list_stories(
        self,
        project: str,
        state: str = "",
        team: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search issues in a project via JQL.

        Uses /rest/api/3/search/jql — the enhanced search endpoint. The legacy
        GET /rest/api/3/search was removed by Atlassian (returns 410 Gone) in 2025.
        The new endpoint returns only id+key unless `fields` is specified, so the
        summary fields needed for canonicalisation are requested explicitly.
        """
        # Quote the project so a multi-word NAME ("My Software Team") is valid JQL.
        # Quoted JQL matches a project by either its name or its key, so this works
        # whether the picker passes the friendly name or the key.
        jql = f'project = "{project}"'
        if state:
            jql += f' AND status = "{state}"'
        params = {
            "jql": jql,
            "maxResults": 100,
            "fields": "summary,status,assignee,priority,issuetype,labels",
        }
        data, _ = await self._jira_request_with_retry(
            "GET", "/rest/api/3/search/jql", params=params
        )
        issues = data.get("issues", []) if isinstance(data, dict) else []
        return [self._canonical_summary(issue, project) for issue in issues]

    async def fetch_item_detail(
        self, project: str, issue_key: str
    ) -> Dict[str, Any]:
        """GET /rest/api/3/issue/{key} → canonical detail dict."""
        data, _ = await self._jira_request_with_retry(
            "GET", f"/rest/api/3/issue/{issue_key}"
        )
        return self._canonical_detail(data, project)

    async def _resolve_project_key(self, project: str) -> str:
        """Return the Jira project KEY for a name-or-key input.

        Reads (list_stories/list_projects) tolerate the display name via quoted JQL, but
        issue CREATE requires the real key ("SCRUM"), not the name ("My Software Team").
        Matches the input against project keys first, then names (case-insensitive), and
        falls back to the input unchanged when no project list is available.
        """
        if not project:
            return project
        try:
            projects = await self.list_projects()
        except Exception:
            return project
        for p in projects:
            if project == p.get("key"):
                return project
        target = project.strip().lower()
        for p in projects:
            if target == (p.get("name") or "").strip().lower():
                return p.get("key") or project
        return project

    async def create_item(
        self,
        project: str,
        title: str = "",
        description: str = "",
        item_type: str = "Story",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST /rest/api/3/issue → canonical dict with created key."""
        project_key = await self._resolve_project_key(project)
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": title,
                "issuetype": {"name": item_type},
            }
        }
        if description:
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }
        data, _ = await self._jira_request_with_retry(
            "POST", "/rest/api/3/issue", json=payload
        )
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=data.get("id"),
            source_key=data.get("key") or str(data.get("id", "")),
            title=title,
            item_type=item_type,
            state="",
            description=description,
            project=project,
            raw=data,
        )

    @staticmethod
    def _derive_project_key(name: str) -> str:
        """A valid Jira project key from a name: uppercase letters/digits, ≤10 chars."""
        letters = "".join(c for c in name.upper() if c.isalnum())
        # Keys must start with a letter.
        letters = letters.lstrip("0123456789") or "PROJ"
        return letters[:10]

    async def create_project(
        self,
        name: str,
        key: str = "",
        project_type: str = "software",
        template: str = "",
        description: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """POST /rest/api/3/project — create a new Jira project (requires admin token).

        Resolves the current user as project lead, auto-derives a key from the name when
        one isn't given, and defaults to a Scrum software template. Returns the created
        project's id/key/name.
        """
        me, _ = await self._jira_request_with_retry("GET", "/rest/api/3/myself")
        lead_account_id = me.get("accountId", "")
        proj_key = (key or self._derive_project_key(name)).upper()
        template_key = template or "com.pyxis.greenhopper.jira:gh-simplified-agility-scrum"
        payload: Dict[str, Any] = {
            "key": proj_key,
            "name": name,
            "projectTypeKey": project_type,
            "projectTemplateKey": template_key,
        }
        if lead_account_id:
            payload["leadAccountId"] = lead_account_id
        if description:
            payload["description"] = description
        data, _ = await self._jira_request_with_retry(
            "POST", "/rest/api/3/project", json=payload
        )
        return {
            "id": str(data.get("id", "")),
            "key": data.get("key") or proj_key,
            "name": name,
            "url": data.get("self", ""),
        }

    async def update_item(
        self,
        project: str = "",
        issue_key: str = "",
        title: str = "",
        description: str = "",
        item_type: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """PUT /rest/api/3/issue/{key} — update summary/description/issue type.

        Only provided fields change. Status is NOT changed here — use move_item_state
        (Jira status changes go through the transitions API, not a field write).
        """
        fields: Dict[str, Any] = {}
        if title:
            fields["summary"] = title
        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description}]}
                ],
            }
        if item_type:
            fields["issuetype"] = {"name": item_type}
        if not fields:
            return {"issue_key": issue_key, "updated": False, "note": "no fields to update"}
        await self._jira_request_with_retry(
            "PUT", f"/rest/api/3/issue/{issue_key}", json={"fields": fields}
        )
        return {"issue_key": issue_key, "updated": True, "fields": list(fields.keys())}

    async def delete_item(
        self, project: str = "", issue_key: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        """DELETE /rest/api/3/issue/{key} — permanently delete the issue."""
        await self._jira_request_with_retry("DELETE", f"/rest/api/3/issue/{issue_key}")
        return {"issue_key": issue_key, "deleted": True}

    async def list_states(self, project: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        """GET /rest/api/3/project/{key}/statuses → distinct workflow status names.

        Jira statuses are defined per issue type; flatten to a unique ordered list so the
        agent can validate a target state before requesting a transition.
        """
        key = await self._resolve_project_key(project) if project else project
        try:
            data, _ = await self._jira_request_with_retry(
                "GET", f"/rest/api/3/project/{key}/statuses"
            )
        except Exception:
            return []
        seen: List[str] = []
        for itype in (data if isinstance(data, list) else []):
            for st in itype.get("statuses", []):
                name = st.get("name", "")
                if name and name not in seen:
                    seen.append(name)
        return [{"name": n} for n in seen]

    async def move_item_state(
        self,
        project: str,
        issue_key: str,
        target_state: str,
        tenant_id: str = "default",
    ) -> None:
        """Two-step Jira transition: GET available transitions, POST matching one.

        Never uses PUT /issue/{id} with a status field directly (Pitfall 2).
        """
        transitions_data, _ = await self._jira_request_with_retry(
            "GET", f"/rest/api/3/issue/{issue_key}/transitions"
        )
        transitions: List[Dict[str, Any]] = transitions_data.get("transitions", [])

        candidates = self._transition_candidates(target_state)
        matched_id: Optional[str] = None
        for candidate in candidates:
            for t in transitions:
                if t.get("name", "").strip().lower() == candidate.strip().lower():
                    matched_id = t["id"]
                    break
            if matched_id:
                break

        if not matched_id:
            available = [t.get("name", "") for t in transitions]
            raise ValueError(
                f"No matching transition for {target_state!r}. "
                f"Available transitions: {available}"
            )

        await self._jira_request_with_retry(
            "POST",
            f"/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": matched_id}},
        )

    async def add_comment(
        self, project: str, issue_key: str, text: str
    ) -> Dict[str, Any]:
        """POST /rest/api/3/issue/{key}/comment."""
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": text}],
                    }
                ],
            }
        }
        data, _ = await self._jira_request_with_retry(
            "POST", f"/rest/api/3/issue/{issue_key}/comment", json=payload
        )
        return data
