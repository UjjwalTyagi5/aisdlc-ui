"""A connector bound to an access level.

WHY A WRAPPER AND NOT A CHECK AT EACH CALL SITE. Agents reach connectors through
`connector.read_adapter(...)` / `connector.write_adapter(...)` from roughly twenty
places across eight modules, and every new agent adds more. A permission check
written at those call sites is a check somebody will forget, and the one they forget
is the one that matters. There is exactly one place every connector is obtained —
`get_connector_for_session` — so that is where the level is bound, and every existing
call site becomes governed without being edited.

DENIAL IS AN EXCEPTION, NOT AN EMPTY RESULT. `ConnectorAccessDenied` is raised rather
than returning None or []: a write that silently does nothing looks to an agent like a
write that succeeded, and it would go on to report progress it did not make. The agent
frameworks already surface tool exceptions as failed steps, which is the honest
outcome.

IT REFUSES BY DEFAULT. `ScopedConnector(inner, None)` permits nothing. The wrapper is
never the thing that decides an access level — it only enforces one it was handed —
so a caller that could not resolve a level gets a connector that does nothing rather
than one that does everything.

THE MANIFEST IS NARROWED TOO. `capability_manifest()` reports only the capabilities
the level admits, so anything asking a connector what it can do — the capabilities
API, an agent planning its own steps — is told the truth for THIS project rather than
the connector's theoretical maximum.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from config.connectors.base import BaseConnector
from config.connectors.models import CapabilityManifest, ConnectorAuditEvent, ConnectorHealth
from shared.authz.connector_access import AccessLevel, label, permits

logger = logging.getLogger(__name__)


class ConnectorAccessDenied(PermissionError):
    """A connector operation the caller's grant does not admit.

    PermissionError rather than a bespoke base: callers that already handle
    permission failures treat it correctly without importing this module.
    """

    def __init__(self, connector: str, mode: str, level: Optional[str]) -> None:
        self.connector = connector
        self.mode = mode
        self.level = level
        super().__init__(
            f"{connector} is {label(level)} for this project — "
            f"a {mode} operation is not permitted."
        )


class ScopedConnector(BaseConnector):
    """Delegates everything to `inner`, gating the two adapters on `access`.

    Subclasses BaseConnector so it satisfies every type hint and isinstance check the
    codebase already has; the abstract methods are all forwarded.
    """

    def __init__(self, inner: BaseConnector, access: Optional[AccessLevel]) -> None:
        self._inner = inner
        self._access = access

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def connector_name(self) -> str:
        return self._inner.connector_name

    @property
    def display_name(self) -> str:
        return self._inner.display_name

    @property
    def access_level(self) -> Optional[str]:
        """The level this instance is bound to. None means it permits nothing."""
        return self._access

    # ── the gate ─────────────────────────────────────────────────────────────
    def _require(self, mode: str) -> None:
        if not permits(self._access, mode):
            logger.warning(
                "connector access denied: %s %s (level=%s)",
                self._inner.connector_name, mode, self._access,
            )
            raise ConnectorAccessDenied(self._inner.connector_name, mode, self._access)

    async def read_adapter(self, operation: str, **kwargs: Any) -> Any:
        self._require("read")
        return await self._inner.read_adapter(operation, **kwargs)

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        self._require("write")
        return await self._inner.write_adapter(operation, **kwargs)

    # ── narrowed metadata ────────────────────────────────────────────────────
    def capability_manifest(self) -> CapabilityManifest:
        """The inner manifest with the capabilities this level cannot reach removed.

        Listen capabilities are left alone: receiving a webhook is neither a read nor
        a write the caller performs, and gating it here would silently stop inbound
        events for a read-only grant, which is the opposite of what read-only means.
        """
        manifest = self._inner.capability_manifest()
        return CapabilityManifest(
            connector_name=manifest.connector_name,
            version=manifest.version,
            read_capabilities=(
                manifest.read_capabilities if permits(self._access, "read") else {}
            ),
            write_capabilities=(
                manifest.write_capabilities if permits(self._access, "write") else {}
            ),
            listen_capabilities=manifest.listen_capabilities,
        )

    # ── straight delegation ──────────────────────────────────────────────────
    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        return await self._inner.auth_adapter(tenant_id)

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        return await self._inner.webhook_receiver(payload)

    async def rate_limit_manager(self, tenant_id: str) -> None:
        return await self._inner.rate_limit_manager(tenant_id)

    async def health_check(self) -> ConnectorHealth:
        return await self._inner.health_check()

    async def audit_emitter(self, event: ConnectorAuditEvent) -> None:
        return await self._inner.audit_emitter(event)

    def __getattr__(self, name: str) -> Any:
        """Forward anything not overridden above to the wrapped connector.

        Connectors carry kind-specific helpers beyond the ABC, and the wrapper must
        stay transparent for those. Only the two adapters are gated — a helper that
        reaches the network on its own would bypass this, which is why connectors are
        required to route through the adapters.
        """
        return getattr(self._inner, name)
