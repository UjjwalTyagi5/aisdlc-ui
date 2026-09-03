"""SonarQube connector implementing the full BaseConnector contract.

Both read AND write are implemented — see capability_manifest() below. Complements,
rather than duplicates, the existing static-analysis path: agents_orchestrator's
testing_agent already PARSES a downloaded SonarQube report artifact after a pipeline
run (agents_orchestrator/testing_agent/tools/security_parsers.py::parse_sonar_issues_json)
— that is offline, point-in-time, and read-only. This connector is the live counterpart:
an agent can ask a running SonarQube server "is this project's quality gate passing
right now", list/triage its open issues, and act on them (comment, transition, assign)
without waiting for the next pipeline artifact.

AUTH is SonarQube's own convention: HTTP Basic with the user token as the username and
an EMPTY password (SonarQube Cloud and Server both accept this — no separate email/PAT
pairing like Jira). Ladder mirrors every other connector in this package: tenant secret
store (the Integrations "Add credentials" form) -> tenant Key Vault -> global Key Vault
-> env var fallback (local dev only).

NOTE (ASSUMED): SonarQube's rate-limit behavior (whether it sends Retry-After on 429)
is unverified here; the implementation honors the header if present and falls back to
exponential backoff otherwise, same as every other connector in this package.
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
    trailing slash — same forgiveness JiraConnector/ConfluenceConnector give."""
    u = (url or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


class SonarQubeConnector(BaseConnector):
    """Full SonarQubeConnector backed by the SonarQube Web API over httpx."""

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
        return "sonarqube"

    @property
    def display_name(self) -> str:
        return "SonarQube"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve the server URL + token ephemerally. Never stored on self.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        Ladder: tenant secret store -> tenant Key Vault -> global Key Vault -> env.
        """
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for SonarQubeConnector.auth_adapter() — "
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

        server_url = await _tenant_secret("sonarqube-url")
        if not server_url:
            server_url = await _keyvault.load_secret("sonarqube-url", tenant_id=tenant_id)

        # Project-scoped personal override, checked first: a credential this project
        # member set for themselves — or the ad-hoc value Test Connection is
        # validating — wins over the tenant-wide token below.
        override = await self._resolve_credential_override(tenant_id, "sonarqube")
        if override and override.token:
            # Their own server URL wins; blank falls back to the tenant-wide one
            # resolved above, then to the org_url this connector was built with.
            return {
                "sonarqube_url": _normalize_base_url(
                    override.base_url or server_url or self._org_url
                ),
                "token": override.token,
            }

        if not self._tenant_fallback_allowed():
        # NO TENANT FALLBACK. This credential belongs to a person
        # (base.PERSONAL_CREDENTIAL_KINDS). Without one for the acting user
        # this connector is NOT connected — borrowing a shared token would make
        # it work for a project that never configured it, and record the work
        # against whoever minted that token.
            return {
                "sonarqube_url": _normalize_base_url(server_url or self._org_url),
                "token": "",
            }

        from shared.services import secret_store as _ss  # lazy: avoid import cycle
        token_raw = await _tenant_secret("sonarqube-token")
        disconnected = token_raw == _ss.DISCONNECTED_MARKER  # explicitly disconnected
        token = "" if disconnected else token_raw
        if not disconnected:
            if not token:
                token = await _keyvault.load_secret("sonarqube-token", tenant_id=tenant_id)

        return {
            "sonarqube_url": _normalize_base_url(server_url or self._org_url),
            "token": token or "",
        }

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="sonarqube",
            read_capabilities={
                "list_projects": CapabilityEntry(status="implemented"),
                "get_quality_gate_status": CapabilityEntry(
                    status="implemented",
                    description="GET /api/qualitygates/project_status — PASSED/FAILED + conditions",
                ),
                "get_measures": CapabilityEntry(
                    status="implemented",
                    description="Coverage, bugs, vulnerabilities, code smells, ratings, duplication",
                ),
                "list_issues": CapabilityEntry(status="implemented"),
                "fetch_issue_detail": CapabilityEntry(status="implemented"),
                "list_quality_gates": CapabilityEntry(status="implemented"),
            },
            write_capabilities={
                "create_project": CapabilityEntry(status="implemented"),
                "delete_project": CapabilityEntry(status="implemented"),
                "add_comment": CapabilityEntry(status="implemented"),
                "transition_issue": CapabilityEntry(
                    status="implemented",
                    description="confirm/resolve/reopen/falsepositive/wontfix/accept",
                ),
                "assign_issue": CapabilityEntry(status="implemented"),
                "set_issue_severity": CapabilityEntry(
                    status="implemented",
                    description="Deprecated on newer SonarQube server versions; kept for broad compatibility",
                ),
                "set_quality_gate": CapabilityEntry(
                    status="implemented",
                    description="Assign a named quality gate to a project",
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        # No inbound SonarQube webhook plumbing exists yet (no verifier/normalizer
        # registered in webhooks/router.py) — deliberately not half-built. A future
        # plan wiring analysis-complete events should follow the jira.py pattern: a
        # verifier + normalizer pair registered there, not a receiver here.
        raise NotImplementedError(
            "SonarQubeConnector.webhook_receiver: no inbound webhook route is wired yet."
        )

    # ── Rate limiting (per-tenant with backoff) ───────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe the credential, not just the server.

        MUST distinguish a valid token from an absent one. This used to call
        `list_projects()` (GET /api/projects/search), which a server allowing
        anonymous browsing answers with HTTP 200 and an empty component list —
        so `raise_for_status()` never fired and any token reported healthy. The
        identical bug was confirmed live on Jira; see JiraConnector.health_check.

        /api/authentication/validate is SonarQube's own token check. NOTE it
        answers HTTP 200 either way and puts the verdict in the BODY as
        {"valid": true|false} — so the status code alone is exactly the trap
        this method is being fixed for, and the body must be read.
        """
        start = time.time()
        try:
            data, _ = await self._sonar_request_with_retry(
                "GET", "/api/authentication/validate"
            )
            latency_ms = (time.time() - start) * 1000
            if not (isinstance(data, dict) and data.get("valid") is True):
                return ConnectorHealth(
                    connector_name="sonarqube",
                    status="unhealthy",
                    latency_ms=latency_ms,
                    # Not an HTTP failure — the server answered 200 and said no.
                    error="invalid_token",
                )
            return ConnectorHealth(
                connector_name="sonarqube",
                status="healthy",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.time() - start) * 1000
            # NEVER str(exc) — credential leakage risk. HTTP status is safe/diagnostic:
            # 401 = bad token, 403 = no permission, 404 = wrong server URL.
            err = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                err = f"HTTP {exc.response.status_code}"
            return ConnectorHealth(
                connector_name="sonarqube",
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
            "list_projects": self.list_projects,
            "list_items": self.list_projects,
            "get_quality_gate_status": self.get_quality_gate_status,
            "get_measures": self.get_measures,
            "list_issues": self.list_issues,
            "fetch_issue_detail": self.fetch_issue_detail,
            "fetch_item_detail": self.fetch_issue_detail,
            "list_quality_gates": self.list_quality_gates,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "create_project": self.create_project,
            "delete_project": self.delete_project,
            "add_comment": self.add_comment,
            "transition_issue": self.transition_issue,
            "move_item_state": self.transition_issue,
            "assign_issue": self.assign_issue,
            "set_issue_severity": self.set_issue_severity,
            "set_quality_gate": self.set_quality_gate,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Internal HTTP helper ──────────────────────────────────────────────

    async def _sonar_request(
        self,
        method: str,
        path: str,
        tenant_id: str = "",
        **kwargs: Any,
    ) -> Any:
        """Execute one SonarQube Web API call with per-tenant rate-limit backoff.

        Auth is HTTP Basic with the token as username and an empty password — the
        convention SonarQube itself documents for token auth (no Bearer header).
        """
        tenant_id = tenant_id or self._tenant_id
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

        auth = await self.auth_adapter(tenant_id=tenant_id)
        base_url = auth["sonarqube_url"].rstrip("/")
        url = f"{base_url}{path}"

        client = get_async_client(timeout=30)
        resp = await client.request(
            method,
            url,
            auth=(auth["token"], ""),
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
                connector="sonarqube", tenant_id=tenant_id
            ).inc()
            resp.raise_for_status()

        resp.raise_for_status()

        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def _sonar_request_with_retry(
        self,
        method: str,
        path: str,
        tenant_id: str = "",
        **kwargs: Any,
    ) -> tuple[Any, int]:
        """Execute a SonarQube request, retrying once on 429; return (data, retry_count)."""
        tenant_id = tenant_id or self._tenant_id
        retry_count = 0
        for attempt in range(2):
            try:
                data = await self._sonar_request(method, path, tenant_id, **kwargs)
                state = self.__class__._tenant_states.get(tenant_id)
                retry_count = state.retry_count if state else 0
                return data, retry_count
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt == 0:
                    retry_count += 1
                    continue
                raise
        raise RuntimeError("SonarQube request retry exhausted")  # pragma: no cover

    # ── Canonicalisation helpers ──────────────────────────────────────────

    def _canonical_issue(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row.get("key", ""),
            "key": row.get("key", ""),
            "rule": row.get("rule", ""),
            "severity": row.get("severity", ""),
            "status": row.get("status", ""),
            "type": row.get("type", ""),
            "message": row.get("message", ""),
            "component": row.get("component", ""),
            "line": row.get("line"),
            "assignee": row.get("assignee", ""),
            "creationDate": row.get("creationDate", ""),
        }

    # ── CRUD operations ───────────────────────────────────────────────────

    async def list_projects(self, project: str = "") -> List[Dict[str, Any]]:
        """GET /api/projects/search → project picker [{name, key}]."""
        params: Dict[str, Any] = {"ps": 500}
        if project:
            params["q"] = project
        data, _ = await self._sonar_request_with_retry(
            "GET", "/api/projects/search", params=params
        )
        components = data.get("components", []) if isinstance(data, dict) else []
        return [
            {"name": p.get("name") or p.get("key", ""), "key": p.get("key", "")}
            for p in components
        ]

    async def get_quality_gate_status(self, project: str) -> Dict[str, Any]:
        """GET /api/qualitygates/project_status?projectKey=... → PASSED/FAILED + conditions."""
        data, _ = await self._sonar_request_with_retry(
            "GET", "/api/qualitygates/project_status", params={"projectKey": project}
        )
        status = (data or {}).get("projectStatus", {})
        return {
            "status": status.get("status", ""),
            "conditions": [
                {
                    "metric": c.get("metricKey", ""),
                    "status": c.get("status", ""),
                    "actual": c.get("actualValue", ""),
                    "threshold": c.get("errorThreshold", ""),
                }
                for c in status.get("conditions", [])
            ],
        }

    _DEFAULT_METRICS = (
        "coverage,bugs,vulnerabilities,code_smells,security_rating,"
        "reliability_rating,sqale_rating,duplicated_lines_density,ncloc"
    )

    async def get_measures(self, project: str, metric_keys: str = "") -> Dict[str, Any]:
        """GET /api/measures/component → {metric: value} for the requested metrics.

        Defaults to the metric set a Testing/Review agent actually acts on: coverage,
        bug/vulnerability/code-smell counts, the three letter ratings, duplication, and
        lines of code — not the full ~200-metric catalogue SonarQube exposes.
        """
        data, _ = await self._sonar_request_with_retry(
            "GET",
            "/api/measures/component",
            params={"component": project, "metricKeys": metric_keys or self._DEFAULT_METRICS},
        )
        measures = (data or {}).get("component", {}).get("measures", [])
        return {m.get("metric", ""): m.get("value", "") for m in measures}

    async def list_issues(
        self,
        project: str,
        state: str = "",
        team: Optional[str] = None,
        severities: str = "",
    ) -> List[Dict[str, Any]]:
        """GET /api/issues/search → open (or filtered) issues for a project.

        `state` maps to SonarQube's statuses param (OPEN, CONFIRMED, RESOLVED,
        REOPENED, CLOSED) — kept as the generic board-tool parameter name so the same
        agent tool shape works whether the connector behind it is a board or a scanner.
        """
        params: Dict[str, Any] = {"componentKeys": project, "ps": 100}
        if state:
            params["statuses"] = state.upper()
        if severities:
            params["severities"] = severities.upper()
        data, _ = await self._sonar_request_with_retry(
            "GET", "/api/issues/search", params=params
        )
        issues = (data or {}).get("issues", [])
        return [self._canonical_issue(i) for i in issues]

    async def fetch_issue_detail(self, project: str = "", issue_key: str = "") -> Dict[str, Any]:
        """GET /api/issues/search?issues={key} → single issue detail.

        SonarQube's Web API has no GET-by-id endpoint for issues; the search endpoint
        accepts a single key and returns exactly one result, so that is the read.
        """
        data, _ = await self._sonar_request_with_retry(
            "GET", "/api/issues/search", params={"issues": issue_key}
        )
        issues = (data or {}).get("issues", [])
        if not issues:
            raise ValueError(f"No SonarQube issue found for key {issue_key!r}")
        return self._canonical_issue(issues[0])

    async def list_quality_gates(self) -> List[Dict[str, Any]]:
        """GET /api/qualitygates/list → every quality gate defined on the server."""
        data, _ = await self._sonar_request_with_retry("GET", "/api/qualitygates/list")
        gates = (data or {}).get("qualitygates", [])
        return [
            {"id": str(g.get("id", "")), "name": g.get("name", ""), "isDefault": g.get("isDefault", False)}
            for g in gates
        ]

    async def create_project(
        self, name: str, key: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        """POST /api/projects/create → the created project's key/name."""
        project_key = key or self._derive_project_key(name)
        data, _ = await self._sonar_request_with_retry(
            "POST", "/api/projects/create", params={"name": name, "project": project_key}
        )
        project = (data or {}).get("project", {})
        return {
            "key": project.get("key", project_key),
            "name": project.get("name", name),
        }

    @staticmethod
    def _derive_project_key(name: str) -> str:
        """A valid-shaped SonarQube project key from a name: lowercase, dash-joined."""
        cleaned = "".join(c if c.isalnum() else "-" for c in name.lower())
        while "--" in cleaned:
            cleaned = cleaned.replace("--", "-")
        return cleaned.strip("-") or "project"

    async def delete_project(self, project: str, **kwargs: Any) -> Dict[str, Any]:
        """POST /api/projects/delete — permanently delete the project and its history."""
        await self._sonar_request_with_retry(
            "POST", "/api/projects/delete", params={"project": project}
        )
        return {"project": project, "deleted": True}

    async def add_comment(self, issue_key: str, text: str, **kwargs: Any) -> Dict[str, Any]:
        """POST /api/issues/add_comment."""
        data, _ = await self._sonar_request_with_retry(
            "POST", "/api/issues/add_comment", params={"issue": issue_key, "text": text}
        )
        issue = (data or {}).get("issue", {})
        return self._canonical_issue(issue) if issue else {"issue_key": issue_key}

    _VALID_TRANSITIONS = {
        "confirm", "unconfirm", "reopen", "resolve", "close",
        "falsepositive", "wontfix", "accept",
    }

    async def transition_issue(
        self, project: str = "", issue_key: str = "", target_state: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        """POST /api/issues/do_transition — move an issue through its workflow.

        `target_state` accepts SonarQube's own transition names (resolve, reopen,
        confirm, falsepositive, wontfix, accept, close) case-insensitively, matching
        the board connectors' move_item_state contract.
        """
        transition = (target_state or "").strip().lower()
        if transition not in self._VALID_TRANSITIONS:
            raise ValueError(
                f"Unknown SonarQube transition {target_state!r}. "
                f"Valid transitions: {sorted(self._VALID_TRANSITIONS)}"
            )
        data, _ = await self._sonar_request_with_retry(
            "POST", "/api/issues/do_transition", params={"issue": issue_key, "transition": transition}
        )
        issue = (data or {}).get("issue", {})
        return self._canonical_issue(issue) if issue else {"issue_key": issue_key, "transition": transition}

    async def assign_issue(self, issue_key: str, assignee: str = "", **kwargs: Any) -> Dict[str, Any]:
        """POST /api/issues/assign. Empty assignee un-assigns the issue."""
        params: Dict[str, Any] = {"issue": issue_key}
        if assignee:
            params["assignee"] = assignee
        data, _ = await self._sonar_request_with_retry(
            "POST", "/api/issues/assign", params=params
        )
        issue = (data or {}).get("issue", {})
        return self._canonical_issue(issue) if issue else {"issue_key": issue_key, "assignee": assignee}

    async def set_issue_severity(self, issue_key: str, severity: str, **kwargs: Any) -> Dict[str, Any]:
        """POST /api/issues/set_severity.

        [ASSUMED] Deprecated on newer SonarQube server versions in favour of the
        automatic severity model — kept because self-hosted/older servers (the common
        case for an on-prem SonarQube) still honour it, and a 404 here is a clear,
        actionable failure rather than a missing capability.
        """
        data, _ = await self._sonar_request_with_retry(
            "POST", "/api/issues/set_severity", params={"issue": issue_key, "severity": severity.upper()}
        )
        issue = (data or {}).get("issue", {})
        return self._canonical_issue(issue) if issue else {"issue_key": issue_key, "severity": severity}

    async def set_quality_gate(self, project: str, gate_name: str, **kwargs: Any) -> Dict[str, Any]:
        """POST /api/qualitygates/select — assign a named quality gate to a project."""
        await self._sonar_request_with_retry(
            "POST", "/api/qualitygates/select", params={"projectKey": project, "gateName": gate_name}
        )
        return {"project": project, "gate": gate_name}
