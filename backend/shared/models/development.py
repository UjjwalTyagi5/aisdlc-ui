from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    command: str
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    allowed: bool = True
    sanitized: bool = True


class ValidationResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped", "error"]
    summary: str = ""
    command: Optional[str] = None
    output: str = ""
    duration_ms: int = 0


class DevelopmentArtifacts(BaseModel):
    repo_url: Optional[str] = None
    repo_type: Optional[Literal["ado", "github", "local"]] = None
    branch_name: Optional[str] = None
    target_branch: Optional[str] = None
    changed_files: List[str] = Field(default_factory=list)
    generated_files: List[str] = Field(default_factory=list)
    commands_run: List[CommandResult] = Field(default_factory=list)
    lint_results: List[ValidationResult] = Field(default_factory=list)
    test_results: List[ValidationResult] = Field(default_factory=list)
    build_results: List[ValidationResult] = Field(default_factory=list)
    pr_url: Optional[str] = None
    pr_title: Optional[str] = None
    work_item_ids: List[str] = Field(default_factory=list)
    status: Literal["not_started", "in_progress", "validated", "pr_created", "failed"] = "not_started"
