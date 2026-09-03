"""Typed Pydantic artifact models for SDLC pipeline stages (M2-02, D-11).

ORM artifact models — map 1:1 to JSONB columns on the runs table:
    RequirementsArtifact  -> runs.requirements_payload
    DesignArtifact        -> runs.design_artifacts
    DevelopmentArtifact   -> runs.development_artifacts
    TestingArtifact       -> runs.testing_artifacts
    CodeReviewArtifact    -> runs.code_review_artifacts
    SecurityArtifact      -> runs.security_artifacts
model_dump() output is the only accepted input for those JSONB columns —
unvalidated dicts may not be written directly (T-M2-02-02 mitigation).

Utilities:
    TraceLink             — bidirectional traceability link between artifact items
    validate_handoff()    — consumer-side helper; strips sentinels + validates any BaseModel
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel


class RequirementsArtifact(BaseModel):
    agent_session_id: str
    brd_content: Optional[str] = None
    user_stories: Optional[List[dict]] = None
    acceptance_criteria: Optional[List[str]] = None
    risk_register: Optional[List[dict]] = None
    version: int = 1


class PlanArtifact(BaseModel):
    """The PM agent's output: what the work is, when it happens, and who does it.

    A BASELINE FROM THE FIRST SAVE. Answering "how far have we slipped" needs what was
    originally committed to, and a baseline added after plans already exist leaves every
    earlier plan with no history to compare against. It costs one field now and cannot
    be retrofitted later.

    Every field is optional because a plan is built up over a conversation — a work
    breakdown with no schedule yet is a legitimate intermediate state, not an invalid
    one.
    """

    agent_session_id: str = ""
    #: Work items with estimates, in the canonical board-item shape.
    tasks: Optional[List[dict]] = None
    #: Sprints or dated phases, each with its items.
    schedule: Optional[List[dict]] = None
    #: Who is on what, against real capacity where the board exposes it (ADO does,
    #: Jira does not — see the connector manifests).
    assignments: Optional[List[dict]] = None
    #: Blockers and cross-team dependencies, with what each one threatens.
    risks: Optional[List[dict]] = None
    milestones: Optional[List[dict]] = None
    #: The first committed version of `schedule`, kept unchanged for comparison.
    baseline: Optional[dict] = None
    #: Board and provider the plan was built against, so a later re-plan knows where
    #: the items live without asking again.
    board_project: Optional[str] = None
    provider_kind: Optional[str] = None
    version: int = 1


class DesignArtifact(BaseModel):
    hld: Optional[str] = None
    lld: Optional[str] = None
    api_contracts: Optional[str] = None
    database_schema: Optional[str] = None
    c4_diagram_url: Optional[str] = None
    adrs: Optional[List[dict]] = None
    security_checklist: Optional[str] = None
    version: int = 1


class DevelopmentArtifact(BaseModel):
    repo_url: Optional[str] = None
    branch_name: Optional[str] = None
    pr_url: Optional[str] = None
    code_summary: Optional[str] = None
    test_results: Optional[dict] = None
    version: int = 1


class TestingArtifact(BaseModel):
    test_plan: Optional[str] = None
    test_cases: Optional[List[dict]] = None
    coverage_report: Optional[dict] = None
    defect_log: Optional[List[dict]] = None
    version: int = 1


class CodeReviewArtifact(BaseModel):
    pr_ref: Optional[str] = None
    findings: List[dict] = []
    requirements_coverage: Optional[dict] = None
    design_conformance: Optional[dict] = None
    merge_recommendation: Optional[str] = None
    review_summary: Optional[str] = None
    semgrep_results: Optional[List[dict]] = None
    version: int = 1


class SecurityArtifact(BaseModel):
    scope: Optional[str] = None
    dependency_findings: List[dict] = []
    code_findings: List[dict] = []
    secret_findings: List[dict] = []
    risk_score: Optional[str] = None
    remediation_plan: List[dict] = []
    security_sign_off: bool = False
    scan_summary: Optional[str] = None
    version: int = 1


# ---------------------------------------------------------------------------
# Traceability and handoff helpers
# ---------------------------------------------------------------------------

_SENTINEL_PATTERN = re.compile(r"^(REQUIREMENTS_PAYLOAD|HANDOFF)::\s*", re.IGNORECASE)


class TraceLink(BaseModel):
    """Bidirectional traceability link between two artifact items."""

    from_id: str
    to_id: str
    kind: str


def validate_handoff(
    payload: Union[Dict[str, Any], str],
    model: type[BaseModel],
) -> BaseModel:
    """Parse and validate an agent handoff payload against a typed contract.

    Accepts:
      - A plain dict
      - A JSON string (with or without a leading ``REQUIREMENTS_PAYLOAD::`` /
        ``HANDOFF::`` sentinel and optional whitespace)

    Strips the sentinel prefix before parsing. On validation failure raises
    ``ValueError`` with the full Pydantic error detail so callers receive a
    precise diagnostic rather than a bare validation exception.
    """
    if isinstance(payload, str):
        payload = _SENTINEL_PATTERN.sub("", payload).strip()
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Handoff payload is not valid JSON: {exc}") from exc

    try:
        return model.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"Handoff payload failed validation against {model.__name__}: {exc}") from exc
