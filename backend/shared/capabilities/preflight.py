"""Capability-gap pre-flight (decision DP6).

required − provided. Empty list = the agent can do its job with the assigned tools.
Run at project-config save (warn) and at run-start (hard block).
"""
from __future__ import annotations

from config.agent_registry import AGENT_REGISTRY


def capability_gap(required: list[str], provided: "set[str]") -> list[str]:
    return sorted([c for c in required if c not in provided])


def gap_message(agent_id: str, gap: list[str]) -> str:
    defn = AGENT_REGISTRY.get(agent_id)
    name = defn.name if defn else agent_id
    caps = ", ".join(f"`{c}`" for c in gap)
    return (
        f"{name} requires {caps}, but no assigned tool provides "
        f"{'them' if len(gap) > 1 else 'it'}. Enable the matching connector or a "
        f"capability-tagged MCP server, then retry."
    )


def preflight_agent(agent_id: str, resolved) -> list[str]:
    defn = AGENT_REGISTRY.get(agent_id)
    if defn is None:
        return []
    return capability_gap(defn.required_capabilities, set(resolved.active.keys()))
