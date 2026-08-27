"""Resolve where a tenant's notifications and documents actually go.

A connector answers "can we reach Teams / Slack / SharePoint / Confluence / Azure
DevOps Wiki at all". This module answers the separate question "and which channel,
library, space, or wiki", which is routing configuration rather than a credential.

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


async def ado_wiki_target(tenant_id: str) -> Optional[Dict[str, str]]:
    """Which Azure DevOps wiki this tenant's runbooks/knowledge articles live in.

    Returns {"project", "wiki_id", "runbook_path", "kb_path"} or None. `wiki_id` is the
    operative value — every wiki-pages call addresses a wiki by id — so a configuration
    without one is treated as unconfigured, same contract as sharepoint_target above.
    """
    wiki_id = await _get(tenant_id, "ado-wiki-id")
    if not wiki_id:
        return None
    return {
        "project": await _get(tenant_id, "ado-wiki-project"),
        "wiki_id": wiki_id,
        "runbook_path": await _get(tenant_id, "ado-wiki-runbook-path") or "/Runbooks",
        "kb_path": await _get(tenant_id, "ado-wiki-kb-path") or "/Knowledge Base",
    }


async def figma_target(tenant_id: str) -> Optional[Dict[str, str]]:
    """Which Figma file this tenant's design work defaults to.

    Unlike the resolvers above, this one is a CONVENIENCE, not a gate: the Design
    agent's tools accept an explicit file URL, and a tenant with no default file can
    still use them by pasting one. So None here means "no default configured", NOT
    "Figma is unavailable" — connectedness is the `figma-connected` marker's job, and
    conflating the two would hide a working integration behind an unset preference.
    """
    file_key = await _get(tenant_id, "figma-file-key")
    return {"file_key": file_key} if file_key else None


async def confluence_target(tenant_id: str) -> Optional[Dict[str, str]]:
    """Which Confluence space this tenant files generated documentation into.

    Same shape as `figma_target`: a CONVENIENCE, not a gate. The Documentation agent's
    Confluence tools accept an explicit space key/id on every call, so a tenant with no
    default configured can still use them by naming a space each time. None here means
    "no default configured", NOT "Confluence is unavailable" — connectedness is the
    `confluence-api-token` credential's job (checked by the connector itself).
    """
    space = await _get(tenant_id, "confluence-space-key")
    return {"space": space} if space else None
