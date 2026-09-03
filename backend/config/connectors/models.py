"""Pydantic v2 schemas for the connector hub.

CapabilityManifest declares what a connector can do; ConnectorHealth reports a
point-in-time health probe; ConnectorAuditEvent is emitted for every connector
call for compliance attribution. error_type stores type(exc).__name__ only —
never str(exc) — to avoid leaking credentials into audit records (M1 decision).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def normalize_acceptance_criteria(value: Any) -> List[str]:
    """Return acceptance criteria as a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []

    lines = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        line = line.lstrip("-*").strip()
        if line:
            lines.append(line)
    return lines or [text]


def make_board_item(
    *,
    provider_kind: str,
    item_id: Any,
    title: str = "",
    item_type: str = "",
    state: str = "",
    source_key: Optional[str] = None,
    description: str = "",
    acceptance_criteria: Any = None,
    assigned_to: str = "",
    tags: Optional[List[str]] = None,
    url: str = "",
    project: str = "",
    team: str = "",
    parent_id: Any = None,
    children: Optional[List[Dict[str, Any]]] = None,
    # ── planning fields ──────────────────────────────────────────────────────
    # Everything a schedule is built from. They were absent, so the canonical item
    # described WHAT a piece of work is and nothing about how big it is, when it is
    # due, or which sprint it belongs to — the questions a project manager asks first.
    # Both providers already fetch the underlying values; ADO's `iteration_path` in
    # particular was being read off the row and then dropped here.
    estimate: Optional[float] = None,
    iteration: str = "",
    start_date: str = "",
    due_date: str = "",
    remaining_work: Optional[float] = None,
    completed_work: Optional[float] = None,
    priority: Any = None,
    raw: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a canonical board item dict with legacy compatibility aliases."""
    canonical_id = str(item_id) if item_id is not None else ""
    source_key = source_key or canonical_id
    criteria = normalize_acceptance_criteria(acceptance_criteria)
    tags = tags or []
    children = children or []

    item: Dict[str, Any] = {
        "provider_kind": provider_kind,
        "source_type": provider_kind,
        "id": canonical_id,
        "source_key": source_key,
        "key": source_key,
        "title": title or "",
        "type": item_type or "",
        "work_item_type": item_type or "",
        "state": state or "",
        "description": description or "",
        "acceptance_criteria": criteria,
        "assigned_to": assigned_to or "",
        "tags": tags,
        "url": url or "",
        "parent_id": parent_id,
        "children": children,
        "project": project or "",
        "team": team or "",
        # None, not 0. An unestimated item and a zero-point item are different facts,
        # and averaging the second into a velocity is how a plan quietly lies.
        "estimate": estimate,
        "iteration": iteration or "",
        "start_date": start_date or "",
        "due_date": due_date or "",
        "remaining_work": remaining_work,
        "completed_work": completed_work,
        "priority": priority,
        "raw": raw or {},
    }

    # Compatibility keys used by the existing requirements agent and ingestion DB.
    item["work_item_id"] = item_id
    item["work_item_url"] = url or ""

    for key, value in extra.items():
        if value not in (None, ""):
            item[key] = value
    return item


class CapabilityEntry(BaseModel):
    status: Literal["implemented", "stub", "not_supported"]
    description: str = ""


class CapabilityManifest(BaseModel):
    connector_name: str
    version: str = "1.0"
    read_capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)
    write_capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)
    listen_capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)


class ConnectorHealth(BaseModel):
    connector_name: str
    status: Literal["healthy", "degraded", "unhealthy"]
    latency_ms: Optional[float] = None
    last_checked: float = Field(default_factory=time.time)
    error: Optional[str] = None


class ConnectorAuditEvent(BaseModel):
    connector_name: str
    method: str
    tenant_id: str
    run_id: Optional[str] = None
    latency_ms: float
    status: Literal["success", "error"]
    # type(exc).__name__ only — never str(exc) (credential leakage risk, M1 decision)
    error_type: Optional[str] = None
    retry_count: int = 0  # rate-limit retry attempts before success (REQ-M6-12)
    timestamp: float = Field(default_factory=time.time)
