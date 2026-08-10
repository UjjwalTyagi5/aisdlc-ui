from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.pipeline_context import (  # noqa: E402
    build_pipeline_context,
    empty_pipeline_context,
    pipeline_context_json,
)
from config.agent_context import build_agent_input_text, parse_pipeline_context  # noqa: E402


def test_empty_pipeline_context_defaults_to_azure_devops():
    context = empty_pipeline_context(session_id="s1", user_id="u1")

    assert context["session_id"] == "s1"
    assert context["user_id"] == "u1"
    assert context["board_provider"] == "azure_devops"
    assert context["requirements"] == {}
    assert context["last_handoff_event"] == {}


def test_build_pipeline_context_uses_typed_artifacts():
    context = build_pipeline_context(
        session_id="s1",
        user_id="u1",
        artifacts={
            "requirements_payload": {"provider_kind": "jira", "project": "PAY"},
            "design_artifacts": {"api_contracts": "openapi"},
            "development_artifacts": {"branch": "feature/pay"},
            "testing_artifacts": {"status": "passed"},
            "last_handoff_event": {"to": "design"},
        },
    )

    assert context["board_provider"] == "jira"
    assert context["requirements"]["project"] == "PAY"
    assert context["design"]["api_contracts"] == "openapi"
    assert context["development"]["branch"] == "feature/pay"
    assert context["testing"]["status"] == "passed"
    assert context["last_handoff_event"]["to"] == "design"


def test_build_pipeline_context_explicit_values_override_artifacts():
    context = build_pipeline_context(
        session_id="s1",
        user_id="u1",
        provider_kind="azure_devops",
        artifacts={
            "requirements_payload": {"provider_kind": "jira"},
            "last_handoff_event": {"to": "design"},
        },
        last_handoff_event={"to": "development"},
    )

    assert context["board_provider"] == "azure_devops"
    assert context["last_handoff_event"]["to"] == "development"


def test_pipeline_context_json_handles_non_json_values():
    context = {"created": Path("artifact.md")}

    assert pipeline_context_json(context) == '{"created": "artifact.md"}'


def test_parse_pipeline_context_rejects_invalid_values():
    assert parse_pipeline_context('{"board_provider": "jira"}') == {"board_provider": "jira"}
    assert parse_pipeline_context("not json") == {}
    assert parse_pipeline_context(["not", "a", "dict"]) == {}


def test_build_agent_input_text_adds_selected_structured_context():
    text = build_agent_input_text(
        conversation_context="Requirements Context",
        task_intent="Create design",
        pipeline_context={
            "session_id": "s1",
            "board_provider": "jira",
            "requirements": {"project": "PAY"},
            "development": {"branch": "feature/pay"},
        },
        pipeline_sections=("requirements",),
    )

    assert "Requirements Context" in text
    assert "[STRUCTURED PIPELINE CONTEXT JSON]" in text
    assert '"board_provider": "jira"' in text
    assert '"requirements": {"project": "PAY"}' in text
    assert "feature/pay" not in text
    assert "Task intent: Create design" in text
