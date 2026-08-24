"""Standalone Documentation agent artifact (session-held doc set).

Distinct from the pipeline-mode `DocumentationArtifact` planned for
shared/models/artifacts.py — this is the interactive standalone surface: the agent
generates documents on demand, each saved to the local docs folder (Azure Blob
later) and surfaced in the page's left-side list.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

DocType = Literal[
    "doc_set", "overview", "sdd", "api_reference", "code_summary",
    "changelog", "release_notes", "rtm", "run_summary", "compliance", "custom",
    "runbook_update", "knowledge_article",
]


class GeneratedDoc(BaseModel):
    id: str = ""
    type: DocType = "custom"
    title: str = ""
    filename: str = ""
    format: str = "md"
    path: str = ""              # local path on disk (or blob URL later)
    contents: str = ""          # markdown body (served to the viewer)
    bytes: int = 0
    diff: Optional[str] = None          # runbook_update: unified diff of the changed sections
    source_ref: Optional[str] = None    # where the prior version was read from (wiki page path / SharePoint item)
    issue_ref: Optional[str] = None     # knowledge_article: the fixed issue this article is about


class DocContext(BaseModel):
    repo_name: str = ""
    ado_project: str = ""
    mode: Literal["branch", "pr"] = "branch"
    source_branch: str = ""
    pr_id: Optional[str] = None
    head_sha: str = ""
    languages: List[str] = Field(default_factory=list)
    upstream_summary: str = ""


class DocumentationArtifact(BaseModel):
    context: DocContext = Field(default_factory=DocContext)
    documents: List[GeneratedDoc] = Field(default_factory=list)
    pr_url: Optional[str] = None
    status: Literal["idle", "generating", "ready", "pr_opened"] = "idle"
