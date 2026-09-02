import html
import re
from typing import Any, Dict, List, Optional

import httpx


HTML_TAG_RE = re.compile(r"<[^>]+>")
LIST_ITEM_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
BREAK_RE = re.compile(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", re.IGNORECASE)


def strip_html(value: Optional[str]) -> str:
    if not value:
        return ""
    text = BREAK_RE.sub("\n", value)
    text = HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_acceptance_criteria(value: Optional[str]) -> List[str]:
    if not value:
        return []

    items = LIST_ITEM_RE.findall(value)
    if items:
        cleaned = [strip_html(item) for item in items]
        return [item for item in cleaned if item]

    text = strip_html(value)
    criteria = []
    for line in text.splitlines():
        cleaned = line.strip(" -*\t")
        if cleaned:
            criteria.append(cleaned)
    return criteria


def build_work_item_url(org_url: str, project: str, work_item_id: int) -> str:
    org_url = org_url.rstrip("/")
    return f"{org_url}/{project}/_workitems/edit/{work_item_id}"


async def fetch_work_item(
    *,
    org_url: str,
    project: str,
    work_item_id: int,
    pat: str,
) -> Dict[str, Any]:
    org_url = org_url.rstrip("/")
    api_url = f"{org_url}/{project}/_apis/wit/workitems/{work_item_id}?$expand=relations&api-version=7.1"

    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        response = await client.get(api_url)
        response.raise_for_status()
        return response.json()


async def list_projects(*, org_url: str, pat: str) -> List[Dict[str, Any]]:
    org_url = org_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.get(f"{org_url}/_apis/projects?api-version=7.1&$top=200")
        r.raise_for_status()
        return [
            {"id": p["id"], "name": p["name"], "description": p.get("description", "")}
            for p in r.json().get("value", [])
        ]


async def list_teams(*, org_url: str, project: str, pat: str) -> List[Dict[str, Any]]:
    org_url = org_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.get(f"{org_url}/_apis/projects/{project}/teams?api-version=7.1&$top=200")
        r.raise_for_status()
        return [
            {"id": t["id"], "name": t["name"], "description": t.get("description", "")}
            for t in r.json().get("value", [])
        ]


async def list_states(
    *, org_url: str, project: str, work_item_type: str, pat: str
) -> List[Dict[str, Any]]:
    org_url = org_url.rstrip("/")
    encoded_type = work_item_type.replace(" ", "%20")
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.get(
            f"{org_url}/{project}/_apis/wit/workitemtypes/{encoded_type}/states?api-version=7.1"
        )
        r.raise_for_status()
        return [
            {"name": s["name"], "category": s.get("category", "")}
            for s in r.json().get("value", [])
        ]


async def list_item_types(
    *, org_url: str, project: str, pat: str
) -> List[Dict[str, Any]]:
    """The work item types this PROJECT actually has.

    NOT a fixed list, and that is the whole point. Azure DevOps types come from the
    project's process template and differ per project: Agile has "User Story", Scrum
    has "Product Backlog Item", Basic has neither — it has "Issue". An agent that
    assumes one gets VS402323 ("Work item type X does not exist in project Y"), which
    is what a real run hit on a Basic project.
    """
    org_url = org_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.get(
            f"{org_url}/{project}/_apis/wit/workitemtypes?api-version=7.1"
        )
        r.raise_for_status()
        return [
            {"name": t.get("name", ""), "description": t.get("description", "")}
            for t in r.json().get("value", [])
            if t.get("name")
        ]


async def list_wikis(*, org_url: str, project: str, pat: str) -> List[Dict[str, Any]]:
    org_url = org_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.get(f"{org_url}/{project}/_apis/wiki/wikis?api-version=7.1")
        r.raise_for_status()
        return [
            {"id": w["id"], "name": w.get("name", ""), "type": w.get("type", "")}
            for w in r.json().get("value", [])
        ]


async def get_wiki_page(
    *, org_url: str, project: str, wiki_id: str, path: str, pat: str
) -> Dict[str, Any]:
    """Fetch one wiki page's content. `path` is the wiki page path, e.g. "/Runbooks/Payments"."""
    org_url = org_url.rstrip("/")
    encoded_path = httpx.QueryParams({"path": path or "/"})
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.get(
            f"{org_url}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
            f"?{encoded_path}&includeContent=true&api-version=7.1"
        )
        r.raise_for_status()
        data = r.json()
        return {
            "path": data.get("path", path),
            "content": data.get("content", ""),
            "version": str(data.get("id", "")),
            "url": data.get("remoteUrl") or data.get("url", ""),
        }


async def list_wiki_pages(
    *, org_url: str, project: str, wiki_id: str, path_prefix: str, pat: str
) -> List[Dict[str, str]]:
    """List page paths under `path_prefix` (recursively), without content bodies."""
    org_url = org_url.rstrip("/")
    encoded_path = httpx.QueryParams({"path": path_prefix or "/"})
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.get(
            f"{org_url}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
            f"?{encoded_path}&recursionLevel=Full&api-version=7.1"
        )
        r.raise_for_status()
        data = r.json()

    def _walk(node: Dict[str, Any], out: List[Dict[str, str]]) -> None:
        if node.get("path"):
            out.append({"path": node["path"], "url": node.get("remoteUrl") or node.get("url", "")})
        for child in node.get("subPages", []) or []:
            _walk(child, out)

    pages: List[Dict[str, str]] = []
    _walk(data, pages)
    return pages


async def create_work_item(
    *,
    org_url: str,
    project: str,
    work_item_type: str = "User Story",
    title: str,
    description: str = "",
    acceptance_criteria: str = "",
    parent_id: str = "",
    pat: str,
) -> Dict[str, Any]:
    """Create a new work item in ADO. Returns the created work item.

    `parent_id` links the new item UNDER an existing one. Without it items are created
    unparented — which is what happened when an agent reported creating Tasks "linked
    under Epic #1" and the board showed three orphans, because the only trace of the
    parent was a sentence somebody had typed into a description.

    The link goes in THIS request, not a follow-up PATCH, so it is atomic: either the
    item exists parented or it does not exist. A second call could leave an orphan
    behind on failure, which is the state we are trying to stop producing.
    """
    org_url = org_url.rstrip("/")
    api_url = f"{org_url}/{project}/_apis/wit/workitems/${work_item_type}?api-version=7.1"
    ops = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        # Set AreaPath to project root so items appear on the default team board
        {"op": "add", "path": "/fields/System.AreaPath", "value": project},
    ]
    if description:
        ops.append({"op": "add", "path": "/fields/System.Description", "value": description})
    if acceptance_criteria:
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "value": acceptance_criteria})
    if parent_id:
        # Hierarchy-REVERSE is the child->parent direction. Hierarchy-Forward would
        # declare the new item the PARENT of the id given, which is the same call with
        # the tree upside down and no error to tell you.
        #
        # The URL is org-level (no project segment) — that is what the API returns in
        # `relations` and what it expects here.
        ops.append({
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": f"{org_url}/_apis/wit/workItems/{parent_id}",
            },
        })
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.post(api_url, json=ops, headers={"Content-Type": "application/json-patch+json"})
        r.raise_for_status()
        return r.json()


