"""Jira Cloud REST API v3 helper functions.

All functions are async and use httpx.BasicAuth(email, api_token).
The 'pat' stored in the connector DB is 'email|||api_token' — split before calling.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


def _auth(email: str, api_token: str) -> httpx.BasicAuth:
    return httpx.BasicAuth(email, api_token)


def _base(org_url: str) -> str:
    return f"{org_url.rstrip('/')}/rest/api/3"


def _extract_adf_text(node: Any) -> str:
    """Recursively extract plain text from Atlassian Document Format."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        text = " ".join(_extract_adf_text(c) for c in node.get("content", []))
        if node.get("type") in ("paragraph", "heading", "bulletList", "orderedList", "listItem", "blockquote", "codeBlock"):
            text = text.strip() + "\n"
        return text
    if isinstance(node, list):
        return " ".join(_extract_adf_text(c) for c in node)
    return ""


def _text_to_adf(text: str) -> Dict:
    """Wrap plain text in minimal ADF for writing to Jira fields."""
    paragraphs = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            paragraphs.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": line}]
            })
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]
    return {"version": 1, "type": "doc", "content": paragraphs}


def _normalize_issue_summary(issue: Dict[str, Any]) -> Dict[str, Any]:
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or {}
    return {
        "id": int(issue.get("id", 0)),
        "key": issue.get("key", ""),
        "title": fields.get("summary", ""),
        "state": (fields.get("status") or {}).get("name", ""),
        "work_item_type": (fields.get("issuetype") or {}).get("name", ""),
        "assigned_to": assignee.get("displayName", ""),
        "tags": list(fields.get("labels") or []),
    }


def _normalize_issue_detail(issue: Dict[str, Any], org_url: str) -> Dict[str, Any]:
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or {}

    description_adf = fields.get("description")
    description_text = _extract_adf_text(description_adf).strip() if description_adf else ""

    # Acceptance criteria: check common Jira custom fields
    ac_text = ""
    for cf_key in ("customfield_10016", "customfield_10056", "customfield_10028", "customfield_70016"):
        cf_val = fields.get(cf_key)
        if cf_val:
            ac_text = _extract_adf_text(cf_val).strip() if isinstance(cf_val, dict) else str(cf_val).strip()
            break

    return {
        "id": int(issue.get("id", 0)),
        "key": issue.get("key", ""),
        "title": fields.get("summary", ""),
        "state": (fields.get("status") or {}).get("name", ""),
        "work_item_type": (fields.get("issuetype") or {}).get("name", ""),
        "assigned_to": assignee.get("displayName", ""),
        "description": description_text,
        "acceptance_criteria": ac_text,
        "tags": list(fields.get("labels") or []),
        "url": f"{org_url}/browse/{issue.get('key', '')}",
        "priority": (fields.get("priority") or {}).get("name", ""),
        "source_type": "jira",
    }


def _transition_candidates(target_state: str) -> List[str]:
    """Return Jira transition names to try for a canonical lifecycle intent."""
    target = (target_state or "").strip()
    normalized = target.lower().replace("_", " ").replace("-", " ")
    aliases = {
        "to do": ["To Do"],
        "todo": ["To Do"],
        "new": ["To Do", "Open", "Backlog"],
        "open": ["To Do", "Open", "Backlog"],
        "in development": ["In Development", "In Progress"],
        "development": ["In Development", "In Progress"],
        "active": ["In Progress", "In Development"],
        "in progress": ["In Progress"],
        "progress": ["In Progress"],
        "in review": ["In Review", "Review", "Code Review", "In Progress"],
        "review": ["In Review", "Review", "Code Review", "In Progress"],
        "done": ["Done", "Deployed", "Complete", "Completed", "Closed"],
        "deployed": ["Done", "Deployed", "Complete", "Completed", "Closed"],
        "complete": ["Done", "Complete", "Completed", "Closed"],
        "completed": ["Done", "Complete", "Completed", "Closed"],
        "closed": ["Done", "Closed", "Complete", "Completed"],
    }
    candidates = aliases.get(normalized, [target])
    if target and target not in candidates:
        candidates = [target, *candidates]
    deduped: List[str] = []
    for candidate in candidates:
        if candidate and candidate.lower() not in {c.lower() for c in deduped}:
            deduped.append(candidate)
    return deduped


