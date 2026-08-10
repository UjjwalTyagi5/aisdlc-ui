from workflows.activities._base import _COLUMN_MAP
from config.agent_registry import AGENT_REGISTRY


def test_column_map_covers_all_agents_with_output():
    for agent_id, defn in AGENT_REGISTRY.items():
        if defn.output_artifact:
            assert agent_id in _COLUMN_MAP, f"Missing column map entry for {agent_id}"
            assert _COLUMN_MAP[agent_id] == defn.output_artifact


def test_column_map_excludes_agents_without_output():
    for agent_id, defn in AGENT_REGISTRY.items():
        if not defn.output_artifact:
            assert agent_id not in _COLUMN_MAP
