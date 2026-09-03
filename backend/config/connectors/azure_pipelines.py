"""Azure Pipelines connector — list, create and run ADO pipelines.

The gap this fills: `azure_devops.py` is work items and wiki, `azure_repos.py` is
repositories. Nothing could see a build definition, let alone create or start one, so
the deployment agent could write an `azure-pipelines.yml` into a PR and go no further.

CREDENTIALS ARE SHARED WITH THE REST OF ADO. One `ado-pat` covers boards, repos and
CI/CD, which is why this connector is registered as a kind but deliberately kept out of
the Integrations catalogue: it is part of the consolidated Azure DevOps tile, exactly as
`azure_repos` is. Adding it to `_CATALOG_KINDS` would make the dashboard count a
connector the user cannot separately connect.

WHAT A RUN ACTUALLY SAYS. ADO reports `state` (inProgress / completed / …) separately
from `result` (succeeded / failed / …), and `result` is meaningless until the state is
`completed`. Reading `result` alone makes a running deploy look like a failure — so
`_canonical_run` keeps them apart and reports "running" as its own status rather than
collapsing it into an outcome.

WHY THE TIMELINE MATTERS. A failed deployment reported as "the pipeline failed" tells
nobody anything. `get_run_timeline` returns the per-stage/job/task breakdown so the
failing step can be named, which is the whole difference between a useful report and a
plausible one.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import shared.keyvault as _keyvault
from config.connectors.base import BaseConnector, ConnectorNotAvailableError
from config.connectors.http_client import get_async_client
from config.connectors.models import (
    CapabilityEntry,
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
)
from config.connectors.rate_limit import _TenantRateLimitState, await_backoff

logger = logging.getLogger(__name__)

_API = "7.1"

#: ADO run `result` values → canonical outcomes. Only meaningful once state is
#: "completed"; see `_canonical_run`.
_RESULT_MAP: Dict[str, str] = {
    "succeeded": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
    "partiallySucceeded": "partially_succeeded",
}

#: ADO run `state` values → canonical statuses for a run that has not finished.
_STATE_MAP: Dict[str, str] = {
    "inProgress": "running",
    "notStarted": "queued",
    "canceling": "canceling",
    "postponed": "queued",
}


def _canonical_run(raw: Dict[str, Any]) -> Dict[str, Any]:
    """One run in a stable shape, with state and result kept apart.

    A run that is still going has NO result. Reporting its empty result as a failure is
    the obvious bug here, so an unfinished run gets its status from `state` and carries
    `result: None` rather than a guess.
    """
    state = str(raw.get("state") or "")
    result = str(raw.get("result") or "")
    finished = state == "completed"
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "url": (raw.get("_links") or {}).get("web", {}).get("href") or raw.get("url"),
        "state": state or "unknown",
        "finished": finished,
        # None while running — the caller must not read this as "not succeeded".
        "result": _RESULT_MAP.get(result, result or None) if finished else None,
        "status": (
            _RESULT_MAP.get(result, result or "unknown") if finished
            else _STATE_MAP.get(state, "unknown")
        ),
        "created_date": raw.get("createdDate"),
        "finished_date": raw.get("finishedDate"),
    }


class AzurePipelinesConnector(BaseConnector):
    """Azure Pipelines — build definitions and their runs.

    Per-tenant rate-limit state is class-level so one tenant's backoff never blocks
    another (REQ-M6-12), matching AzureDevOpsConnector and AzureReposConnector.
    """

    _tenant_states: Dict[str, _TenantRateLimitState] = {}
    _tenant_semaphores: Dict[str, asyncio.Semaphore] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        self._org_url = (org_url or "").rstrip("/")
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "azure_pipelines"

    @property
    def display_name(self) -> str:
        return "Azure Pipelines"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve the ADO PAT ephemerally — the same credential as boards and repos.

        Never stored on self, never logged. tenant_id is required: connector
        credentials are per-tenant (REQ-M7-01).
        """
        tenant_id = tenant_id or self._tenant_id
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for AzurePipelinesConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )

        # THE PROJECT-SCOPED CREDENTIAL COMES FIRST, and reading it is not optional.
        # The Integrations page lets a project member save an Azure DevOps PAT against
        # their own project, and that is where a real tenant's credential usually
        # lives — a tenant-wide "ado-pat" may not exist at all. Skipping this rung
        # means every pipeline call reports "credentials are not configured" while the
        # credential sits in the database, which is the most misleading form of
        # working-as-designed there is.
        #
        # The override is looked up under "azure_devops", NOT "azure_pipelines". Same
        # reasoning as the grant alias in shared/authz/connector_grants: pipelines has
        # no Integrations tile of its own, so no row is ever written under its name,
        # and asking for one looks up a credential that can never exist.
        override = await self._resolve_credential_override(tenant_id, "azure_devops")
        if override and override.token:
            # base_url travels with the token: a PAT pointing at somebody else's
            # organisation authenticates against the wrong ADO.
            return {
                "org_url": (override.base_url or self._org_url or "").rstrip("/"),
                "pat": override.token,
            }

        pat = await _keyvault.load_secret("ado-pat", tenant_id=tenant_id)
        return {"org_url": self._org_url, "pat": pat}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="azure_pipelines",
            read_capabilities={
                "list_pipelines": CapabilityEntry(
                    status="implemented",
                    description="GET {project}/_apis/pipelines — id, name, folder.",
                ),
                "get_pipeline": CapabilityEntry(
                    status="implemented",
                    description="GET {project}/_apis/pipelines/{id} — includes the YAML path.",
                ),
                "list_runs": CapabilityEntry(
                    status="implemented",
                    description="GET {project}/_apis/pipelines/{id}/runs, newest first.",
                ),
                "get_run": CapabilityEntry(
                    status="implemented",
                    description=(
                        "GET {project}/_apis/pipelines/{id}/runs/{run_id}. State and "
                        "result are reported separately — result is null until the run "
                        "completes."
                    ),
                ),
                "get_run_timeline": CapabilityEntry(
                    status="implemented",
                    description=(
                        "GET {project}/_apis/build/builds/{run_id}/timeline — per-stage "
                        "and per-task records, so a failure can name the step that failed."
                    ),
                ),
                "list_service_connections": CapabilityEntry(
                    status="implemented",
                    description=(
                        "GET {project}/_apis/serviceendpoint/endpoints — what the project "
                        "can actually deploy to. Generating a pipeline that references a "
                        "service connection that does not exist fails on first run."
                    ),
                ),
                "download_run_logs": CapabilityEntry(
                    status="not_supported",
                    description=(
                        "Returns a zip archive of the whole run. The timeline names the "
                        "failing task without moving log volume through the platform."
                    ),
                ),
            },
            write_capabilities={
                "create_pipeline": CapabilityEntry(
                    status="implemented",
                    description=(
                        "POST {project}/_apis/pipelines — registers a YAML file already "
                        "in the repo as a pipeline definition. It does NOT write the YAML; "
                        "the file must be committed first or ADO rejects the create."
                    ),
                ),
                "run_pipeline": CapabilityEntry(
                    status="implemented",
                    description=(
                        "POST {project}/_apis/pipelines/{id}/runs — queues a run on a "
                        "branch. Consequential: this is what actually deploys."
                    ),
                ),
                "cancel_run": CapabilityEntry(
                    status="implemented",
                    description="PATCH {project}/_apis/build/builds/{run_id} to cancel.",
                ),
                "update_pipeline_yaml": CapabilityEntry(
                    status="not_supported",
                    description=(
                        "Pipeline YAML lives in the repo, not in the pipeline resource. "
                        "It is changed by committing the file — the deployment PR path — "
                        "not through this API."
                    ),
                ),
            },
            listen_capabilities={
                "pipeline_run": CapabilityEntry(
                    status="not_supported",
                    description=(
                        "ADO service hooks carry no HMAC signature, so an inbound run "
                        "webhook would need the Basic-Auth verifier azure_repos uses. "
                        "Deferred: run status is polled instead, which needs no inbound "
                        "surface at all."
                    ),
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Not accepted — declared `not_supported` above.

        Returning a fabricated acknowledgement would let a caller believe run events
        were being received when nothing is listening.
        """
        raise ConnectorNotAvailableError(
            "azure_pipelines does not accept webhooks; run status is polled via get_run."
        )

    # ── Rate limiting ─────────────────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe ADO reachability.

        MUST NOT RAISE. A connector whose health_check raises is dropped from the
        health cache, and /connectors/health then re-probes inline on every request
        (see _EXPECTED_CONNECTOR_NAMES in config/connectors/router.py).

        Reports type(exc).__name__ only — never str(exc), which can carry the PAT.
        """
        start = time.time()
        try:
            await self._probe()
            return ConnectorHealth(
                connector_name="azure_pipelines",
                status="healthy",
                latency_ms=(time.time() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 — must never propagate; see docstring
            return ConnectorHealth(
                connector_name="azure_pipelines",
                status="unhealthy",
                latency_ms=(time.time() - start) * 1000,
                error=type(exc).__name__,
            )

    async def _probe(self) -> None:
        auth = await self.auth_adapter()
        if not auth.get("org_url") or not auth.get("pat"):
            raise ConnectorNotAvailableError("ADO credentials not configured")
        client = get_async_client(timeout=10)
        resp = await client.get(
            f"{auth['org_url']}/_apis/projects?api-version={_API}&$top=1",
            auth=("", auth["pat"]),
        )
        resp.raise_for_status()

    # ── Audit ─────────────────────────────────────────────────────────────

    async def audit_emitter(self, event: ConnectorAuditEvent) -> None:
        from shared.services.metrics import observe_connector_call

        observe_connector_call(event)
        from shared.audit.models import AuditEventPayload
        from shared.audit.service import audit_service

        await audit_service.emit(
            AuditEventPayload(
                tenant_id=str(event.tenant_id),
                run_id=getattr(event, "run_id", None),
                event_type="connector_call",
                resource_type=event.connector_name,
                resource_id=event.method,
                agent_type=event.connector_name,
                actor_id=f"system:{event.connector_name}",
                payload=event.model_dump(),
            )
        )

    # ── HTTP ──────────────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, *, project: str, params: Optional[dict] = None,
        json_body: Optional[dict] = None, api_version: str = _API,
    ) -> Any:
        """One authenticated call against a project-scoped ADO endpoint."""
        auth = await self.auth_adapter()
        org_url, pat = auth.get("org_url"), auth.get("pat")
        if not org_url or not pat:
            raise ConnectorNotAvailableError("ADO credentials not configured")
        if not project:
            raise ValueError("project is required for an Azure Pipelines call")

        await self.rate_limit_manager(self._tenant_id or "")
        url = f"{org_url}/{project}/_apis/{path}"
        client = get_async_client(timeout=30)
        resp = await client.request(
            method, url, auth=("", pat), json=json_body,
            params={**(params or {}), "api-version": api_version},
        )
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()

    # ── Reads ─────────────────────────────────────────────────────────────

    async def list_pipelines(self, project: str) -> List[Dict[str, Any]]:
        data = await self._request("GET", "pipelines", project=project)
        return [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "folder": p.get("folder"),
                "url": (p.get("_links") or {}).get("web", {}).get("href"),
            }
            for p in data.get("value", [])
        ]

    async def get_pipeline(self, project: str, pipeline_id: int) -> Dict[str, Any]:
        data = await self._request("GET", f"pipelines/{pipeline_id}", project=project)
        cfg = data.get("configuration") or {}
        repo = cfg.get("repository") or {}
        # ADO SENDS ONLY id AND type HERE — never the repository name, confirmed
        # against a real organisation. Reading `name` gave None for every YAML
        # pipeline, which reads as "this pipeline has no repository" rather than
        # "the API did not say". The id is what is actually present, and it is the
        # field `create_pipeline` needs back, so it is the one surfaced.
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "folder": data.get("folder"),
            "yaml_path": cfg.get("path"),
            "repository_id": repo.get("id"),
            "repository_type": repo.get("type"),
            # Present only when ADO happens to include it; absent is normal.
            "repository_name": repo.get("name"),
            "type": cfg.get("type"),
        }

    async def list_runs(
        self, project: str, pipeline_id: int, top: int = 20
    ) -> List[Dict[str, Any]]:
        data = await self._request(
            "GET", f"pipelines/{pipeline_id}/runs", project=project
        )
        runs = [_canonical_run(r) for r in data.get("value", [])]
        return runs[:top]

    async def get_run(
        self, project: str, pipeline_id: int, run_id: int
    ) -> Dict[str, Any]:
        return _canonical_run(
            await self._request(
                "GET", f"pipelines/{pipeline_id}/runs/{run_id}", project=project
            )
        )

    async def get_run_timeline(self, project: str, run_id: int) -> Dict[str, Any]:
        """Per-stage/task breakdown, reduced to what failed.

        The point is naming the failing step. `failed` carries the records that
        actually went wrong, so a caller does not have to re-derive them from a list
        that is mostly successes.
        """
        data = await self._request(
            "GET", f"build/builds/{run_id}/timeline", project=project
        )
        records = data.get("records") or []
        rows = [
            {
                "name": r.get("name"),
                "type": r.get("type"),
                "state": r.get("state"),
                "result": r.get("result"),
                "issues": [
                    {"type": i.get("type"), "message": i.get("message")}
                    for i in (r.get("issues") or [])
                ],
            }
            for r in records
        ]
        return {
            "records": rows,
            "failed": [r for r in rows if r["result"] in ("failed", "canceled")],
        }

    async def list_service_connections(self, project: str) -> List[Dict[str, Any]]:
        """What this project can deploy to.

        Used before generating a pipeline: referencing a service connection the
        project does not have produces YAML that fails on its first run.
        """
        data = await self._request(
            "GET", "serviceendpoint/endpoints", project=project,
            api_version="7.1-preview.4",
        )
        return [
            {"id": e.get("id"), "name": e.get("name"), "type": e.get("type")}
            for e in data.get("value", [])
        ]

    # ── Writes ────────────────────────────────────────────────────────────

    async def create_pipeline(
        self, project: str, name: str, yaml_path: str, repository_id: str,
        repository_name: str = "", folder: str = "\\",
        repository_type: str = "azureReposGit",
    ) -> Dict[str, Any]:
        """Register a YAML file already in the repo as a pipeline definition.

        THE FILE MUST BE COMMITTED FIRST. This registers a path; it does not create
        the YAML. ADO rejects the create when the path does not resolve on the repo's
        default branch, which is the correct order: the deployment PR merges, then the
        pipeline is created against the merged file.
        """
        body = {
            "folder": folder,
            "name": name,
            "configuration": {
                "type": "yaml",
                "path": yaml_path if yaml_path.startswith("/") else f"/{yaml_path}",
                "repository": {
                    "id": repository_id,
                    "name": repository_name or repository_id,
                    "type": repository_type,
                },
            },
        }
        data = await self._request("POST", "pipelines", project=project, json_body=body)
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "url": (data.get("_links") or {}).get("web", {}).get("href"),
        }

    async def run_pipeline(
        self, project: str, pipeline_id: int, branch: str = "",
        variables: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Queue a run. CONSEQUENTIAL — this is the call that deploys."""
        body: Dict[str, Any] = {}
        if branch:
            ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
            body["resources"] = {"repositories": {"self": {"refName": ref}}}
        if variables:
            body["variables"] = {
                k: {"value": str(v), "isSecret": False} for k, v in variables.items()
            }
        return _canonical_run(
            await self._request(
                "POST", f"pipelines/{pipeline_id}/runs", project=project, json_body=body
            )
        )

    async def cancel_run(self, project: str, run_id: int) -> Dict[str, Any]:
        data = await self._request(
            "PATCH", f"build/builds/{run_id}", project=project,
            json_body={"status": "Cancelling"},
        )
        return {"id": data.get("id"), "status": data.get("status")}

    # ── Read / write dispatch ─────────────────────────────────────────────

    async def read_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "list_pipelines": self.list_pipelines,
            "get_pipeline": self.get_pipeline,
            "list_runs": self.list_runs,
            "get_run": self.get_run,
            "get_run_timeline": self.get_run_timeline,
            "list_service_connections": self.list_service_connections,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown read operation: {operation!r}")
        return await fn(**kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP = {
            "create_pipeline": self.create_pipeline,
            "run_pipeline": self.run_pipeline,
            "cancel_run": self.cancel_run,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)