# ── API functions ─────────────────────────────────────────────────────────────

async def list_projects(org_url: str, email: str, api_token: str) -> List[Dict[str, Any]]:
    url = f"{_base(org_url)}/project/search"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.get(url, params={"maxResults": 100, "expand": "description"})
        resp.raise_for_status()
        data = resp.json()
    return [
        {
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "key": p.get("key", ""),
            "description": p.get("description", "") or "",
        }
        for p in data.get("values", [])
    ]


async def list_teams(org_url: str, email: str, api_token: str, project_key: str) -> List[Dict[str, Any]]:
    """Jira project roles serve as team proxies."""
    url = f"{_base(org_url)}/project/{project_key}/role"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        roles = resp.json()
    return [{"id": k, "name": k} for k in roles.keys()]


async def list_states(
    org_url: str, email: str, api_token: str, project_key: str, issue_type: str = "Story"
) -> List[Dict[str, Any]]:
    url = f"{_base(org_url)}/project/{project_key}/statuses"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        statuses_by_type = resp.json()

    for entry in statuses_by_type:
        if entry.get("name", "").lower() == issue_type.lower():
            return [
                {
                    "name": s.get("name", ""),
                    "category": (s.get("statusCategory") or {}).get("name", ""),
                    "id": s.get("id", ""),
                }
                for s in entry.get("statuses", [])
            ]

    if statuses_by_type:
        return [
            {
                "name": s.get("name", ""),
                "category": (s.get("statusCategory") or {}).get("name", ""),
                "id": s.get("id", ""),
            }
            for s in statuses_by_type[0].get("statuses", [])
        ]
    return []


async def list_stories_by_state(
    org_url: str,
    email: str,
    api_token: str,
    project_key: str,
    state: str,
    team: Optional[str] = None,
    issue_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    types = issue_types or ["Story", "Epic", "Task", "Bug"]
    type_str = ", ".join(f'"{t}"' for t in types)
    jql = f'project = "{project_key}" AND issuetype IN ({type_str}) AND status = "{state}"'
    if team:
        jql += f' AND team = "{team}"'

    url = f"{_base(org_url)}/search/jql"
    payload = {
        "jql": jql,
        "maxResults": 200,
        "fields": ["summary", "status", "issuetype", "assignee", "labels", "priority"],
    }
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=20.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return [_normalize_issue_summary(i) for i in data.get("issues", [])]


async def list_all_work_items(
    org_url: str,
    email: str,
    api_token: str,
    project_key: str,
    team: Optional[str] = None,
) -> List[Dict[str, Any]]:
    jql = f'project = "{project_key}" ORDER BY created DESC'
    if team:
        jql = f'project = "{project_key}" AND team = "{team}" ORDER BY created DESC'

    url = f"{_base(org_url)}/search/jql"
    payload = {
        "jql": jql,
        "maxResults": 500,
        "fields": ["summary", "status", "issuetype", "assignee", "labels", "priority"],
    }
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return [_normalize_issue_summary(i) for i in data.get("issues", [])]


async def fetch_work_item(org_url: str, email: str, api_token: str, issue_id: int) -> Dict[str, Any]:
    url = f"{_base(org_url)}/issue/{issue_id}"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.get(url, params={"expand": "renderedFields,names,schema"})
        resp.raise_for_status()
        issue = resp.json()
    return _normalize_issue_detail(issue, org_url)


async def fetch_hierarchy_tree(
    org_url: str, email: str, api_token: str, project_key: str
) -> List[Dict[str, Any]]:
    """Fetch epics and their child stories as a hierarchy tree."""
    search_url = f"{_base(org_url)}/search/jql"

    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=30.0) as client:
        # Fetch epics
        epic_resp = await client.post(search_url, json={
            "jql": f'project = "{project_key}" AND issuetype = Epic ORDER BY created ASC',
            "maxResults": 100,
            "fields": ["summary", "status", "issuetype", "assignee"],
        })
        epic_resp.raise_for_status()

        tree = []
        for epic in epic_resp.json().get("issues", []):
            epic_key = epic["key"]
            epic_node = {**_normalize_issue_summary(epic), "children": []}

            child_resp = await client.post(search_url, json={
                "jql": f'"Epic Link" = "{epic_key}" OR parent = "{epic_key}"',
                "maxResults": 200,
                "fields": ["summary", "status", "issuetype", "assignee"],
            })
            if child_resp.status_code == 200:
                epic_node["children"] = [
                    _normalize_issue_summary(c) for c in child_resp.json().get("issues", [])
                ]
            tree.append(epic_node)

        # Orphan stories (no epic parent)
        orphan_resp = await client.post(search_url, json={
            "jql": (
                f'project = "{project_key}" AND issuetype in (Story, Task, Bug) '
                f'AND "Epic Link" is EMPTY AND parent is EMPTY ORDER BY created ASC'
            ),
            "maxResults": 200,
            "fields": ["summary", "status", "issuetype", "assignee"],
        })
        if orphan_resp.status_code == 200:
            for orphan in orphan_resp.json().get("issues", []):
                tree.append({**_normalize_issue_summary(orphan), "children": []})

    return tree


