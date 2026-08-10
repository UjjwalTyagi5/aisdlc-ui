"""Execution plan builder — derives a phase DAG from agent registry + policy.

Public API:
    build_execution_plan(run_id, project_id, mode, active_agents, skip_agents)
        -> ExecutionPlan

The ExecutionPlan is JSON-serializable (Pydantic v2) and can be passed
through Temporal's DataConverter as part of SDLCWorkflowInput.
"""
from __future__ import annotations

from collections import defaultdict
from typing import List, Optional

from pydantic import BaseModel

from config.agent_registry import AGENT_REGISTRY


class ExecutionPhase(BaseModel):
    phase_number: int
    agent_ids: List[str]
    parallel: bool = False
    gate_type: str = "approval_required"
    sla_hours: int = 24
    max_rejections: int = 1


class ExecutionPlan(BaseModel):
    run_id: str
    project_id: str
    mode: str  # "pipeline" | "standalone" | "triggered"
    phases: List[ExecutionPhase]


def build_execution_plan(
    *,
    run_id: str,
    project_id: str,
    mode: str = "pipeline",
    active_agents: Optional[List[str]] = None,
    skip_agents: Optional[List[str]] = None,
) -> ExecutionPlan:
    """Build an ExecutionPlan from the agent registry, filtered by policy."""
    skip_set = set(skip_agents or [])

    if active_agents is not None:
        agent_ids = [aid for aid in active_agents if aid in AGENT_REGISTRY and aid not in skip_set]
    else:
        agent_ids = [aid for aid in AGENT_REGISTRY if aid not in skip_set]

    if mode == "standalone":
        phases = []
        for i, aid in enumerate(agent_ids, 1):
            defn = AGENT_REGISTRY[aid]
            phases.append(ExecutionPhase(
                phase_number=i,
                agent_ids=[aid],
                parallel=False,
                gate_type=defn.gate_type,
                sla_hours=defn.sla_hours,
                max_rejections=defn.max_rejections,
            ))
        return ExecutionPlan(run_id=run_id, project_id=project_id, mode=mode, phases=phases)

    # Pipeline / triggered mode: group by pipeline_position
    by_pos: dict[int, list[str]] = defaultdict(list)
    for aid in agent_ids:
        defn = AGENT_REGISTRY[aid]
        by_pos[defn.pipeline_position].append(aid)

    phases = []
    for i, pos in enumerate(sorted(by_pos.keys()), 1):
        group = by_pos[pos]
        first_defn = AGENT_REGISTRY[group[0]]
        is_parallel = len(group) > 1
        gate = "all_must_approve" if is_parallel else first_defn.gate_type
        phases.append(ExecutionPhase(
            phase_number=i,
            agent_ids=sorted(group),
            parallel=is_parallel,
            gate_type=gate,
            sla_hours=first_defn.sla_hours,
            max_rejections=first_defn.max_rejections,
        ))

    return ExecutionPlan(run_id=run_id, project_id=project_id, mode=mode, phases=phases)
