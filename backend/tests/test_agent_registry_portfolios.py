from config.agent_registry import AGENT_DEFAULT_REACH, AGENT_REGISTRY, TRACK_PORTFOLIOS

_PORTFOLIO_1 = [
    "requirements", "design", "development", "code_review",
    "security", "testing", "deployment", "documentation",
]


def test_greenfield_and_enhancement_share_the_same_eight_agent_portfolio():
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


def test_ba_owns_requirements_and_uses_everything_else_in_portfolio_1():
    row = {a: AGENT_DEFAULT_REACH[a]["ba"] for a in _PORTFOLIO_1}
    assert row == {
        "requirements": "owner", "design": "use", "development": "use",
        "code_review": "use", "security": "use", "testing": "use",
        "deployment": "use", "documentation": "use",
    }


def test_security_engineer_owns_only_security_and_reaches_six_others():
    row = {a: AGENT_DEFAULT_REACH[a]["security_engineer"] for a in _PORTFOLIO_1}
    assert row == {
        "requirements": "use", "design": "use", "development": "use",
        "code_review": "use", "security": "owner", "testing": "none",
        "deployment": "use", "documentation": "none",
    }


def test_devops_engineer_has_no_default_reach_to_requirements_design_or_code_review():
    for agent_id in ("requirements", "design", "code_review"):
        assert AGENT_DEFAULT_REACH[agent_id]["devops_engineer"] == "none"


def test_project_admin_owns_every_portfolio_1_agent():
    for agent_id in _PORTFOLIO_1:
        assert AGENT_DEFAULT_REACH[agent_id]["project_admin"] == "owner"
