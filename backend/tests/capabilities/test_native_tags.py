from shared.capabilities import native_tags, taxonomy
from config.agent_registry import AGENT_REGISTRY


def test_all_tag_values_are_valid_capabilities():
    # A tag value may be a single capability or a list of them (multi-cap tools).
    for agent_id, mapping in native_tags.NATIVE_TAGS.items():
        caps = [c for value in mapping.values() for c in native_tags._as_caps(value)]
        taxonomy.assert_valid(caps)


def test_requirements_native_covers_its_native_required_caps():
    provided = native_tags.native_capabilities("requirements")
    must_be_native = {
        "req.ingest", "req.gap.detect", "story.generate", "story.ac.normalize",
        "doc.generate.brd", "doc.generate.risk", "req.payload.build",
        "board.read", "artifact.write",
    }
    assert must_be_native <= provided


def test_native_capabilities_unknown_agent_is_empty():
    assert native_tags.native_capabilities("does-not-exist") == set()
