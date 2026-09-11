"""The Discovery & Assessment agent's graph (Track 3 — Code Modernization).

`app` is what both entry points run: the Orchestrator's dispatch (through
`orchestrator2/registry.py`) and the standalone socket (`discovery_agent_api.py`).
"""
from __future__ import annotations

from agents_orchestrator.discovery_agent.prompts.discovery_prompt import DISCOVERY_SYS_MESSAGE
from agents_orchestrator.discovery_agent.tools.assessment_tools import TOOLS
from agents_orchestrator.modernization_common.graph import build_tool_agent_graph

AGENT_ID = "discovery"

app = build_tool_agent_graph(agent_type=AGENT_ID, tools=TOOLS, checkpoint_name=AGENT_ID)

__all__ = ["AGENT_ID", "DISCOVERY_SYS_MESSAGE", "TOOLS", "app"]
