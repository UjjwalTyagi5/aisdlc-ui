"""Abstract interface for enterprise connectors (milestone-3).

BaseConnector is the full enterprise connector contract that replaces the
legacy provider ABC. Beyond read/write dispatch it mandates auth,
capability declaration, webhook receipt, per-tenant rate limiting, health
checks, and audit emission. Agent tools call only these methods via the
contextvar in context.py — they never import a backend SDK directly.

Concrete per-operation CRUD methods (list_projects, create_item, …) are NOT
abstract here — they become convenience methods on the concrete connector
(e.g. AzureDevOpsConnector) and are reached through read_adapter/write_adapter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from config.connectors.models import (
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
)


class ConnectorNotAvailableError(Exception):
    """Raised when no connector is configured for the requested kind."""


class BaseConnector(ABC):
    """Full enterprise connector contract. 10 abstract members."""

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Machine-readable id, e.g. 'azure_devops', 'jira'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in logs and error messages."""

    # ── Auth ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve an ephemeral credential context for a single task.

        tenant_id must be a non-empty string in connector paths — implementations
        raise ValueError when absent (REQ-M7-01, SC-02). The caller (agent tool /
        connector factory) is responsible for supplying a valid tenant_id.

        Callers must NOT log or persist this return value, and implementations
        must NOT store it on self.* — credentials may not survive task
        completion (REQ-M3-10).
        """

    # ── Project-scoped credential override ───────────────────────────────

    async def _resolve_credential_override(self, tenant_id: str, target_id: str) -> "str | None":
        """The primary-credential override for THIS call, if any — checked before
        falling back to the tenant-wide credential in auth_adapter().

        `target_id` is the connector's own kind name (e.g. "jira") — the caller
        does NOT pass `kind="connector"` separately; it's hardcoded below to match
        the `kind` column `project_integration_credentials` actually stores for
        every connector-type row (`ProjectIntegrationKind` on the frontend is
        "connector" | "mcp", never a specific connector name — passing the
        connector's own name as `kind` here would look up a row that can never
        exist, which is exactly the bug this note is here to stop someone
        reintroducing).

        Two sources, in order:
          1. `self._credential_override` — the ad-hoc, not-yet-saved value a Test
             Connection request is validating. Never written to secret_store.
          2. The project-scoped personal credential a project member saved for
             themselves (`project_integration_credentials`), if the connector
             factory attached project/owner context — see
             config/connector_factory.py::get_connector_for_session.

        NOT a violation of auth_adapter's "never store the resolved credential on
        self" rule (REQ-M3-10): `_credential_override` is the one-shot value this
        single ephemeral instance exists to validate, and
        `_project_id_for_creds`/`_project_owner_id` are routing identifiers, not
        secrets — the same category as `_tenant_id`/`_org_url`, which every
        connector already stores on self.
        """
        override = getattr(self, "_credential_override", None)
        if override:
            return override
        project_id = getattr(self, "_project_id_for_creds", "")
        owner_id = getattr(self, "_project_owner_id", "")
        if not (project_id and owner_id):
            return None
        from shared.authz.project_credential import resolve_project_secret  # noqa: PLC0415

        return await resolve_project_secret(
            tenant_id=tenant_id, project_id=project_id, owner_id=owner_id,
            kind="connector", target_id=target_id,
        )

    # ── Capability declaration ────────────────────────────────────────────

    @abstractmethod
    def capability_manifest(self) -> CapabilityManifest:
        """Pure metadata: which read/write/listen capabilities are implemented."""

    # ── Webhooks ──────────────────────────────────────────────────────────

    @abstractmethod
    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        """Handle an inbound webhook payload from the backend."""

    # ── Rate limiting ─────────────────────────────────────────────────────

    @abstractmethod
    async def rate_limit_manager(self, tenant_id: str) -> None:
        """Throttle calls per tenant.

        Must NOT use a global rate limit; one tenant cannot block another
        (REQ-M3-11).
        """

    # ── Health ────────────────────────────────────────────────────────────

    @abstractmethod
    async def health_check(self) -> ConnectorHealth:
        """Probe backend reachability; return status + latency."""

    # ── Audit ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def audit_emitter(self, event: ConnectorAuditEvent) -> None:
        """Emit an audit event for a connector call.

        Must NOT raise — log and swallow exceptions so auditing never breaks
        the calling tool.
        """

    # ── Read / write dispatch ─────────────────────────────────────────────

    @abstractmethod
    async def read_adapter(self, operation: str, **kwargs: Any) -> Any:
        """Dispatch a named read operation (list_projects, fetch_item_detail, …)."""

    @abstractmethod
    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        """Dispatch a named write operation (create_item, move_item_state, …)."""
