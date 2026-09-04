from config.agent_registry import AGENT_DEFAULT_REACH, AGENT_REGISTRY, TRACK_PORTFOLIOS

# A deliberate second copy, not an import: restating the roster here is what makes a
# change to it fail a test rather than pass silently. The PM agent (0041/0042) sits
# between design and development.
_PORTFOLIO_1 = [
    "requirements", "design", "plan", "development", "code_review",
    "security", "testing", "deployment", "documentation",
]


def test_greenfield_and_enhancement_share_the_same_nine_agent_portfolio():
    assert TRACK_PORTFOLIOS["greenfield"] == _PORTFOLIO_1
    assert TRACK_PORTFOLIOS["enhancement"] == _PORTFOLIO_1


def test_portfolios_2_through_4_are_empty_until_their_agents_are_built():
    assert TRACK_PORTFOLIOS["modernization"] == []
    assert TRACK_PORTFOLIOS["rpa_infra"] == []
    assert TRACK_PORTFOLIOS["data_engineering"] == []


def test_every_portfolio_1_agent_has_a_default_reach_row():
    for agent_id in _PORTFOLIO_1:
        assert agent_id in AGENT_DEFAULT_REACH
        assert agent_id in AGENT_REGISTRY


def test_ba_owns_requirements_and_documentation_and_reaches_nothing_else():
    """One agent, one role. The `use` tier that let a BA open all nine is gone —
    extra access is granted per person per project instead."""
    row = {a: AGENT_DEFAULT_REACH[a]["ba"] for a in _PORTFOLIO_1}
    assert row == {
        "requirements": "owner", "documentation": "owner",
        "design": "none", "plan": "none", "development": "none",
        "code_review": "none", "security": "none", "testing": "none",
        "deployment": "none",
    }


def test_security_engineer_owns_only_security():
    row = {a: AGENT_DEFAULT_REACH[a]["security_engineer"] for a in _PORTFOLIO_1}
    assert row == {
        "security": "owner",
        "requirements": "none", "design": "none", "development": "none",
        "plan": "none", "code_review": "none", "testing": "none",
        "deployment": "none", "documentation": "none",
    }


def test_every_delivery_role_reaches_only_what_it_owns():
    """The property the whole table now rests on. A role with reach on an agent it
    does not own means the `use` tier crept back in under another name."""
    owners = {
        "requirements": "ba", "documentation": "ba",
        "design": "architect", "code_review": "architect",
        "development": "developer", "testing": "qa",
        "security": "security_engineer", "deployment": "devops_engineer",
        "plan": "scrum_master",
    }
    for agent_id, reach in AGENT_DEFAULT_REACH.items():
        for role, involvement in reach.items():
            if role == "project_admin":
                continue  # universal fallback approver, owner everywhere by design
            expected = "owner" if owners[agent_id] == role else "none"
            assert involvement == expected, (
                f"{agent_id}.{role} is {involvement!r}, expected {expected!r}"
            )


def test_devops_engineer_has_no_default_reach_to_requirements_design_or_code_review():
    for agent_id in ("requirements", "design", "code_review"):
        assert AGENT_DEFAULT_REACH[agent_id]["devops_engineer"] == "none"


def test_project_admin_owns_every_portfolio_1_agent():
    for agent_id in _PORTFOLIO_1:
        assert AGENT_DEFAULT_REACH[agent_id]["project_admin"] == "owner"
