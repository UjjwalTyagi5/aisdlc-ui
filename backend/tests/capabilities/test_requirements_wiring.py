"""Contract tests for Task 11: Requirements agent capability-spine wiring.

Pins the preflight contract: a run with a missing required capability must
be blocked with a gap message that names the agent and the missing cap.
A run with all required capabilities must proceed (gap == []).
"""
import pytest
from shared.capabilities.resolution import ResolvedToolset
from shared.capabilities.providers import CapabilityProvider
from shared.capabilities.preflight import preflight_agent, gap_message


def test_requirements_run_blocks_when_required_capability_missing():
    # Simulate a resolution that lacks req.ingest (e.g. ingestion tool unavailable).
    resolved = ResolvedToolset()
    resolved.active = {"board.read": CapabilityProvider("native", "board.read", "x")}
    gap = preflight_agent("requirements", resolved)
    assert "req.ingest" in gap
    msg = gap_message("requirements", gap)
    assert "Requirements Agent" in msg and "req.ingest" in msg


def test_requirements_run_proceeds_when_all_required_present():
    resolved = ResolvedToolset()
    from config.agent_registry import AGENT_REGISTRY
    resolved.active = {
        c: CapabilityProvider("native", c, c)
        for c in AGENT_REGISTRY["requirements"].required_capabilities
    }
    assert preflight_agent("requirements", resolved) == []
