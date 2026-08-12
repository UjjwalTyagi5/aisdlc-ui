"""Microsoft Teams connector — write-only notification adapter.

The Teams counterpart to SlackConnector, and deliberately shaped like it: write-only,
explicit target with no default fallback, and the same content guard. read_adapter
raises NotImplementedError; write_adapter exposes only notify_teams.

TWO TRANSPORTS, tried in this order:

1. Incoming Webhook URL (msteams-webhook-url). A channel-scoped URL pasted by the
   operator. Needs NO Entra app registration and no admin consent, so Teams delivery
   works on day one. Posts a MessageCard/Adaptive-Card payload.
2. Microsoft Graph (msteams-team-id + msteams-channel-id), via the shared
   config.connectors.msgraph helper.

   [ASSUMED] A5 — built + live-deferred. Posting a channel message with APPLICATION
   permissions (ChannelMessage.Send) is a Microsoft "protected API": it requires a
   separate request/approval process with Microsoft and returns 403 without it,
   regardless of tenant admin consent. This path is implemented and unverified. A 403
   here surfaces as a delivery failure, never as an exception into a caller.

SECURITY CONSTRAINTS (parity with Slack — REQ-M6-04, REQ-M6-15):
- The target must be explicit. No default channel, ever. A falsy target raises
  ValueError before any network call.
- Message content is validated before sending: over-length and known secret patterns
  raise ValueError. The guard is IMPORTED from slack.py rather than re-declared —
  _SECRET_PATTERNS is the platform's redaction policy and a second copy would drift.
- Credentials resolve per-tenant and are never stored on self or logged.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

import httpx

from config.connectors.base import BaseConnector
from config.connectors.models import (
    CapabilityEntry,
    CapabilityManifest,
    ConnectorAuditEvent,
    ConnectorHealth,
)
from config.connectors.msgraph import (
    GraphCredentialsMissing,
    get_graph_token,
    graph_request,
)
from config.connectors.rate_limit import _TenantRateLimitState, await_backoff

# Reuse, do not duplicate: one redaction policy for every notification channel.
from config.connectors.slack import MAX_MESSAGE_LENGTH, _validate_message

logger = logging.getLogger(__name__)

_HEALTH_PROBE_TENANT = "__health_probe__"


def _adaptive_card(message: str, title: str = "", link_url: str = "") -> Dict[str, Any]:
    """Build a MessageCard payload for the Incoming Webhook transport.

    MessageCard (not the newer Adaptive Card envelope) is what Teams Incoming Webhooks
    accept without an app manifest, which is the whole point of this transport.

    `link_url` becomes an OpenUri action. Approval flows deep-link into the platform's
    own authenticated UI rather than offering an inline approve button — a Teams
    callback carries no user identity the platform can trust, and accepting one would
    create a second, weaker approval path around the permission check in
    shared/routers/signals.py.
    """
    card: Dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": title or "Notification",
        "themeColor": "0F6CBD",
        "text": message,
    }
    if title:
        card["title"] = title
    if link_url:
        card["potentialAction"] = [
            {
                "@type": "OpenUri",
                "name": "Open in the platform",
                "targets": [{"os": "default", "uri": link_url}],
            }
        ]
    return card


class MSTeamsConnector(BaseConnector):
    """Write-only Microsoft Teams notification connector.

    Per-tenant rate-limit state is class-level to isolate tenants (REQ-M6-12).
    """

    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        """Constructor stores only non-secret config.

        Args:
            org_url:   Accepted and ignored — Teams has no org-URL concept. Present so
                       the connector factory can construct every kind uniformly.
            tenant_id: Run context, NOT a credential (REQ-M7-01).
        """
        self._org_url = ""
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "ms_teams"

    @property
    def display_name(self) -> str:
        return "Microsoft Teams"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve a Graph bearer token plus the configured delivery target.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        Never stored on self, never logged (REQ-M6-14).

        The token is resolved best-effort: a tenant using only an Incoming Webhook has
        no Entra app at all, and that is a supported configuration rather than an
        error, so a missing app registration yields token="" instead of raising.
        """
        tid = tenant_id or self._tenant_id
        if not tid:
            raise ValueError(
                "tenant_id is required for MSTeamsConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )
        target = await self._resolve_target(tid)
        token = ""
        try:
            token = await get_graph_token(tid)
        except (GraphCredentialsMissing, ValueError):
            token = ""  # webhook-only tenant — legitimate
        except Exception as exc:  # noqa: BLE001
            logger.debug("Teams Graph token unavailable: %s", type(exc).__name__)
            token = ""
        return {"token": token, **target}

    async def _resolve_target(self, tenant_id: str) -> dict[str, str]:
        """Read the tenant's configured Teams delivery target. Never raises."""
        from shared.services.notification_targets import teams_target

        return (await teams_target(tenant_id)) or {}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="ms_teams",
            read_capabilities={},  # write-only connector
            write_capabilities={
                "notify_teams": CapabilityEntry(
                    status="implemented",
                    description=(
                        "Post a message to an explicitly configured channel, via an "
                        "Incoming Webhook URL or Graph channel message. Target must be "
                        "passed by the caller — no default fallback (REQ-M6-04). Content "
                        "is validated: max 4 000 chars; no secret patterns (REQ-M6-15)."
                    ),
                ),
            },
            listen_capabilities={
                "driveItem_change": CapabilityEntry(
                    status="not_supported",
                    description="Teams inbound callbacks are not accepted; approvals deep-link instead.",
                ),
            },
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError(
            "MSTeamsConnector is a write-only notification adapter. Inbound Microsoft "
            "change notifications are handled by POST /webhooks/msgraph/{tenant_id}."
        )

    # ── Rate limiting (per-tenant) ────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe the configured Teams target.

        - Webhook URL configured        → healthy (there is no non-destructive probe
                                          for an Incoming Webhook; posting to test
                                          would spam the channel).
        - Graph token + team/channel    → GET /teams/{t}/channels/{c} → healthy.
        - Token mints, no target        → degraded (credential good, target missing).
        - Anything else                 → unhealthy.

        MUST NOT raise — a raising probe is dropped from the health cache, which makes
        GET /connectors/health re-probe inline on every request.
        """
        start = time.time()
        tid = self._tenant_id or _HEALTH_PROBE_TENANT
        try:
            auth = await self.auth_adapter(tid)

            if auth.get("webhook_url"):
                return ConnectorHealth(
                    connector_name="ms_teams",
                    status="healthy",
                    latency_ms=(time.time() - start) * 1000,
                )

            if not auth.get("token"):
                return ConnectorHealth(
                    connector_name="ms_teams",
                    status="unhealthy",
                    latency_ms=(time.time() - start) * 1000,
                    error="GraphCredentialsMissing",
                )

            team_id, channel_id = auth.get("team_id"), auth.get("channel_id")
            if not (team_id and channel_id):
                return ConnectorHealth(
                    connector_name="ms_teams",
                    status="degraded",
                    latency_ms=(time.time() - start) * 1000,
                    error="NoDeliveryTargetConfigured",
                )

            await graph_request(
                "GET",
                f"/teams/{team_id}/channels/{channel_id}",
                tenant_id=tid,
                connector_name="ms_teams",
            )
            return ConnectorHealth(
                connector_name="ms_teams",
                status="healthy",
                latency_ms=(time.time() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorHealth(
                connector_name="ms_teams",
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
        raise NotImplementedError(
            "MSTeamsConnector is write-only. read_adapter is not supported."
        )

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP: dict[str, Any] = {
            "notify_teams": self.notify_teams,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Primary write method ──────────────────────────────────────────────

    async def notify_teams(
        self,
        message: str,
        *,
        team_id: str = "",
        channel_id: str = "",
        webhook_url: str = "",
        title: str = "",
        link_url: str = "",
        tenant_id: str = "",
    ) -> None:
        """Post a message to an explicitly configured Teams channel.

        Args:
            message:     Plain-text body. Must not exceed MAX_MESSAGE_LENGTH chars and
                         must not contain secret patterns (REQ-M6-15).
            team_id:     Graph team id — required with channel_id when not using a webhook.
            channel_id:  Graph channel id.
            webhook_url: Teams Incoming Webhook URL. Preferred when present.
            title:       Optional card title.
            link_url:    Optional deep link rendered as an OpenUri action.
            tenant_id:   Overrides the instance tenant for credential resolution.

        Raises:
            ValueError: No explicit target, over-length message, or secret pattern.
        """
        # REQ-M6-04 parity: explicit target — no default channel.
        if not webhook_url and not (team_id and channel_id):
            raise ValueError(
                "A Teams target must be explicitly configured — pass webhook_url, or "
                "both team_id and channel_id. No default channel is allowed (REQ-M6-04)."
            )

        # REQ-M6-15: content guard — length + secret patterns.
        _validate_message(message)

        if webhook_url:
            # Incoming Webhook: the URL itself is the credential, so no Graph token.
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    webhook_url, json=_adaptive_card(message, title, link_url)
                )
                resp.raise_for_status()
            return

        # [ASSUMED] A5 — Graph app-only channel posting is a protected API. See module docstring.
        tid = tenant_id or self._tenant_id
        body_text = message
        if link_url:
            body_text = f"{message}<br/><a href=\"{link_url}\">Open in the platform</a>"
        if title:
            body_text = f"<strong>{title}</strong><br/>{body_text}"
        await graph_request(
            "POST",
            f"/teams/{team_id}/channels/{channel_id}/messages",
            tenant_id=tid,
            connector_name="ms_teams",
            json={"body": {"contentType": "html", "content": body_text}},
        )


# Re-exported so callers can reason about the shared limit without importing slack.
__all__ = ["MSTeamsConnector", "MAX_MESSAGE_LENGTH"]
