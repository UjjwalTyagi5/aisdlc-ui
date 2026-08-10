from config.agent_registry import AGENT_REGISTRY
from shared.capabilities import taxonomy


def test_every_agent_declares_required_capabilities():
    for agent_id, defn in AGENT_REGISTRY.items():
        assert isinstance(defn.required_capabilities, list)
        assert defn.required_capabilities, f"{agent_id} has no required_capabilities"


def test_all_declared_capabilities_are_in_taxonomy():
    for agent_id, defn in AGENT_REGISTRY.items():
        taxonomy.assert_valid(defn.required_capabilities)   # raises if unknown
        taxonomy.assert_valid(defn.optional_capabilities)


def test_requirements_requires_core_caps():
    req = AGENT_REGISTRY["requirements"]
    for cap in ["req.ingest", "req.quality.analyze", "story.generate", "artifact.write"]:
        assert cap in req.required_capabilities
