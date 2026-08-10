"""Progression state machine for the SDLC pipeline.

Pure functions — no IO, no LLM. Derives stage order and gate metadata
from AGENT_REGISTRY. Agents at the same pipeline_position are sorted
alphabetically to produce a deterministic STAGE_ORDER.
"""
from __future__ import annotations

from config.agent_registry import AGENT_REGISTRY

STAGE_ORDER: list[str] = [
    aid
    for aid, _ in sorted(
        AGENT_REGISTRY.items(),
        key=lambda kv: (kv[1].pipeline_position, kv[0]),
    )
]


def next_stage(active: str) -> str | None:
    """Return the stage that follows *active*, or None if *active* is last."""
    try:
        i = STAGE_ORDER.index(active)
    except ValueError:
        return None
    return STAGE_ORDER[i + 1] if i + 1 < len(STAGE_ORDER) else None


def gate_type_for(stage: str) -> str:
    """Return the gate_type for *stage* from the registry."""
    defn = AGENT_REGISTRY.get(stage)
    return getattr(defn, "gate_type", "approval_required") if defn else "approval_required"


def stage_status_from_run(run_status: str, gate_pending: bool, active: str) -> str:
    """Map a run-level status + gate flag to a stage-level display status.

    Returns one of: interviewing | running | awaiting_gate | approved | rejected | complete
    """
    if run_status == "complete":
        return "complete"
    if "approval" in run_status and run_status.startswith("awaiting_"):
        return "awaiting_gate"
    if "clarification" in run_status and run_status.startswith("awaiting_"):
        return "interviewing"
    if run_status == "rejected":
        return "rejected"
    if run_status == "approved":
        return "approved"
    if gate_pending:
        return "awaiting_gate"
    return "running"
