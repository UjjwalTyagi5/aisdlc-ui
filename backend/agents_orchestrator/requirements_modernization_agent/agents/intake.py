"""The Requirements agent's graph in migration-intent mode (Track 3 — Code Modernization).

A separate agent from Portfolio 1's `requirements` — its own id, prompt, tools and output
column — because Track 3 is its own portfolio (help/multi-track-agent-access-design.md
§1.4) and its work product is a migration-intent brief, not a story backlog.
"""
from __future__ import annotations

from agents_orchestrator.modernization_common.graph import build_tool_agent_graph
from agents_orchestrator.requirements_modernization_agent.prompts.migration_intent_prompt import (
    MIGRATION_INTENT_SYS_MESSAGE,
)
from agents_orchestrator.requirements_modernization_agent.tools.brief_tools import TOOLS

AGENT_ID = "requirements_modernization"

app = build_tool_agent_graph(agent_type=AGENT_ID, tools=TOOLS, checkpoint_name=AGENT_ID)

__all__ = ["AGENT_ID", "MIGRATION_INTENT_SYS_MESSAGE", "TOOLS", "app"]