async def update_work_item_fields(
    *,
    org_url: str,
    project: str,
    work_item_id: int,
    acceptance_criteria: str = "",
    description: str = "",
    title: str = "",
    pat: str,
) -> Dict[str, Any]:
    """Patch title / acceptance criteria / description onto an existing work item."""
    org_url = org_url.rstrip("/")
    api_url = f"{org_url}/{project}/_apis/wit/workitems/{work_item_id}?api-version=7.1"
    ops = []
    if title:
        ops.append({"op": "add", "path": "/fields/System.Title", "value": title})
    if acceptance_criteria:
        ops.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "value": acceptance_criteria})
    if description:
        ops.append({"op": "add", "path": "/fields/System.Description", "value": description})
    if not ops:
        return {}
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.patch(api_url, json=ops, headers={"Content-Type": "application/json-patch+json"})
        r.raise_for_status()
        return r.json()


async def create_ado_project(
    *,
    org_url: str,
    name: str,
    description: str = "",
    process: str = "Agile",
    pat: str,
) -> Dict[str, Any]:
    """Create an Azure DevOps project (Git + the named process). Async on the ADO side:
    returns the operation reference — the project provisions in the background."""
    org_url = org_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        procs = await client.get(f"{org_url}/_apis/process/processes?api-version=7.1")
        procs.raise_for_status()
        values = procs.json().get("value", [])
        process_id = None
        for p in values:
            if (p.get("name") or "").lower() == process.lower():
                process_id = p.get("id")
                break
        if not process_id and values:
            process_id = values[0].get("id")
        body = {
            "name": name,
            "description": description,
            "capabilities": {
                "versioncontrol": {"sourceControlType": "Git"},
                "processTemplate": {"templateTypeId": process_id},
            },
        }
        r = await client.post(f"{org_url}/_apis/projects?api-version=7.1", json=body)
        r.raise_for_status()
        data = r.json() if r.content else {}
        return {"name": name, "queued": True, "operation": data}


