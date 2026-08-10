"""Per-project agent policy — controls which agents run and how.

Stored as a JSONB column on the Project ORM model (future migration).
For now, this model is used in-memory by the execution plan builder
when constructing plans from API requests.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel


class ProjectAgentPolicy(BaseModel):
    project_id: str
    active_agents: Optional[List[str]] = None
    skip_agents: List[str] = []
    auto_approve_agents: List[str] = []
    gate_overrides: Dict[str, str] = {}
    sla_overrides: Dict[str, int] = {}
    # MCP server ids applied to EVERY stage of this project.
    mcp_servers_all: List[str] = []
    # MCP server ids applied to a specific stage only: {agent_id: [server_id, ...]}.
    # A stage's effective set is mcp_servers_all ∪ mcp_servers.get(agent_id, []).
    mcp_servers: Dict[str, List[str]] = {}

    def mcp_servers_for(self, agent_id: str) -> List[str]:
        """Effective MCP server ids for one stage (all-stage ∪ stage-specific)."""
        merged = list(self.mcp_servers_all) + list(self.mcp_servers.get(agent_id, []))
        seen: set[str] = set()
        return [s for s in merged if not (s in seen or seen.add(s))]
