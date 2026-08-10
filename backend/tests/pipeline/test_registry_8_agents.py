from config.agent_registry import AGENT_REGISTRY
from shared.services.artifact_service import _COLUMN_MAP


def test_eight_agents_with_output_artifacts():
    for a in ["requirements", "design", "development", "testing", "code_review",
              "security", "deployment", "documentation"]:
        assert a in AGENT_REGISTRY, f"{a} missing from registry"
    assert AGENT_REGISTRY["deployment"].output_artifact == "deployment_artifacts"
    assert AGENT_REGISTRY["documentation"].output_artifact == "documentation_artifacts"
    assert _COLUMN_MAP["deployment"] == "deployment_artifacts"
    assert _COLUMN_MAP["documentation"] == "documentation_artifacts"
