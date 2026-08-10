"""Azure Repos webhook event normalizer — REQ-M6-08.

Maps ADO PR service-hook payloads to CanonicalWorkItem via make_board_item().
ADO PR service hooks carry the event in resource.* with the top-level 'id' GUID
as the dedup event_id (A2 ASSUMED — verify against real payload before production).
"""
from __future__ import annotations

from config.connectors.models import make_board_item

# ADO PR status → canonical state mapping
_STATUS_MAP: dict[str, str] = {
    "active": "open",
    "completed": "closed",
    "abandoned": "abandoned",
    "draft": "draft",
}


def normalize_azure_repos_event(payload: dict, tenant_id: str = "", event_id: str = "") -> dict:
    """Map an ADO PR service-hook payload to CanonicalWorkItem.

    Fields mapped:
    - resource.pullRequestId → id / source_key
    - resource.title         → title
    - resource.status        → state (via _STATUS_MAP)
    - resource.repository.project.name → project
    - top-level 'id'         → event_id (A2 ASSUMED)
    """
    resource = payload.get("resource", {})
    repo = resource.get("repository", {})
    project_obj = repo.get("project", {})

    ado_status = resource.get("status", "")
    canonical_state = _STATUS_MAP.get(ado_status, ado_status)

    # A2 ASSUMED: top-level 'id' is the ADO service hook delivery GUID
    ado_event_id = event_id or payload.get("id", "")

    canonical = make_board_item(
        provider_kind="azure_repos",
        item_id=resource.get("pullRequestId"),
        source_key=str(resource.get("pullRequestId", "")),
        title=resource.get("title", ""),
        item_type="PullRequest",
        state=canonical_state,
        description="",
        assigned_to=(resource.get("createdBy") or {}).get("displayName", ""),
        tags=[],
        url="",
        project=project_obj.get("name", "") or repo.get("name", ""),
        raw=payload,
    )
    canonical.setdefault("status", canonical.get("state", ""))
    if tenant_id:
        canonical["tenant_id"] = tenant_id
    if ado_event_id:
        canonical["event_id"] = ado_event_id
    # Attach PR branch refs for downstream consumers
    canonical["source_ref"] = resource.get("sourceRefName", "")
    canonical["target_ref"] = resource.get("targetRefName", "")
    canonical["event_type"] = payload.get("eventType", "")
    return canonical
