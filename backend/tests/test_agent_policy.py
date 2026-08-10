from shared.models.agent_policy import ProjectAgentPolicy


def test_default_policy():
    policy = ProjectAgentPolicy(project_id="p1")
    assert policy.active_agents is None
    assert policy.skip_agents == []
    assert policy.auto_approve_agents == []


def test_policy_with_overrides():
    policy = ProjectAgentPolicy(
        project_id="p1",
        active_agents=["requirements", "design", "development"],
        skip_agents=["deployment"],
        auto_approve_agents=["documentation"],
        sla_overrides={"requirements": 12},
    )
    assert "deployment" in policy.skip_agents
    assert policy.sla_overrides["requirements"] == 12


def test_policy_serialization():
    policy = ProjectAgentPolicy(project_id="p1", skip_agents=["testing"])
    data = policy.model_dump()
    restored = ProjectAgentPolicy(**data)
    assert restored.skip_agents == ["testing"]
