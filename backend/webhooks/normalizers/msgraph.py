"""Normalize a single Microsoft Graph change notification.

Graph delivers a BATCH — {"value": [notification, ...]} — so this normalizer takes ONE
notification, not the HTTP payload. The dedicated /webhooks/msgraph route splits the
batch and calls this per element; the generic one-payload-one-event registry contract
cannot express a batch.

IDENTIFIERS ONLY, NO CONTENT. A Graph driveItem notification tells you that something
changed and gives you its ids — it never carries the file. Anything that needs the
document must fetch it back through
SharePointConnector.read_adapter("download_document"). That is also the security
posture: `clientState` is the only authentication Graph offers, so these events must be
treated as untrusted hints and re-verified by fetching through an authenticated client.
"""
from __future__ import annotations

from typing import Any, Dict


def normalize_msgraph_event(
    notification: Dict[str, Any], tenant_id: str = "", event_id: str = ""
) -> Dict[str, Any]:
    """Map one Graph change notification to a flat change event.

    Raises:
        ValueError: the object is not a Graph change notification.
    """
    if not isinstance(notification, dict) or not notification.get("subscriptionId"):
        raise ValueError("not a Microsoft Graph change notification")

    resource_data = notification.get("resourceData") or {}
    resource = notification.get("resource", "") or ""

    # resource looks like "drives/{drive-id}/root" or "drives/{drive-id}/items/{item-id}".
    drive_id = ""
    parts = resource.split("/")
    if len(parts) >= 2 and parts[0] == "drives":
        drive_id = parts[1]

    return {
        "provider": "sharepoint",
        "tenant_id": tenant_id,
        "event_id": event_id,
        "subscription_id": notification.get("subscriptionId", ""),
        "change_type": notification.get("changeType", ""),
        "resource": resource,
        "drive_id": drive_id,
        "item_id": resource_data.get("id", "") if isinstance(resource_data, dict) else "",
        # The Entra directory the change happened in — NOT the platform tenant.
        "entra_tenant_id": notification.get("tenantId", ""),
        "subscription_expiration": notification.get("subscriptionExpirationDateTime", ""),
    }
