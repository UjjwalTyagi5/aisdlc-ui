"""Config-time capability-gap report (DP6 warn half).

Computes the gap from STATIC inputs only — native tags, curated catalog default-on,
and admin-asserted BYO capabilities — without loading any live tool. Used to warn at
project-config save. The run-start hard block (Task 11) uses the live-resolved set.
"""
from __future__ import annotations

from config.agent_registry import AGENT_REGISTRY
from shared.capabilities import native_tags, system_catalog
from shared.capabilities.preflight import capability_gap


def config_capability_report(project_assignment: dict) -> "dict[str, list[str]]":
    agents_cfg = (project_assignment or {}).get("agents", {})
    report: dict[str, list[str]] = {}
    for agent_id, defn in AGENT_REGISTRY.items():
        provided: set[str] = set()
        provided |= native_tags.native_capabilities(agent_id)
        disabled = set((agents_cfg.get(agent_id, {}) or {}).get("disabled_curated", []))
        provided |= {ct.capability for ct in system_catalog.curated_for_agent(agent_id, disabled)}
        provided |= set((agents_cfg.get(agent_id, {}) or {}).get("byo_capabilities", []))
        report[agent_id] = capability_gap(defn.required_capabilities, provided)
    return report
