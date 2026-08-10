from __future__ import annotations
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AcceptanceCriteria(BaseModel):
    id: str
    description: str
    gherkin: Optional[str] = None


class UserStory(BaseModel):
    id: str
    title: str
    description: str
    acceptance_criteria: List[AcceptanceCriteria] = Field(default_factory=list)
    priority: Optional[str] = None
    state: Optional[str] = None


class RequirementsPayload(BaseModel):
    """Typed contract for the requirements artifact produced by the Requirements Agent.

    Matches the output of build_requirements_payload() in planning.py.
    Backward-compatible: batch_id, gap_report, tech_stack, raw are all optional.
    """

    project: str
    team: Optional[str] = None
    batch_id: Optional[str] = None
    generated_at: Optional[str] = None
    scope_summary: Optional[str] = None
    story_count: Optional[int] = None
    # stories is List[Any] — ADO normalized dicts vary in shape; UserStory validation is advisory
    stories: List[Any] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    non_functional_requirements: Optional[Dict[str, Any]] = None
    # Structured work item index — populated by build_requirements_payload so downstream
    # agents can update states without regex-parsing the context string.
    # Each entry: {"id": 26, "type": "Issue", "title": "..."}
    work_items: List[Dict[str, Any]] = Field(default_factory=list)
    # Which PM provider was used for this session — propagated to downstream agents
    provider_kind: str = "azure_devops"
    # legacy / optional fields kept for backward compatibility
    gap_report: Optional[str] = None
    tech_stack: Optional[str] = None
    raw: Optional[str] = None
