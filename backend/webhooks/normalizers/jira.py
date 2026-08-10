"""Jira webhook event normalizer — REQ-M6-08.

Maps Jira issue webhook payloads to CanonicalWorkItem via make_board_item().
The Jira payload nests fields under issue.fields (unlike ADO's flat resource.*).
Carries tenant_id and event_id into the canonical event for the consumer (Plan 07).
"""
from __future__ import annotations

from config.connectors.models import make_board_item


def normalize_jira_event(payload: dict, tenant_id: str = "", event_id: str = "") -> dict:
    """Map a Jira issue webhook payload to CanonicalWorkItem.

    Fields mapped (Jira nests under issue.fields):
    - issue.key        → source_key
    - issue.id         → id
    - fields.summary   → title
    - fields.status.name → state
    - fields.issuetype.name → item_type
    - fields.description → description
    - fields.assignee.displayName → assigned_to
    - fields.priority.name → tags (as priority tag)
    """
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or {}
    issue_type = (fields.get("issuetype") or {}).get("name", "")
    priority = (fields.get("priority") or {}).get("name", "")
    tags = [priority] if priority else []

    canonical = make_board_item(
        provider_kind="jira",
        item_id=issue.get("id", ""),
        source_key=issue.get("key", ""),
        title=fields.get("summary", ""),
        item_type=issue_type,
        state=(fields.get("status") or {}).get("name", ""),
        description=fields.get("description") or "",
        assigned_to=assignee.get("displayName", ""),
        tags=tags,
        url="",
        project=issue.get("key", "").split("-")[0] if issue.get("key") else "",
        raw=payload,
    )
    canonical.setdefault("status", canonical.get("state", ""))
    if tenant_id:
        canonical["tenant_id"] = tenant_id
    if event_id:
        canonical["event_id"] = event_id
    return canonical