async def delete_work_item(
    *,
    org_url: str,
    project: str,
    work_item_id: int,
    pat: str,
) -> Dict[str, Any]:
    """DELETE a work item (moves it to the project's Recycle Bin). Returns the API response."""
    org_url = org_url.rstrip("/")
    api_url = f"{org_url}/{project}/_apis/wit/workitems/{work_item_id}?api-version=7.1"
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.delete(api_url)
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return {"id": work_item_id, "deleted": True}
        return r.json()


_INPROGRESS_HINTS = {
    "in development", "in progress", "active", "doing", "in dev",
    "development", "wip", "working", "started",
}
_CATEGORY_HINTS: Dict[str, set] = {
    "InProgress": _INPROGRESS_HINTS,
    "Resolved": {"in review", "review", "resolved", "testing", "qa", "pending"},
    "Completed": {"done", "closed", "complete", "completed", "finished",
                  "in production", "deployed", "released", "shipped"},
    "Proposed": {"to do", "todo", "new", "proposed", "open", "backlog"},
    "Removed": {"removed", "cancelled", "canceled", "deleted"},
}


async def _resolve_state(
    *,
    org_url: str,
    project: str,
    work_item_type: str,
    requested_state: str,
    client: httpx.AsyncClient,
) -> str:
    """Return the correct state name for `work_item_type` that best matches `requested_state`.

    Tries exact match first, then falls back to category-aware resolution so that
    e.g. "In Development" resolves to "Doing" for work item types whose in-progress
    state has a different name.
    """
    encoded_type = work_item_type.replace(" ", "%20")
    r = await client.get(
        f"{org_url}/{project}/_apis/wit/workitemtypes/{encoded_type}/states?api-version=7.1"
    )
    if r.status_code != 200:
        return requested_state

    valid_states = r.json().get("value", [])
    state_names = [s["name"] for s in valid_states]

    # Exact match (case-insensitive)
    for name in state_names:
        if name.lower() == requested_state.lower():
            return name

    # Category-aware fallback
    requested_lower = requested_state.lower()
    target_category: Optional[str] = None
    for category, hints in _CATEGORY_HINTS.items():
        if requested_lower in hints or any(h in requested_lower for h in hints):
            target_category = category
            break

    if target_category:
        for s in valid_states:
            # ADO returns either "stateCategory" or "category" depending on API version
            cat = s.get("stateCategory") or s.get("category") or ""
            if cat == target_category:
                return s["name"]

    return requested_state


async def update_work_item_state(
    *,
    org_url: str,
    project: str,
    work_item_id: int,
    new_state: str,
    pat: str,
) -> Dict[str, Any]:
    """Move a work item to a new state. Returns the updated work item.

    No-op (returns current item unchanged) if the item is already in `new_state`.
    Automatically resolves `new_state` to the correct name for the work item's
    actual type (e.g. "In Development" → "Doing" for Issue-type items).
    """
    org_url = org_url.rstrip("/")
    api_url = f"{org_url}/{project}/_apis/wit/workitems/{work_item_id}?api-version=7.1"

    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        current = await client.get(api_url)
        current.raise_for_status()
        fields = current.json().get("fields") or {}
        current_state = fields.get("System.State", "")
        if current_state == new_state:
            return current.json()

        # Resolve against valid states for this work item's actual type
        item_type = fields.get("System.WorkItemType", "")
        resolved_state = new_state
        if item_type:
            resolved_state = await _resolve_state(
                org_url=org_url,
                project=project,
                work_item_type=item_type,
                requested_state=new_state,
                client=client,
            )

        ops = [{"op": "add", "path": "/fields/System.State", "value": resolved_state}]
        r = await client.patch(
            api_url,
            json=ops,
            headers={"Content-Type": "application/json-patch+json"},
        )
        r.raise_for_status()
        return r.json()


