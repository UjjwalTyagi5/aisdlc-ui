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
