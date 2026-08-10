"""Typed output artifact for the Code Review agent (`code_review_artifacts`).

Read-only review of a branch-vs-base diff OR an existing PR. The agent never
mutates the repo — it emits this structured artifact (findings, coverage,
conformance, metrics, summary, merge recommendation).
"""
from __future__ import annotations

from typing import List, Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator

Severity = Literal["critical", "high", "medium", "low", "info"]
FindingCategory = Literal[
    "logic_error", "security", "performance", "style", "design", "maintainability", "other"
]
MergeRecommendation = Literal["approve", "request_changes", "needs_discussion"]
CoverageStatus = Literal["satisfied", "violated", "unimplemented", "partial"]
ConformanceStatus = Literal["conforms", "drifts", "violates", "unknown"]


def _normalize_enum(value: object, valid: set, default: str) -> str:
    """Map an LLM-authored enum value onto the closed literal set instead of raising.

    A single mis-cased or invented severity/category/status must not fail an
    entire multi-finding review — the model coerces to a safe default and the
    finding still gets recorded (just under a conservative label).
    """
    if isinstance(value, str):
        candidate = value.strip().lower().replace("-", "_").replace(" ", "_")
        if candidate in valid:
            return candidate
    return default


class ReviewContext(BaseModel):
    repo_name: str = ""
    ado_project: str = ""
    mode: Literal["branch", "pr"] = "branch"
    source_branch: str = ""
    base_branch: str = ""
    pr_id: Optional[str] = None
    pr_title: Optional[str] = None
    head_sha: str = ""
    base_sha: str = ""


class ReviewFinding(BaseModel):
    id: str                          # F-001, F-002, …
    severity: Severity
    category: FindingCategory
    file: str = ""
    line: int = 0
    description: str
    recommendation: str = ""
    autofix_patch: Optional[str] = None   # copyable unified-diff snippet, never applied in v1

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, v):
        return _normalize_enum(v, set(get_args(Severity)), "medium")

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v):
        return _normalize_enum(v, set(get_args(FindingCategory)), "other")


class CoverageEntry(BaseModel):
    ac_id: str                       # acceptance-criterion id or short label
    status: CoverageStatus
    note: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        return _normalize_enum(v, set(get_args(CoverageStatus)), "partial")


class ConformanceEntry(BaseModel):
    rule: str                        # contract / schema / boundary checked
    status: ConformanceStatus
    note: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        return _normalize_enum(v, set(get_args(ConformanceStatus)), "unknown")


class ReviewMetrics(BaseModel):
    files_changed: int = 0
    added: int = 0
    removed: int = 0
    complexity_delta: Optional[float] = None
    dupe_delta: Optional[float] = None
    debt_delta: Optional[float] = None


class CodeReviewArtifact(BaseModel):
    context: ReviewContext = Field(default_factory=ReviewContext)
    summary: str = ""                # markdown review summary
    merge_recommendation: MergeRecommendation = "needs_discussion"
    findings: List[ReviewFinding] = Field(default_factory=list)
    requirements_coverage: List[CoverageEntry] = Field(default_factory=list)
    design_conformance: List[ConformanceEntry] = Field(default_factory=list)
    metrics: ReviewMetrics = Field(default_factory=ReviewMetrics)
    diff: str = ""                   # unified diff under review (capped)
    status: Literal["pending", "reviewed"] = "reviewed"

    @field_validator("merge_recommendation", mode="before")
    @classmethod
    def _coerce_merge_recommendation(cls, v):
        return _normalize_enum(v, set(get_args(MergeRecommendation)), "needs_discussion")