async def list_stories_by_state(
    *,
    org_url: str,
    project: str,
    state: str,
    pat: str,
    team: Optional[str] = None,
    work_item_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return summary rows for all work items matching the state filter.

    Each row: {id, title, state, work_item_type, assigned_to, tags}.
    """
    org_url = org_url.rstrip("/")
    types = work_item_types or ["User Story", "Product Backlog Item"]
    types_clause = ", ".join(f"'{t}'" for t in types)

    safe_state = state.replace("'", "''")
    wiql = {
        "query": (
            "SELECT [System.Id] FROM workitems "
            "WHERE [System.TeamProject] = @project "
            f"AND [System.State] = '{safe_state}' "
            f"AND [System.WorkItemType] IN ({types_clause}) "
            "ORDER BY [System.ChangedDate] DESC"
        )
    }

    base = f"{org_url}/{project}"
    if team:
        base = f"{org_url}/{project}/{team}"

    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.post(f"{base}/_apis/wit/wiql?api-version=7.1", json=wiql)
        r.raise_for_status()
        ids = [w["id"] for w in r.json().get("workItems", [])]
        if not ids:
            return []

        # ADO caps at 200 ids per batch fetch
        rows: List[Dict[str, Any]] = []
        for i in range(0, len(ids), 200):
            chunk = ids[i : i + 200]
            ids_str = ",".join(map(str, chunk))
            fields = "System.Id,System.Title,System.State,System.WorkItemType,System.AssignedTo,System.Tags"
            r2 = await client.get(
                f"{org_url}/{project}/_apis/wit/workitems"
                f"?ids={ids_str}&fields={fields}&api-version=7.1"
            )
            r2.raise_for_status()
            for w in r2.json().get("value", []):
                f = w["fields"]
                assigned = f.get("System.AssignedTo")
                if isinstance(assigned, dict):
                    assigned_name = assigned.get("displayName", "")
                else:
                    assigned_name = str(assigned or "")
                rows.append({
                    "id": f["System.Id"],
                    "title": f.get("System.Title", ""),
                    "state": f.get("System.State", ""),
                    "work_item_type": f.get("System.WorkItemType", ""),
                    "assigned_to": assigned_name,
                    "tags": [t.strip() for t in (f.get("System.Tags") or "").split(";") if t.strip()],
                })
        return rows


async def list_all_work_items(
    *,
    org_url: str,
    project: str,
    pat: str,
    team: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return ALL work items in a project regardless of state or type.

    Uses a WIQL query with no state or type filter so nothing is missed.
    Each row: {id, title, state, work_item_type, assigned_to, tags}.
    """
    org_url = org_url.rstrip("/")
    wiql = {
        "query": (
            "SELECT [System.Id] FROM workitems "
            "WHERE [System.TeamProject] = @project "
            "ORDER BY [System.ChangedDate] DESC"
        )
    }
    base = f"{org_url}/{project}"
    if team:
        base = f"{org_url}/{project}/{team}"

    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.post(f"{base}/_apis/wit/wiql?api-version=7.1", json=wiql)
        r.raise_for_status()
        ids = [w["id"] for w in r.json().get("workItems", [])]
        if not ids:
            return []

        rows: List[Dict[str, Any]] = []
        for i in range(0, len(ids), 200):
            chunk = ids[i : i + 200]
            ids_str = ",".join(map(str, chunk))
            fields = "System.Id,System.Title,System.State,System.WorkItemType,System.AssignedTo,System.Tags"
            r2 = await client.get(
                f"{org_url}/{project}/_apis/wit/workitems"
                f"?ids={ids_str}&fields={fields}&api-version=7.1"
            )
            r2.raise_for_status()
            for w in r2.json().get("value", []):
                f = w["fields"]
                assigned = f.get("System.AssignedTo")
                assigned_name = assigned.get("displayName", "") if isinstance(assigned, dict) else str(assigned or "")
                rows.append({
                    "id": f["System.Id"],
                    "title": f.get("System.Title", ""),
                    "state": f.get("System.State", ""),
                    "work_item_type": f.get("System.WorkItemType", ""),
                    "assigned_to": assigned_name,
                    "tags": [t.strip() for t in (f.get("System.Tags") or "").split(";") if t.strip()],
                })
        return rows


def normalize_work_item(
    *,
    work_item: Dict[str, Any],
    org_url: str,
    project: str,
    team: str = "",
    source_type: str = "azure_boards",
) -> Dict[str, Any]:
    fields = work_item.get("fields", {})
    work_item_id = work_item.get("id")
    title = fields.get("System.Title", "")
    description_html = fields.get("System.Description", "")
    acceptance_html = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
    relations = work_item.get("relations", []) or []

    tags = fields.get("System.Tags", "")
    tag_list = [tag.strip() for tag in tags.split(";") if tag.strip()]

    normalized = {
        "source_type": source_type,
        "organization_url": org_url.rstrip("/"),
        "project": project,
        "team": team,
        "work_item_id": work_item_id,
        "work_item_type": fields.get("System.WorkItemType", ""),
        "title": title,
        "state": fields.get("System.State", ""),
        "area_path": fields.get("System.AreaPath", ""),
        "iteration_path": fields.get("System.IterationPath", ""),
        "assigned_to": (
            fields.get("System.AssignedTo", {}) or {}
        ).get("displayName", "")
        if isinstance(fields.get("System.AssignedTo"), dict)
        else str(fields.get("System.AssignedTo", "") or ""),
        "created_by": (
            fields.get("System.CreatedBy", {}) or {}
        ).get("displayName", "")
        if isinstance(fields.get("System.CreatedBy"), dict)
        else str(fields.get("System.CreatedBy", "") or ""),
        "description": strip_html(description_html),
        "acceptance_criteria": extract_acceptance_criteria(acceptance_html),
        "tags": tag_list,
        "relations": [
            {
                "rel": relation.get("rel", ""),
                "url": relation.get("url", ""),
                "name": (relation.get("attributes") or {}).get("name", ""),
            }
            for relation in relations
        ],
        "work_item_url": build_work_item_url(org_url, project, work_item_id),
    }

    return normalized


async def fetch_work_items_batch(
    *,
    org_url: str,
    project: str,
    work_item_ids: List[int],
    pat: str,
    extra_fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Fetch full field data for a batch of work item IDs (max 200 per call)."""
    if not work_item_ids:
        return []
    org_url = org_url.rstrip("/")
    default_fields = [
        "System.Id", "System.Title", "System.State", "System.WorkItemType",
        "System.AssignedTo", "System.Description", "System.Tags",
        "Microsoft.VSTS.Common.AcceptanceCriteria",
        "System.AreaPath", "System.IterationPath",
        "System.Parent",
    ]
    fields = ",".join(extra_fields or default_fields)
    results: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        for i in range(0, len(work_item_ids), 200):
            chunk = work_item_ids[i : i + 200]
            ids_str = ",".join(map(str, chunk))
            r = await client.get(
                f"{org_url}/{project}/_apis/wit/workitems"
                f"?ids={ids_str}&fields={fields}&$expand=relations&api-version=7.1"
            )
            r.raise_for_status()
            results.extend(r.json().get("value", []))
    return results


async def list_work_items_by_type(
    *,
    org_url: str,
    project: str,
    work_item_type: str,
    pat: str,
    state: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return summary rows for all work items of a given type (e.g. 'Epic', 'Feature')."""
    org_url = org_url.rstrip("/")
    safe_type = work_item_type.replace("'", "''")
    state_clause = f"AND [System.State] = '{state.replace(chr(39), chr(39)*2)}'" if state else ""
    wiql = {
        "query": (
            "SELECT [System.Id] FROM workitems "
            "WHERE [System.TeamProject] = @project "
            f"AND [System.WorkItemType] = '{safe_type}' "
            f"{state_clause} "
            "ORDER BY [System.ChangedDate] DESC"
        )
    }
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.post(
            f"{org_url}/{project}/_apis/wit/wiql?api-version=7.1", json=wiql
        )
        r.raise_for_status()
        ids = [w["id"] for w in r.json().get("workItems", [])]
    if not ids:
        return []
    items = await fetch_work_items_batch(
        org_url=org_url, project=project, work_item_ids=ids, pat=pat
    )
    rows = []
    for w in items:
        f = w.get("fields", {})
        assigned = f.get("System.AssignedTo")
        rows.append({
            "id": f.get("System.Id"),
            "title": f.get("System.Title", ""),
            "state": f.get("System.State", ""),
            "work_item_type": f.get("System.WorkItemType", ""),
            "assigned_to": assigned.get("displayName", "") if isinstance(assigned, dict) else str(assigned or ""),
        })
    return rows


async def fetch_hierarchy_tree(
    *,
    org_url: str,
    project: str,
    pat: str,
    root_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return a hierarchy tree: Epics with nested Features with nested User Stories.

    Each node: {id, title, type, state, children: [...]}.
    Falls back to a flat list if no parent links are present.
    """
    org_url = org_url.rstrip("/")
    types = root_types or ["Epic", "Feature", "User Story", "Product Backlog Item"]
    types_clause = ", ".join(f"'{t}'" for t in types)
    wiql = {
        "query": (
            "SELECT [System.Id] FROM workitems "
            "WHERE [System.TeamProject] = @project "
            f"AND [System.WorkItemType] IN ({types_clause}) "
            "ORDER BY [System.WorkItemType] ASC, [System.ChangedDate] DESC"
        )
    }
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.post(
            f"{org_url}/{project}/_apis/wit/wiql?api-version=7.1", json=wiql
        )
        r.raise_for_status()
        all_ids = [w["id"] for w in r.json().get("workItems", [])]

    if not all_ids:
        return []

    raw = await fetch_work_items_batch(
        org_url=org_url, project=project, work_item_ids=all_ids, pat=pat
    )

    # Build lookup
    by_id: Dict[int, Dict] = {}
    for item in raw:
        f = item.get("fields", {})
        wid = f.get("System.Id")
        ac = extract_acceptance_criteria(f.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""))
        by_id[wid] = {
            "id": wid,
            "title": f.get("System.Title", ""),
            "type": f.get("System.WorkItemType", ""),
            "state": f.get("System.State", ""),
            "description": strip_html(f.get("System.Description", "")),
            "acceptance_criteria": ac,
            "parent_id": f.get("System.Parent"),
            "children": [],
        }

    # Wire parent→child links
    roots = []
    for node in by_id.values():
        parent_id = node.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    return roots


async def add_comment_to_work_item(
    *,
    org_url: str,
    project: str,
    work_item_id: int,
    comment: str,
    pat: str,
) -> bool:
    """Add a comment to a work item. Returns True on success."""
    org_url = org_url.rstrip("/")
    api_url = (
        f"{org_url}/{project}/_apis/wit/workitems/{work_item_id}/comments?api-version=7.1-preview.3"
    )
    async with httpx.AsyncClient(timeout=30.0, auth=("", pat)) as client:
        r = await client.post(api_url, json={"text": comment})
        r.raise_for_status()
        return True


def build_ingestion_summary(normalized: Dict[str, Any]) -> str:
    acceptance = normalized.get("acceptance_criteria") or []
    criteria_lines = "\n".join(f"- {item}" for item in acceptance) if acceptance else "- None provided"
    tags = ", ".join(normalized.get("tags") or []) or "None"

    return (
        f"Imported Azure Boards work item {normalized['work_item_id']}: {normalized['title']}\n\n"
        f"Project: {normalized['project']}\n"
        f"Team: {normalized.get('team') or 'Not specified'}\n"
        f"Type: {normalized.get('work_item_type') or 'Unknown'}\n"
        f"State: {normalized.get('state') or 'Unknown'}\n"
        f"Tags: {tags}\n\n"
        f"Description:\n{normalized.get('description') or 'No description provided.'}\n\n"
        f"Acceptance Criteria:\n{criteria_lines}"
    )
