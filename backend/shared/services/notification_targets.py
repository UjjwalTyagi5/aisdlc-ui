"""Resolve where a tenant's notifications and documents actually go.

A connector answers "can we reach Teams / Slack / SharePoint at all". This module
answers the separate question "and which channel or library", which is routing
configuration rather than a credential.

WHY THESE LIVE IN THE SECRET STORE, not a new column:

- `gha-owner` is the existing precedent — a non-secret routing value stored beside its
  credential, written by the same POST /connectors/{kind}/credentials call, listed in
  `_KIND_SECRET_STORE_REFS`, and cleaned up on disconnect for free.
- The two dispatch seams that need a channel have NO project id. Both
  `notify_gate_pending(run_id, stage, owner_role, tenant_id)` and
  `emit_escalation_activity` carry only a tenant, so a per-project store would force a
  DB lookup inside a best-effort seam that must never raise.
- `ProjectOut.connectors` is typed `dict[str, list[str]]`, so a nested config dict
  cannot ride along there without breaking response validation.

A per-project override is a deliberate follow-on, not an oversight: it would be an
additive `projects.connector_config` JSONB read before the tenant tier here.

CONTRACT: every resolver returns None when nothing is configured and NEVER raises.
None means "this tenant has not opted in" — callers skip that channel silently. This
is what preserves the explicit-target rule (REQ-M6-04): the connectors still refuse a
falsy channel, and these resolvers decide whether to call them at all.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Default library folder for published documentation when none is configured.
DEFAULT_SHAREPOINT_FOLDER = "SDLC Documentation"


async def _get(tenant_id: str, ref: str) -> str:
    """Read one config ref. Returns "" for absent, disconnected, or unreadable."""
    if not tenant_id:
        return ""
    try:
        from shared.services import secret_store

        value = await secret_store.get_secret(tenant_id, ref)
        if not value or value == secret_store.DISCONNECTED_MARKER:
            return ""
        return value
    except Exception as exc:  # noqa: BLE001 — routing lookup must never raise
        logger.debug("notification_targets: %s unreadable: %s", ref, type(exc).__name__)
        return ""


async def teams_target(tenant_id: str) -> Optional[Dict[str, str]]:
    """Where this tenant wants Teams notifications delivered.

    Returns {"webhook_url": ...} or {"team_id": ..., "channel_id": ...}, or None when
    Teams is not configured. The webhook transport wins when both are present — it
    needs no Entra app registration, so it is the one most likely to actually work.
    """
    webhook_url = await _get(tenant_id, "msteams-webhook-url")
    if webhook_url:
        return {"webhook_url": webhook_url}

    team_id = await _get(tenant_id, "msteams-team-id")
    channel_id = await _get(tenant_id, "msteams-channel-id")
    if team_id and channel_id:
        return {"team_id": team_id, "channel_id": channel_id}
    return None


async def slack_target(tenant_id: str) -> Optional[Dict[str, str]]:
    """Which Slack channel this tenant wants notifications in.

    Until this ref exists, Slack delivery does not fire at all — `notify_slack` has had
    no channel source anywhere in the codebase, which is why it had zero production
    call sites. There is deliberately no default channel (REQ-M6-04): a tenant with a
    stored bot token starts receiving messages only once someone sets this.
    """
    channel = await _get(tenant_id, "slack-channel")
    return {"channel": channel} if channel else None


async def sharepoint_target(tenant_id: str) -> Optional[Dict[str, str]]:
    """Which SharePoint library this tenant files generated documents into.

    Returns {"site_id", "drive_id", "folder"} or None. `drive_id` is the operative
    value — every read/write call addresses a drive — so a configuration without one
    is treated as unconfigured rather than half-usable.
    """
    drive_id = await _get(tenant_id, "sharepoint-drive-id")
    if not drive_id:
        return None
    return {
        "site_id": await _get(tenant_id, "sharepoint-site-id"),
        "drive_id": drive_id,
        "folder": await _get(tenant_id, "sharepoint-folder-path") or DEFAULT_SHAREPOINT_FOLDER,
    }
