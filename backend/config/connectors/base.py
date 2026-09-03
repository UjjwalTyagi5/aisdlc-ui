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
from typing import TYPE_CHECKING, Any

from config.connectors.models import (
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
)

if TYPE_CHECKING:  # import cycle at runtime: authz imports services imports connectors
    from shared.authz.project_credential import ProjectCredentialFields


class ConnectorNotAvailableError(Exception):
    """Raised when no connector is configured for the requested kind."""


#: Connectors whose credential belongs to a PERSON, not to an organisation.
#:
#: For these there is exactly ONE place a credential may come from: the project-scoped
#: credential the acting user saved for themselves. No tenant-wide rung, no key-vault
#: rung, no environment rung.
#:
#: WHY THE FALLBACK HAD TO GO. A tenant-wide token makes a connector work for a project
#: whose members never gave it one — the Integrations page says "Needs a credential"
#: and the agent reaches the system anyway. Worse, the external system then records the
#: work against whoever minted that shared token, so the audit trail names the wrong
#: person confidently. "It works but attributes wrongly" is a harder failure to notice
#: than "it does not work".
#:
#: DELIBERATELY NOT HERE, and each for a reason that is not laziness:
#:   ms_teams, sharepoint, msgraph  an app registration, not a person. There is no
#:                                  personal credential to fall back TO.
#:   slack                          a bot token identifies an APP in a workspace; every
#:                                  member would paste the same value. It is also what
#:                                  notify_dispatch sends with, and that has no user at
#:                                  all — removing the rung would delete notifications.
PERSONAL_CREDENTIAL_KINDS = frozenset({
    "azure_devops",
    "azure_repos",
    "azure_pipelines",
    "jira",
    "confluence",
    "github",
    "github_issues",
    "github_actions",
    "sonarqube",
    "figma",
})


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

    def _tenant_fallback_allowed(self) -> bool:
        """May this connector fall back to a tenant-wide credential?

        False for anything in PERSONAL_CREDENTIAL_KINDS. A connector with no personal
        credential for the acting user must fail as "not connected" rather than borrow
        one — see that constant for why.
        """
        return self.connector_name not in PERSONAL_CREDENTIAL_KINDS

    async def _resolve_credential_override(
        self, tenant_id: str, target_id: str
    ) -> "ProjectCredentialFields | None":
        """The credential override for THIS call, if any — checked before falling
        back to the tenant-wide credential in auth_adapter().

        Returns the WHOLE credential, not just the token: base_url and account
        (the site/organization URL, and the email or owner that goes with it)
        travel with the secret because a connector needs all three to
        authenticate, and a token pointing at somebody else's tenant-wide URL
        authenticates against the wrong instance. Fields may individually be
        None; each caller falls back per field, so a credential saved before
        base_url existed keeps working unchanged.

        `target_id` is the connector's own kind name (e.g. "jira") — the caller
        does NOT pass `kind="connector"` separately; it's hardcoded below to match
        the `kind` column `project_integration_credentials` actually stores for
        every connector-type row (`ProjectIntegrationKind` on the frontend is
        "connector" | "mcp", never a specific connector name — passing the
        connector's own name as `kind` here would look up a row that can never
        exist, which is exactly the bug this note is here to stop someone
        reintroducing).

        Two sources, in order:
          1. `self._credential_override` and its `_base_url` / `_account`
             companions — the ad-hoc, not-yet-saved values a Test Connection
             request is validating. Never written to secret_store. Testing the
             token against a different URL than the one being saved would report
             a pass for a credential that then fails, so all three are carried.
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
        from shared.authz.project_credential import (  # noqa: PLC0415
            ProjectCredentialFields,
            resolve_project_credential,
        )

        override = getattr(self, "_credential_override", None)
        if override:
            return ProjectCredentialFields(
                base_url=getattr(self, "_credential_override_base_url", None) or None,
                account=getattr(self, "_credential_override_account", None) or None,
                token=override,
            )
        project_id = getattr(self, "_project_id_for_creds", "")
        owner_id = getattr(self, "_project_owner_id", "")
        if not (project_id and owner_id):
            return None

        return await resolve_project_credential(
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
