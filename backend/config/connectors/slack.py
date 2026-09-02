"""Slack connector — write-only notification adapter (REQ-M6-04, REQ-M6-15).

SlackConnector sends messages to an explicitly configured Slack channel via the
Slack Bot Token + AsyncWebClient.chat_postMessage.  It is a write-only connector:
read_adapter raises NotImplementedError; write_adapter exposes only notify_slack.

SECURITY CONSTRAINTS:
- channel must be explicitly passed — no default fallback (REQ-M6-04, T-m6-05-EoP).
  Empty or None channel raises ValueError before any API call.
- message content is validated before sending (REQ-M6-15, T-m6-05-ID):
  * Over-length messages (> MAX_MESSAGE_LENGTH) raise ValueError.
  * Messages matching known secret patterns (API keys, tokens, passwords) raise ValueError.
- Credentials (bot token) resolve from the tenant secret store, then Key Vault
  "{tenant}-slack-bot-token". Both rungs are tenant-scoped: no global or env
  rung exists, so an unconnected tenant fails as "not connected".

BOUNDARY RULE:
slack_sdk is imported ONLY inside this connector file (and webhooks/verifiers/slack.py
in Plan 06).  It must never be imported in agents_orchestrator/ tool files (CLAUDE.md).
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Dict

import shared.keyvault as _keyvault
from config.connectors.base import BaseConnector, ConnectorNotAvailableError
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

logger = logging.getLogger(__name__)

# Slack message size limit (Slack API allows up to 40 000 chars in blocks,
# but plain-text messages should stay under 4 000 for readability and to
# prevent accidental artifact dumps — REQ-M6-15).
MAX_MESSAGE_LENGTH: int = 4000

# Secret-pattern guard — raise ValueError before sending if the message matches
# any of these patterns (T-m6-05-ID, Pitfall 8, REQ-M6-15).
# Patterns are case-insensitive to catch common serialization variants.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Environment-variable style: ANY_VAR_NAME matching secret suffixes (API_KEY, TOKEN, etc.)
    # Catches: API_KEY=..., SOME_API_KEY=..., ANTHROPIC_API_KEY=..., etc.
    re.compile(r"[A-Z0-9_]*(?:API_KEY|API_TOKEN|SECRET|_PAT|_PASSWORD)\s*=\s*\S+", re.IGNORECASE),
    # Bearer tokens (any "Bearer sk-..." or "Bearer xox..." Slack/Anthropic/OpenAI style)
    re.compile(r"Bearer\s+\S{8,}", re.IGNORECASE),
    # Explicit keyword: anthropic_api_key or ANTHROPIC_API_KEY anywhere in text
    re.compile(r"ANTHROPIC_API_KEY", re.IGNORECASE),
    # Generic password=value pattern
    re.compile(r"\bpassword\s*=\s*\S+", re.IGNORECASE),
]


def _validate_message(message: str) -> None:
    """Raise ValueError if message violates content guard policies (REQ-M6-15).

    Checks:
    1. Length <= MAX_MESSAGE_LENGTH.
    2. No known secret / PII patterns.
    """
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValueError(
            f"Slack message exceeds maximum length ({len(message)} > {MAX_MESSAGE_LENGTH}). "
            "Do not pass raw artifact content as a Slack message."
        )
    for pattern in _SECRET_PATTERNS:
        if pattern.search(message):
            raise ValueError(
                "Slack message contains a potential secret or credential pattern. "
                "Redact sensitive content before calling notify_slack() (REQ-M6-15)."
            )


class SlackConnector(BaseConnector):
    """Write-only Slack notification connector backed by slack_sdk AsyncWebClient.

    Bot token is loaded ephemerally via auth_adapter() — tenant-scoped rungs only.
    The bot token is resolved per-tenant inside auth_adapter() and never stored on
    the instance. There is no constructor token, no global vault name and no env var:
    a tenant that has not connected Slack has no token and fails as "not connected".

    Per-tenant rate-limit state is class-level to isolate tenants (REQ-M6-12).
    """

    _tenant_states: Dict[str, _TenantRateLimitState] = {}

    def __init__(self, org_url: str = "", tenant_id: str = "") -> None:
        """Constructor stores only non-secret run context — never a credential.

        Args:
            org_url:   Accepted and ignored — Slack has no org-URL concept.  Present so
                       the connector factory can construct every kind uniformly
                       (connector_factory.get_connector_for_session passes org_url= to
                       all connectors; without it this class raised TypeError).
            tenant_id: Run context, NOT a credential.  The factory sets it so
                       auth_adapter()/health_check() can resolve the tenant-scoped bot
                       token without every call site threading it through (REQ-M7-01).

        There was a `bot_token` kwarg here, documented as a local-dev hint that Key
        Vault would override. Nothing in the application ever passed it — only tests
        did — while it kept a credential alive on the instance for the lifetime of the
        connector, which is the one thing REQ-M6-14 forbids. It is gone rather than
        gated, for the same reason the env-var and global-vault rungs are.
        """
        # Slack has no org URL concept; workspace is determined by the bot token.
        self._org_url = ""
        self._tenant_id = tenant_id

    # ── Identity ──────────────────────────────────────────────────────────

    @property
    def connector_name(self) -> str:
        return "slack"

    @property
    def display_name(self) -> str:
        return "Slack"

    # ── Auth (ephemeral) ──────────────────────────────────────────────────

    async def auth_adapter(self, tenant_id: str = "") -> dict[str, Any]:
        """Resolve the bot token ephemerally, tenant-scoped at every rung.

        tenant_id is required — raises ValueError when absent (REQ-M7-01, SC-02).
        An explicit argument wins; otherwise the instance tenant_id set by the factory
        is used, so health_check() and the convenience methods resolve per-tenant
        without threading it through every call site (mirrors AzureDevOpsConnector).

        Two rungs, both tenant-scoped: the project-scoped credential override, then
        this tenant's `slack-bot-token` secret. No global-vault name, no env var and
        no constructor hint — a tenant with no token of its own gets None here and
        fails as "not connected" rather than borrowing the platform's workspace.
        Return value is never stored on self and must not be logged/persisted (REQ-M6-14).
        """
        tenant_id = tenant_id or self._tenant_id
        if not tenant_id:
            raise ValueError(
                "tenant_id is required for SlackConnector.auth_adapter() — "
                "connector credentials are per-tenant (REQ-M7-01)."
            )
        # A project's own bot token wins over the tenant-wide one.
        #
        # WORTH KNOWING WHAT THIS TOKEN IS. Unlike Jira's or GitHub's, a Slack
        # bot token identifies an APP in a workspace, not a person — two members
        # of a project would paste the same value rather than their own. It is
        # stored per-member anyway because that is where this platform keeps
        # secrets (project_integration_config holds URLs, never credentials), and
        # a shared value stored twice is harmless. What it is NOT is per-person
        # attribution: Slack will show the app as the author whoever configured it.
        override = await self._resolve_credential_override(tenant_id, "slack")
        if override and override.token:
            return {"bot_token": override.token}

        bot_token = await _keyvault.load_secret("slack-bot-token", tenant_id=tenant_id)
        return {"bot_token": bot_token}

    # ── Capability declaration ────────────────────────────────────────────

    def capability_manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            connector_name="slack",
            read_capabilities={},  # write-only connector
            write_capabilities={
                "notify_slack": CapabilityEntry(
                    status="implemented",
                    description=(
                        "Send a plain-text message to an explicitly configured channel. "
                        "Channel must be passed by caller — no default fallback (REQ-M6-04). "
                        "Message content is validated: max 4 000 chars; no secret patterns (REQ-M6-15)."
                    ),
                ),
            },
            listen_capabilities={},
        )

    # ── Webhooks ──────────────────────────────────────────────────────────

    async def webhook_receiver(self, payload: dict[str, Any]) -> None:
        """Slack connector is write-only; inbound webhooks are not supported."""
        raise NotImplementedError(
            "SlackConnector is a write-only notification adapter. "
            "Inbound Slack event handling is deferred to Plan 06 (webhooks/verifiers/slack.py)."
        )

    # ── Rate limiting (per-tenant) ────────────────────────────────────────

    async def rate_limit_manager(self, tenant_id: str) -> None:
        """Wait out any active backoff window for this tenant (REQ-M6-12).

        Slack tier limits apply per workspace; tenant isolation prevents one
        workspace's rate-limit from blocking others.
        """
        retry_ref = [0]
        await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)

    # ── Health ────────────────────────────────────────────────────────────

    async def health_check(self) -> ConnectorHealth:
        """Probe Slack API via auth_test().

        Uses type(exc).__name__ only — never str(exc) — to avoid leaking
        bot tokens into health records (M1 decision).
        """
        # Import slack_sdk here — boundary rule: SDK stays inside this file.
        from slack_sdk.web.async_client import AsyncWebClient

        start = time.time()
        try:
            auth = await self.auth_adapter()
            client = AsyncWebClient(token=auth["bot_token"])
            await client.auth_test()
            latency_ms = (time.time() - start) * 1000
            return ConnectorHealth(
                connector_name="slack",
                status="healthy",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.time() - start) * 1000
            return ConnectorHealth(
                connector_name="slack",
                status="unhealthy",
                latency_ms=latency_ms,
                error=type(exc).__name__,
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
        """Slack is a write-only connector — read operations are not supported."""
        raise NotImplementedError(
            "SlackConnector is write-only. read_adapter is not supported."
        )

    async def write_adapter(self, operation: str, **kwargs: Any) -> Any:
        _MAP: dict[str, Any] = {
            "notify_slack": self.notify_slack,
        }
        fn = _MAP.get(operation)
        if fn is None:
            raise ValueError(f"Unknown write operation: {operation!r}")
        return await fn(**kwargs)

    # ── Primary write method ──────────────────────────────────────────────

    async def notify_slack(self, channel: str, message: str) -> None:
        """Send a plain-text message to an explicitly configured Slack channel.

        Args:
            channel: Slack channel ID or name (e.g. "#general" or "C01234ABC").
                     MUST be explicitly passed by the caller — no default fallback
                     channel is allowed (REQ-M6-04, T-m6-05-EoP).
            message: Plain-text message body.  Must not exceed MAX_MESSAGE_LENGTH
                     chars and must not contain secret patterns (REQ-M6-15).

        Raises:
            ValueError: If channel is falsy (empty/None), message is over-length,
                        or message matches a secret pattern.
        """
        # Import slack_sdk inside method — boundary rule: SDK stays inside this file.
        from slack_sdk.web.async_client import AsyncWebClient

        # REQ-M6-04: explicit channel — no default fallback.
        if not channel:
            raise ValueError(
                "Slack channel must be explicitly configured per project — "
                "no default fallback channel is allowed (REQ-M6-04)."
            )

        # REQ-M6-15: content guard — length + secret patterns.
        _validate_message(message)

        auth = await self.auth_adapter()
        client = AsyncWebClient(token=auth["bot_token"])
        await client.chat_postMessage(channel=channel, text=message)
