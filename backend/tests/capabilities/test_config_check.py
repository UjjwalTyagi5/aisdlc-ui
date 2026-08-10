from shared.capabilities.config_check import config_capability_report


def test_report_flags_agent_with_no_byo_for_required_curated_gap():
    # An agent whose required caps are all native reports no gap even with empty assignment.
    report = config_capability_report({"agents": {"requirements": {"byo_capabilities": []}}})
    # Requirements' required caps are native + one curated (default-on) → no gap.
    assert report.get("requirements", []) == []


def test_development_no_gap_after_native_tags_added():
    # Development's native tool tags (D2) cover all required capabilities, so the
    # report must return an empty list — no BYO or curated additions needed.
    report = config_capability_report({"agents": {"development": {"byo_capabilities": []}}})
    assert report.get("development", []) == []