async def create_work_item(
    org_url: str,
    email: str,
    api_token: str,
    project_key: str,
    issue_type: str,
    summary: str,
    description: str = "",
    acceptance_criteria: str = "",
) -> Dict[str, Any]:
    body = description
    if acceptance_criteria:
        body = body.rstrip() + f"\n\nAcceptance Criteria:\n{acceptance_criteria}"

    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if body.strip():
        fields["description"] = _text_to_adf(body.strip())

    url = f"{_base(org_url)}/issue"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.post(url, json={"fields": fields})
        resp.raise_for_status()
        data = resp.json()

    return {"id": int(data["id"]), "key": data["key"], "url": f"{org_url}/browse/{data['key']}"}


async def update_work_item_fields(
    org_url: str,
    email: str,
    api_token: str,
    issue_id: int,
    description: str = "",
    acceptance_criteria: str = "",
) -> Dict[str, Any]:
    body = description
    if acceptance_criteria:
        body = body.rstrip() + f"\n\nAcceptance Criteria:\n{acceptance_criteria}"

    fields: Dict[str, Any] = {}
    if body.strip():
        fields["description"] = _text_to_adf(body.strip())
    if not fields:
        return {"updated": False, "msg": "No fields to update"}

    url = f"{_base(org_url)}/issue/{issue_id}"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.put(url, json={"fields": fields})
        resp.raise_for_status()
    return {"updated": True}


async def update_work_item_state(
    org_url: str, email: str, api_token: str, issue_id: int, new_state: str
) -> Dict[str, Any]:
    url = f"{_base(org_url)}/issue/{issue_id}/transitions"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        transitions = resp.json().get("transitions", [])

        candidates = _transition_candidates(new_state)
        transition = next(
            (
                t for target in candidates for t in transitions
                if t.get("name", "").lower() == target.lower()
            ),
            None,
        )
        transition_id = transition.get("id") if transition else None
        if not transition_id:
            available = [t.get("name") for t in transitions]
            return {
                "moved": False,
                "msg": (
                    f"State '{new_state}' not found. Tried {candidates}. "
                    f"Available: {available}"
                ),
            }

        move_resp = await client.post(url, json={"transition": {"id": transition_id}})
        move_resp.raise_for_status()

    return {"moved": True, "new_state": transition.get("name", new_state)}


async def add_comment_to_work_item(
    org_url: str, email: str, api_token: str, issue_id: int, comment: str
) -> bool:
    url = f"{_base(org_url)}/issue/{issue_id}/comment"
    async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=15.0) as client:
        resp = await client.post(url, json={"body": _text_to_adf(comment)})
    return resp.status_code in (200, 201)


async def test_connection(org_url: str, email: str, api_token: str) -> str:
    """Check connectivity; returns 'healthy', 'degraded', or 'disconnected'."""
    try:
        url = f"{_base(org_url)}/myself"
        async with httpx.AsyncClient(auth=_auth(email, api_token), timeout=10.0) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return "healthy"
        return "degraded"
    except Exception:
        return "disconnected"
