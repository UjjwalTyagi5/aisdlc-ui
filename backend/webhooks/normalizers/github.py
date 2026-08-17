"""GitHub webhook event normalizer — REQ-M6-08.

Maps GitHub issue webhook payloads to CanonicalWorkItem via make_board_item().
Carries tenant_id and event_id into the canonical event so the consumer
(Plan 07) can build a deterministic run id.
"""
from __future__ import annotations

from config.connectors.models import make_board_item


def normalize_github_event(payload: dict, tenant_id: str = "", event_id: str = "") -> dict:
    """Map a GitHub issue webhook payload to CanonicalWorkItem.

    Fields mapped:
    - issue.number → id / source_key
    - issue.title  → title
    - issue.state  → state
    - issue.body   → description
    - issue.labels → tags
    - issue.assignee.login → assigned_to
    - issue.html_url → url
    - repository.full_name → project
    """
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    canonical = make_board_item(
        provider_kind="github_issues",
        item_id=issue.get("number"),
        source_key=str(issue.get("number", "")),
        title=issue.get("title", ""),
        item_type="Issue",
        state=issue.get("state", ""),
        description=issue.get("body") or "",
        assigned_to=(issue.get("assignee") or {}).get("login", ""),
        tags=[lbl["name"] for lbl in issue.get("labels", []) if isinstance(lbl, dict)],
        url=issue.get("html_url", ""),
        project=repo.get("full_name", ""),
        raw=payload,
    )
    # Carry dedup / routing context into the canonical event
    # Add status alias for test and consumer compatibility (make_board_item uses 'state')
    canonical.setdefault("status", canonical.get("state", ""))
    if tenant_id:
        canonical["tenant_id"] = tenant_id
    if event_id:
        canonical["event_id"] = event_id
    return canonical
