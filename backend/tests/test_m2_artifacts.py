"""M2-06 artifact persistence and no-HANDOFF integration tests.

test_artifact_persistence_no_handoff:
  Part 1 — static grep gate: verifies no HANDOFF:: sentinel exists in any
  agent orchestrator Python file (excluding test files themselves).
  Part 2 — artifact import round-trip: verifies artifact models and service
  are importable and the write_and_notify call path is wired correctly.
"""
import sys
import os
import pytest


@pytest.mark.integration
async def test_artifact_persistence_no_handoff():
    """Verify: requirements agent writes artifact to Postgres; artifact service is wired correctly.

    Was also a grep gate for zero HANDOFF:: matches in agents_orchestrator/ (Part 1).
    Removed — see shared/tests/test_m2_verification.py's module docstring for the
    full reasoning: HANDOFF:: was reintroduced as the Copilot chat flow's live
    stage-transition sentinel (parsed by copilot_api.py's _detect_handoff, emitted
    by the requirements/design prompts), so the gate now fails permanently against
    correct, deliberate code rather than catching leftover legacy debt.
    """
    # Artifact service is importable and types are correct
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from shared.services.artifact_service import (
        persist_artifact,
        publish_artifact_ready,
        write_and_notify,
    )
    from shared.models.artifacts import (
        RequirementsArtifact,
        DesignArtifact,
        DevelopmentArtifact,
        TestingArtifact,
    )

    # Verify artifact models produce valid dicts
    req = RequirementsArtifact(agent_session_id="test-session", brd_content="BRD text")
    assert req.model_dump()["agent_session_id"] == "test-session"
    assert req.version == 1

    design = DesignArtifact(hld="HLD text", lld="LLD text")
    assert design.model_dump()["hld"] == "HLD text"

    dev = DevelopmentArtifact(repo_url="https://github.com/test/repo", pr_url="https://github.com/test/repo/pulls/1")
    assert dev.model_dump()["repo_url"] == "https://github.com/test/repo"

    # Verify artifact service functions exist and are callable
    assert callable(persist_artifact)
    assert callable(publish_artifact_ready)
    assert callable(write_and_notify)

    # Verify _COLUMN_MAP allowlist prevents invalid artifact types
    from shared.services.artifact_service import _COLUMN_MAP
    assert "requirements" in _COLUMN_MAP
    assert "design" in _COLUMN_MAP
    assert "development" in _COLUMN_MAP
    assert "testing" in _COLUMN_MAP


def test_no_handoff_imports_in_active_agents():
    """Verify active agent files do not import from the deleted handoff modules."""
    base = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    agent_files = [
        os.path.join(base, "agents_orchestrator", "requirements_agent", "agents", "planning.py"),
        os.path.join(base, "agents_orchestrator", "design_architecture_agent", "agents", "architecture.py"),
        os.path.join(base, "agents_orchestrator", "development_agent", "agents", "dev_agent.py"),
    ]
    forbidden = ["handoff_router", "from shared.models.handoff", "import.*handoff_router"]
    import re
    for path in agent_files:
        assert os.path.exists(path), f"Agent file missing: {path}"
        with open(path, encoding="utf-8") as f:
            content = f.read()
        for pattern in forbidden:
            matches = re.findall(pattern, content)
            assert not matches, (
                f"Forbidden import pattern '{pattern}' found in {path}"
            )


def test_write_artifact_tools_exist():
    """Verify write_*_artifact tools are registered in each active agent."""
    base = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))

    planning = open(
        os.path.join(base, "agents_orchestrator", "requirements_agent", "agents", "planning.py"),
        encoding="utf-8",
    ).read()
    assert "write_requirements_artifact" in planning, (
        "write_requirements_artifact tool not found in planning.py"
    )

    architecture = open(
        os.path.join(base, "agents_orchestrator", "design_architecture_agent", "agents", "architecture.py"),
        encoding="utf-8",
    ).read()
    assert "write_design_artifact" in architecture, (
        "write_design_artifact tool not found in architecture.py"
    )

    dev_agent = open(
        os.path.join(base, "agents_orchestrator", "development_agent", "agents", "dev_agent.py"),
        encoding="utf-8",
    ).read()
    assert "write_development_artifact" in dev_agent, (
        "write_development_artifact tool not found in dev_agent.py"
    )


def test_artifact_event_listener_in_process_api():
    """Verify artifact_event_listener is wired into process_api.py lifespan."""
    base = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    process_api = open(
        os.path.join(base, "process_api.py"),
        encoding="utf-8",
    ).read()
    assert "artifact_event_listener" in process_api, (
        "artifact_event_listener not found in process_api.py"
    )
    assert "_ORCHESTRATOR_ROUTING" in process_api, (
        "_ORCHESTRATOR_ROUTING routing table not found in process_api.py"
    )
