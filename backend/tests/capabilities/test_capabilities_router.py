from shared.routers.capabilities import build_agent_capability_view


def test_view_includes_required_and_native_for_requirements():
    view = build_agent_capability_view()
    req = next(a for a in view if a["agent_id"] == "requirements")
    assert "req.ingest" in req["required"]
    native_caps = {n["capability"] for n in req["native"]}
    assert "board.read" in native_caps
    curated_caps = {c["capability"] for c in req["curated"]}
    assert "req.quality.analyze" in curated_caps


def test_view_native_section_is_not_marked_configurable():
    view = build_agent_capability_view()
    req = next(a for a in view if a["agent_id"] == "requirements")
    # native entries carry no enable/disable flag — informational only (D7)
    assert all("enabled" not in n for n in req["native"])
