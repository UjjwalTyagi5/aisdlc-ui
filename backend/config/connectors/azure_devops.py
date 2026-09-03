"""Azure DevOps connector implementing the full BaseConnector contract.

Migrated from the legacy provider tree. The decisive difference for
milestone-3: the constructor stores ONLY org_url — never the PAT. Credentials
are resolved ephemerally inside auth_adapter() on every call (REQ-M3-10), so a
worker process never holds a credential beyond a single operation.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from config.ado_ingestion import (
    add_comment_to_work_item,
    create_ado_project,
    create_work_item,
    delete_work_item,
    fetch_hierarchy_tree,
    fetch_work_item,
    get_wiki_page,
    list_all_work_items,
    list_projects,
    list_item_types,
    list_states,
    list_iterations as _list_iterations,
    team_capacity as _team_capacity,
    list_stories_by_state,
    list_teams,
    list_wiki_pages,
    list_wikis,
    normalize_work_item,
    update_work_item_fields,
    update_work_item_state,
)
from config.connectors.base import BaseConnector, ConnectorNotAvailableError
from config.connectors.models import (
    CapabilityEntry,
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
    make_board_item,
)
from shared.keyvault import load_secret

logger = logging.getLogger(__name__)


class AzureDevOpsConnector(BaseConnector):
    # Per-tenant semaphores shared across instances so one tenant cannot exhaust
    # another's rate budget (REQ-M3-11). Keyed by tenant_id; never a global lock.
    _tenant_semaphores: dict[str, asyncio.Semaphore] = {}

    def __init__(self, org_url: str, tenant_id: str = "") -> None:
        # org_url only — no PAT/credential attribute (REQ-M3-10).
        # tenant_id is run context (NOT a credential): the factory sets it so the
        # convenience methods can resolve the tenant-scoped PAT in auth_adapter()
        # without each call site threading it through (REQ-M7-01).
        self._org_url = org_url.rstrip("/")
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "azure_devops"

    @property
    def display_name(self) -> str:
        return "Azure DevOps"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve PAT ephemerally: Key Vault first, env var fallback.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        An explicit tenant_id argument wins; otherwise the instance tenant_id set
        by the factory is used, so convenience methods can call auth_adapter() with
        no argument and still resolve per-tenant. Credentials are resolved
        tenant-scoped only: the tenant secret store, then this tenant's Key Vault
        var (local development only). Return value is never stored on self and must
        not be logged/persisted.
        """
        tid = tenant_id or self._tenant_id
        if not tid:
            raise ValueError(
                "tenant_id is required for AzureDevOpsConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )
        # Project-scoped personal override, checked first: a credential this project
        # member set for themselves — or the ad-hoc value Test Connection is
        # validating — wins over the tenant-wide PAT below.
        override = await self._resolve_credential_override(tid, "azure_devops")
        if override and override.token:
            # org_url comes from the override when the member supplied one.
            # Unlike the other fields it CANNOT be resolved in the factory:
            # _build_connector runs before project/owner context exists, so it
            # only ever knows the tenant-wide URL. Resolving it here is what
            # lets two projects in one tenant point at different organizations.
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

        # Resolution order: tenant secret store (Key Vault in prod, Fernet-encrypted
        # DB in local dev — the path the Integrations "Add credentials" form writes
        # to) → Key Vault "{tenant}-ado-pat". Both rungs are tenant-scoped; there
        # is no global-KV or env rung. The secret_store read is skipped for the
        # health-probe sentinel and never allowed to raise.
        pat = None
        disconnected = False
        if tid != "__health_probe__":
            try:
                from shared.services import secret_store  # lazy: avoid import cycle
                v = await secret_store.get_secret(tid, "ado-pat")
                if v == secret_store.DISCONNECTED_MARKER:
                    disconnected = True  # explicitly disconnected — no fallback
                else:
                    pat = v
            except Exception:  # noqa: BLE001
                pat = None
        if not disconnected:
            if not pat:
                pat = await load_secret("ado-pat", tenant_id=tid)
        return {"org_url": self._org_url, "pat": pat or ""}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="azure_devops",
            read_capabilities={
                "list_projects": CapabilityEntry(status="implemented"),
                "list_teams": CapabilityEntry(status="implemented"),
                "list_states": CapabilityEntry(status="implemented"),
                "list_sprints": CapabilityEntry(
                    status="implemented",
                    description="Team iterations with their date ranges and ADO's own past/current/future classification",
                ),
                "team_capacity": CapabilityEntry(
                    status="implemented",
                    description="Per-person hours per day for a sprint, net of personal and team days off",
                ),
                "list_item_types": CapabilityEntry(status="implemented"),
                "list_stories": CapabilityEntry(status="implemented"),
                "list_all_items": CapabilityEntry(status="implemented"),
                "fetch_item_detail": CapabilityEntry(status="implemented"),
                "fetch_hierarchy": CapabilityEntry(status="implemented"),
                "list_wikis": CapabilityEntry(status="implemented"),
                "get_wiki_page": CapabilityEntry(status="implemented"),
                "list_wiki_pages": CapabilityEntry(status="implemented"),
            },
            write_capabilities={
                "create_item": CapabilityEntry(status="implemented"),
                "update_item_fields": CapabilityEntry(status="implemented"),
                "move_item_state": CapabilityEntry(status="implemented"),
                "add_comment": CapabilityEntry(status="implemented"),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "AzureDevOpsConnector.webhook_receiver not yet implemented"
        )

    # ── Rate limiting (per-tenant) ────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        sem = self.__class__._tenant_semaphores.setdefault(
            tenant_id, asyncio.Semaphore(10)
        )
        async with sem:
            return

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe the credential, not just the organization.

        MUST establish WHO the PAT authenticated as. This used to call
        `list_projects()`, which an organization with public projects answers
        200 for anonymously — so any PAT reported healthy. The identical bug was
        confirmed live on Jira; see JiraConnector.health_check.

        _apis/connectionData is Azure DevOps' "who am I". Unlike Jira and
        Confluence it does NOT 401 an anonymous caller: it answers 200 and
        describes them as the anonymous identity, so the status code is not the
        answer — `authenticatedUser.id` is, and the all-zero GUID is Azure
        DevOps' well-known id for "nobody signed in".
        """
        _ANONYMOUS_ID = "00000000-0000-0000-0000-000000000000"
        start = time.time()
        try:
            auth = await self.auth_adapter()
            org_url = (auth.get("org_url") or "").rstrip("/")
            if not org_url:
                return ConnectorHealth(
                    connector_name="azure_devops",
                    status="unhealthy",
                    latency_ms=(time.time() - start) * 1000,
                    error="no_org_url",
                )
            async with httpx.AsyncClient(timeout=30.0, auth=("", auth.get("pat") or "")) as client:
                # 7.1-PREVIEW.1, not 7.1. connectionData has never left preview,
                # and Azure DevOps answers a released version number on it with
                # HTTP 400 rather than ignoring it. The mistake hides well: an
                # unauthenticated probe 302s to a sign-in page BEFORE the version
                # is ever validated, so testing without a working PAT makes any
                # version look correct.
                resp = await client.get(
                    f"{org_url}/_apis/connectionData?api-version=7.1-preview.1"
                )
                resp.raise_for_status()
                data = resp.json()
            latency_ms = (time.time() - start) * 1000
            user = (data or {}).get("authenticatedUser") or {}
            if str(user.get("id", "")).lower() in ("", _ANONYMOUS_ID):
                return ConnectorHealth(
                    connector_name="azure_devops",
                    status="unhealthy",
                    latency_ms=latency_ms,
                    # 200, but nobody was signed in — the PAT was not accepted.
                    error="not_authenticated",
                )
            return ConnectorHealth(
                connector_name="azure_devops",
                status="healthy",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.time() - start) * 1000
            # NEVER str(exc) — credential leakage risk. HTTP status is safe/diagnostic:
            # 401/203 = bad PAT, 404 = wrong organization URL.
            err = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                err = f"HTTP {exc.response.status_code}"
            return ConnectorHealth(
                connector_name="azure_devops",
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
            "list_projects": self.list_projects,
            "list_teams": self.list_teams,
            "list_states": self.list_states,
            "list_item_types": self.list_item_types,
            "list_stories": self.list_stories,
            "list_all_items": self.list_all_items,
            "fetch_item_detail": self.fetch_item_detail,
            "fetch_hierarchy": self.fetch_hierarchy,
            "list_sprints": self.list_sprints,
            "team_capacity": self.team_capacity,
            "list_wikis": self.list_wikis,
            "get_wiki_page": self.get_wiki_page,
            "list_wiki_pages": self.list_wiki_pages,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "create_project": self.create_project,
            "create_item": self.create_item,
            "update_item": self.update_item_fields,
            "update_item_fields": self.update_item_fields,
            "delete_item": self.delete_item,
            "move_item_state": self.move_item_state,
            "add_comment": self.add_comment,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Canonicalisation helpers ──────────────────────────────────────────

    def _canonical_summary(self, row: Dict[str, Any], project: str, team: str = "") -> Dict[str, Any]:
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=row.get("id") or row.get("work_item_id"),
            source_key=str(row.get("id") or row.get("work_item_id") or ""),
            title=row.get("title", ""),
            item_type=row.get("work_item_type", ""),
            state=row.get("state", ""),
            assigned_to=row.get("assigned_to", ""),
            tags=row.get("tags", []),
            url=row.get("url") or row.get("work_item_url", ""),
            project=project,
            team=team,
            estimate=row.get("estimate"),
            iteration=row.get("iteration_path", ""),
            start_date=row.get("start_date", ""),
            due_date=row.get("due_date", ""),
            remaining_work=row.get("remaining_work"),
            completed_work=row.get("completed_work"),
            priority=row.get("priority"),
            raw=row,
        )

    def _canonical_detail(self, row: Dict[str, Any], project: str) -> Dict[str, Any]:
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=row.get("work_item_id") or row.get("id"),
            source_key=str(row.get("work_item_id") or row.get("id") or ""),
            title=row.get("title", ""),
            item_type=row.get("work_item_type", ""),
            state=row.get("state", ""),
            description=row.get("description", ""),
            acceptance_criteria=row.get("acceptance_criteria", []),
            assigned_to=row.get("assigned_to", ""),
            tags=row.get("tags", []),
            url=row.get("work_item_url") or row.get("url", ""),
            project=project,
            team=row.get("team", ""),
            estimate=row.get("estimate"),
            iteration=row.get("iteration_path", ""),
            start_date=row.get("start_date", ""),
            due_date=row.get("due_date", ""),
            remaining_work=row.get("remaining_work"),
            completed_work=row.get("completed_work"),
            priority=row.get("priority"),
            raw=row,
            organization_url=row.get("organization_url", self._org_url),
            area_path=row.get("area_path", ""),
            iteration_path=row.get("iteration_path", ""),
            created_by=row.get("created_by", ""),
            relations=row.get("relations", []),
        )

    # ── Convenience methods (delegate to ado_ingestion via ephemeral auth) ──

    async def list_projects(self) -> List[Dict[str, Any]]:
        auth = await self.auth_adapter()
        return await list_projects(org_url=auth["org_url"], pat=auth["pat"])

    async def list_teams(self, project: str) -> List[Dict[str, Any]]:
        auth = await self.auth_adapter()
        return await list_teams(org_url=auth["org_url"], project=project, pat=auth["pat"])

    async def list_sprints(self, project: str, team: str = "") -> List[Dict[str, Any]]:
        """The team's iterations. TEAM-SCOPED, because that is how ADO models them —
        a project with three teams has three sprint sets and there is no project-level
        answer. Falls back to the project's first team so a caller that does not know
        or care about teams still gets the common single-team case."""
        auth = await self.auth_adapter()
        team = team or await self._default_team(project, auth)
        return await _list_iterations(
            org_url=auth["org_url"], project=project, team=team, pat=auth["pat"]
        )

    async def team_capacity(
        self, project: str, iteration_id: str, team: str = ""
    ) -> List[Dict[str, Any]]:
        """Per-person capacity for one sprint, with days off already subtracted."""
        auth = await self.auth_adapter()
        team = team or await self._default_team(project, auth)
        return await _team_capacity(
            org_url=auth["org_url"], project=project, team=team,
            iteration_id=iteration_id, pat=auth["pat"],
        )

    async def _default_team(self, project: str, auth: Dict[str, Any]) -> str:
        """The project's first team. ADO names it "<Project> Team" by default, but that
        is a convention rather than a guarantee, so ask rather than construct it."""
        teams = await list_teams(org_url=auth["org_url"], project=project, pat=auth["pat"])
        return teams[0]["name"] if teams else project

    async def list_item_types(self, project: str) -> List[Dict[str, Any]]:
        """Work item types this project's PROCESS TEMPLATE defines — not a fixed list.
        Agile has "User Story", Scrum "Product Backlog Item", Basic "Issue"."""
        auth = await self.auth_adapter()
        return await list_item_types(
            org_url=auth["org_url"], project=project, pat=auth["pat"]
        )

    async def list_states(
        self, project: str, item_type: str = "User Story"
    ) -> List[Dict[str, Any]]:
        auth = await self.auth_adapter()
        return await list_states(
            org_url=auth["org_url"],
            project=project,
            work_item_type=item_type,
            pat=auth["pat"],
        )

    async def list_stories(
        self, project: str, state: str, team: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        auth = await self.auth_adapter()
        rows = await list_stories_by_state(
            org_url=auth["org_url"],
            project=project,
            state=state,
            pat=auth["pat"],
            team=team,
        )
        return [self._canonical_summary(row, project, team or "") for row in rows]

    async def list_all_items(
        self, project: str, team: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        auth = await self.auth_adapter()
        rows = await list_all_work_items(
            org_url=auth["org_url"],
            project=project,
            pat=auth["pat"],
            team=team,
        )
        return [self._canonical_summary(row, project, team or "") for row in rows]

    async def fetch_item_detail(self, project: str, item_id: int) -> Dict[str, Any]:
        auth = await self.auth_adapter()
        wi = await fetch_work_item(
            org_url=auth["org_url"],
            project=project,
            work_item_id=item_id,
            pat=auth["pat"],
        )
        normalized = normalize_work_item(
            work_item=wi, org_url=auth["org_url"], project=project
        )
        return self._canonical_detail(normalized, project)

    async def fetch_hierarchy(self, project: str) -> List[Dict[str, Any]]:
        auth = await self.auth_adapter()
        return await fetch_hierarchy_tree(
            org_url=auth["org_url"], project=project, pat=auth["pat"]
        )

    async def list_wikis(self, project: str) -> List[Dict[str, Any]]:
        auth = await self.auth_adapter()
        return await list_wikis(org_url=auth["org_url"], project=project, pat=auth["pat"])

    async def get_wiki_page(self, project: str, wiki_id: str, path: str = "") -> Dict[str, Any]:
        auth = await self.auth_adapter()
        return await get_wiki_page(
            org_url=auth["org_url"], project=project, wiki_id=wiki_id, path=path, pat=auth["pat"],
        )

    async def list_wiki_pages(self, project: str, wiki_id: str, path_prefix: str = "") -> List[Dict[str, str]]:
        auth = await self.auth_adapter()
        return await list_wiki_pages(
            org_url=auth["org_url"], project=project, wiki_id=wiki_id,
            path_prefix=path_prefix, pat=auth["pat"],
        )

    async def create_item(
        self,
        project: str,
        item_type: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        parent_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        auth = await self.auth_adapter()
        created = await create_work_item(
            org_url=auth["org_url"],
            project=project,
            work_item_type=item_type,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            parent_id=str(parent_id or ""),
            pat=auth["pat"],
        )
        return make_board_item(
            provider_kind=self.connector_name,
            item_id=created.get("id"),
            source_key=str(created.get("id", "")),
            title=title or (created.get("fields", {}) or {}).get("System.Title", ""),
            item_type=item_type,
            state=(created.get("fields", {}) or {}).get("System.State", ""),
            description=description,
            acceptance_criteria=acceptance_criteria,
            url=created.get("_links", {}).get("html", {}).get("href", ""),
            project=project,
            raw=created,
        )

    async def update_item_fields(
        self,
        project: str,
        item_id: int,
        description: str = "",
        acceptance_criteria: str = "",
        title: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        auth = await self.auth_adapter()
        return await update_work_item_fields(
            org_url=auth["org_url"],
            project=project,
            work_item_id=item_id,
            description=description,
            acceptance_criteria=acceptance_criteria,
            title=title,
            pat=auth["pat"],
        )

    async def delete_item(self, project: str, item_id: int, **kwargs: Any) -> Dict[str, Any]:
        auth = await self.auth_adapter()
        return await delete_work_item(
            org_url=auth["org_url"],
            project=project,
            work_item_id=item_id,
            pat=auth["pat"],
        )

    async def create_project(
        self, name: str, description: str = "", process: str = "Agile", **kwargs: Any
    ) -> Dict[str, Any]:
        auth = await self.auth_adapter()
        return await create_ado_project(
            org_url=auth["org_url"],
            name=name,
            description=description,
            process=process,
            pat=auth["pat"],
        )

    async def move_item_state(
        self, project: str, item_id: int, new_state: str
    ) -> Dict[str, Any]:
        auth = await self.auth_adapter()
        return await update_work_item_state(
            org_url=auth["org_url"],
            project=project,
            work_item_id=item_id,
            new_state=new_state,
            pat=auth["pat"],
        )

    async def add_comment(self, project: str, item_id: int, comment: str) -> bool:
        auth = await self.auth_adapter()
        return await add_comment_to_work_item(
            org_url=auth["org_url"],
            project=project,
            work_item_id=item_id,
            comment=comment,
            pat=auth["pat"],
        )
