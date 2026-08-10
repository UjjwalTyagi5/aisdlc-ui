"""Generic work-item ingestion summary builder.

Works for any provider — ADO, Jira, etc. — because the normalized dict shape
is identical across providers (produced by each provider's fetch_item_detail).
"""
from typing import Any, Dict


def build_ingestion_summary(normalized: Dict[str, Any]) -> str:
    acceptance = normalized.get("acceptance_criteria") or []
    criteria_lines = "\n".join(f"- {item}" for item in acceptance) if acceptance else "- None provided"
    tags = ", ".join(normalized.get("tags") or []) or "None"

    item_id = normalized.get("work_item_id") or normalized.get("id") or "Unknown"
    source_raw = normalized.get("source_type", "")
    source = source_raw.replace("_", " ").title() if source_raw else "Work item"

    return (
        f"Imported {source} work item {item_id}: {normalized.get('title', '')}\n\n"
        f"Project: {normalized.get('project', '')}\n"
        f"Team: {normalized.get('team') or 'Not specified'}\n"
        f"Type: {normalized.get('work_item_type') or 'Unknown'}\n"
        f"State: {normalized.get('state') or 'Unknown'}\n"
        f"Tags: {tags}\n\n"
        f"Description:\n{normalized.get('description') or 'No description provided.'}\n\n"
        f"Acceptance Criteria:\n{criteria_lines}"
    )
