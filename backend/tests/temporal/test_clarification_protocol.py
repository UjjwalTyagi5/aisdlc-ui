"""Wave-0 scaffold: detect_clarification_need detection + sandbox-safety (REQ-M10-01).

Tier: unit (pure functions — no Temporal server, no DB).

Covers:
  - detect_clarification_need fires on a "?"-terminated message with no artifact
  - detect_clarification_need fires on a "STOP HERE" sentinel message with no artifact
  - detect_clarification_need returns None when a typed artifact key is present
  - detect_clarification_need returns None on empty messages
  - Anthropic list-of-content-blocks is flattened before the "?"/STOP HERE check
  - shared.models.workflow_models imports cleanly without sqlalchemy/asyncpg in the
    sandbox (RESEARCH Pitfall 3 / T-10.1-03)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from workflows.activities._base import detect_clarification_need


def test_question_mark_triggers_clarification_with_pinned_thread_id():
    final_state = {"messages": [AIMessage(content="What is the scope of this feature?")]}

    result = detect_clarification_need(final_state, run_id="run-123", agent_type="requirements")

    assert result is not None
    assert result.thread_id == "run-123"
    assert result.agent_type == "requirements"
    assert result.phase == "requirements"
    assert result.questions == ["What is the scope of this feature?"]
    assert result.clarification_id  # default_factory populated


def test_stop_here_sentinel_triggers_clarification():
    final_state = {
        "messages": [AIMessage(content="*** STOP HERE. Do NOT call any more tools. ***")]
    }

    result = detect_clarification_need(final_state, run_id="run-456", agent_type="design")

    assert result is not None
    assert result.thread_id == "run-456"
    assert result.agent_type == "design"
    assert result.phase == "design"


@pytest.mark.parametrize(
    "artifact_key",
    ["requirements_payload", "design_artifacts", "development_artifacts", "testing_artifacts"],
)
def test_artifact_present_suppresses_clarification(artifact_key):
    final_state = {
        "messages": [AIMessage(content="Anything else?")],
        artifact_key: {"version": 1},
    }

    result = detect_clarification_need(final_state, run_id="run-789", agent_type="requirements")

    assert result is None


def test_empty_messages_returns_none():
    result = detect_clarification_need({"messages": []}, run_id="run-000", agent_type="requirements")

    assert result is None


def test_missing_messages_key_returns_none():
    result = detect_clarification_need({}, run_id="run-000", agent_type="requirements")

    assert result is None


def test_anthropic_content_blocks_are_flattened_before_check():
    final_state = {
        "messages": [
            AIMessage(
                content=[
                    {"type": "text", "text": "Before continuing, "},
                    {"type": "text", "text": "which environment should this target?"},
                ]
            )
        ]
    }

    result = detect_clarification_need(final_state, run_id="run-flat", agent_type="development")

    assert result is not None
    assert result.questions == ["Before continuing,  which environment should this target?"]


def test_normal_completion_without_question_returns_none():
    final_state = {"messages": [AIMessage(content="All done. Artifact generated.")]}

    result = detect_clarification_need(final_state, run_id="run-done", agent_type="requirements")

    assert result is None


def test_workflow_models_sandbox_safe_import():
    """RESEARCH Pitfall 3 / T-10.1-03: importing workflow_models alone must not
    pull sqlalchemy/asyncpg into sys.modules — the Temporal workflow sandbox
    imports this module via imports_passed_through() and a DB import here would
    be a determinism sandbox violation at worker startup.
    """
    agentic_app_root = Path(__file__).parents[2]
    probe = (
        "import sys; "
        "import shared.models.workflow_models; "
        "bad = [m for m in sys.modules if m.startswith(('sqlalchemy', 'asyncpg'))]; "
        "assert not bad, f'forbidden modules imported: {bad}'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(agentic_app_root),
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
