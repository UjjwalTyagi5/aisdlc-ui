"""Pydantic workflow input and HITL signal models for the Temporal pipeline (M5).

These models flow across the Temporal activity boundary and must remain
serialisation-safe (JSON-round-trippable). Use model_dump() when passing
to Temporal workflow input — the SDK serialises via its DataConverter.

Canonical import surface:
    from shared.models.workflow_models import SDLCWorkflowInput, HITLSignal, HITLApprovalRecord

Do NOT import from workflows.models — that module does not exist.
"""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChangeRequest(BaseModel):
    """Direct/standalone input channel for the Development Agent.

    Allows a user to issue a change instruction (e.g. "Add POST /orders")
    without an upstream Design artifact, enabling standalone/direct mode.
    All fields except `text` are optional so existing callers are unaffected.
    """

    text: str
    kind: Optional[Literal["feature", "bugfix", "refactor", "test", "chore"]] = None
    target_paths: Optional[list[str]] = None
    work_item_id: Optional[str] = None


class SDLCWorkflowInput(BaseModel):
    """Input payload for SDLCWorkflow — passed to client.start_workflow()."""

    run_id: str
    project_id: str
    tenant_id: str
    trigger: str = "manual"
    work_item_id: Optional[str] = None
    model_id: Optional[str] = None
    # The exact provider connection + model resolved at run creation; threaded to
    # every agent so dispatch is unambiguous (not first-match on model_id).
    offering_id: Optional[str] = None
    agent_version: int = 1
    # M10.1: optional resume context injected by the workflow on the
    # within-agent clarification re-invocation path (10.2). Additive only —
    # existing callers/tests are unaffected (REQ-M10-09).
    clarification_answer: Optional[str] = None
    # M10.1: multi-turn clarification loop-depth guard consumed by 10.2
    # (RESEARCH Open Q3).
    max_clarification_rounds: int = 3
    # Data-driven orchestration (Slice B): optional execution plan.
    # When present, SDLCWorkflow iterates this plan instead of the hardcoded phase sequence.
    # When absent, the workflow builds a default plan from AGENT_REGISTRY.
    execution_plan: Optional[dict] = None
    # MCP (Model Context Protocol): server ids to expose as tools for this run, keyed
    # by agent_id ({agent_id: [server_id, ...]}). Resolved at run creation from the
    # project policy (+ any per-run override) and threaded to every activity, which
    # loads the matching servers' tools and injects them before graph.ainvoke.
    # Additive + optional — absence means no MCP tools (existing callers unaffected).
    mcp_servers: Optional[dict[str, list[str]]] = None
    # Per-stage connector selection {agent_id: [connector_kind, ...]} resolved from the
    # project at run creation. When a stage has a selection the activity injects that
    # connector; absent → the tenant's azure_devops default (backward compatible).
    connectors: Optional[dict[str, list[str]]] = None
    # D1: direct/standalone mode input — user issues a change instruction without an
    # upstream Design artifact. Additive + optional; absence means pipeline mode
    # (Requirements→Design→Development as before).
    change_request: Optional[ChangeRequest] = None


class ClarificationRequest(BaseModel):
    """Returned by an agent activity when the agent ended its turn with questions.

    D-M10-04: mirrors the HITLSignal shape so the existing Temporal
    DataConverter + signals.py authz machinery reuse cleanly downstream
    (10.2/10.3). All fields are JSON-round-trippable (REQ-M10-02).
    """

    questions: list[str]
    # = run_id; the PostgresSaver resume key (pipeline run_id namespace —
    # NEVER a chat session_id, resolved A3 / RESEARCH Pitfall 7).
    thread_id: str
    agent_type: str  # "requirements" | "design" | "development" | "testing"
    phase: str
    clarification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ClarificationAnswer(BaseModel):
    """Payload carried by the within_agent_clarification signal (10.2)."""

    # Must match the pending ClarificationRequest.clarification_id —
    # the correlation guard (RESEARCH Pitfall 4).
    clarification_id: str
    # Bounded to cap Temporal history bloat from an oversized answer
    # (threat T-10.1-DOS).
    answer: str = Field(max_length=4096)
    actor_id: str
    idempotency_key: str = Field(default_factory=lambda: str(uuid.uuid4()))


class HITLSignal(BaseModel):
    """Payload carried by a HITL approval/rejection signal."""

    actor_id: str
    payload: dict = {}
    idempotency_key: str = ""


class HITLApprovalRecord(BaseModel):
    """Persisted record of a HITL gate decision (approved | rejected)."""

    run_id: str
    phase: str
    decision: str  # "approved" | "rejected"
    actor_id: str
    reason: Optional[str] = None
    recorded_at: str
