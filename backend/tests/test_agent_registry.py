import pytest
from config.agent_registry import AGENT_REGISTRY, AgentDefinition, get_pipeline_order


def test_agent_definition_has_orchestration_fields():
    req = AGENT_REGISTRY["requirements"]
    assert isinstance(req, AgentDefinition)
    assert req.gate_type == "approval_required"
    assert req.sla_hours == 24
    assert req.can_parallel_with == []
    assert req.max_rejections == 1


def test_all_agents_present():
    assert set(AGENT_REGISTRY.keys()) == {
        "requirements", "design", "development", "testing", "deployment",
        "code_review", "security",
    }


def test_pipeline_order_returns_phases():
    phases = get_pipeline_order()
    assert len(phases) >= 5
    assert phases[0] == ["requirements"]
    assert phases[1] == ["design"]
    for phase in phases:
        for agent_id in phase:
            assert agent_id in AGENT_REGISTRY


def test_code_review_agent_registered():
    cr = AGENT_REGISTRY["code_review"]
    assert cr.name == "Code Review Agent"
    assert cr.pipeline_position == 4
    assert cr.output_artifact == "code_review_artifacts"
    assert "development_artifacts" in cr.input_artifacts
    assert cr.gate_type == "approval_required"
    assert cr.can_parallel_with == ["security"]


def test_security_agent_registered():
    from config.agent_registry import AGENT_REGISTRY
    sec = AGENT_REGISTRY["security"]
    assert sec.name == "Security Agent"
    assert sec.pipeline_position == 4
    assert sec.output_artifact == "security_artifacts"
    assert "development_artifacts" in sec.input_artifacts
    assert sec.gate_type == "approval_required"
    assert sec.can_parallel_with == ["code_review"]


def test_pipeline_order_respects_position():
    phases = get_pipeline_order()
    flat = [aid for phase in phases for aid in phase]
    positions = [AGENT_REGISTRY[aid].pipeline_position for aid in flat]
    assert positions == sorted(positions)
