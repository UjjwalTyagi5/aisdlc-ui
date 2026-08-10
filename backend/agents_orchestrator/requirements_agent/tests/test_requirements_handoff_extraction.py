"""Requirements handoff extraction tests.

M2-06: The sentinel mechanism and _extract_handoff_line helper have
been removed. Pipeline stage transitions are now driven by typed artifact
persistence via artifact_service.py and Redis pub/sub.

Tests are stubbed out to avoid breaking test collection.
See agentic_app/tests/test_m2_artifacts.py for the replacement tests.
"""
import pytest


@pytest.mark.skip(reason="M2-06: _extract_handoff_line removed; see test_m2_artifacts.py")
def test_extracts_handoff_from_assistant_message():
    pass


@pytest.mark.skip(reason="M2-06: _extract_handoff_line removed; see test_m2_artifacts.py")
def test_extracts_handoff_from_tool_message_for_backward_compatibility():
    pass


@pytest.mark.skip(reason="M2-06: _EXPLICIT_DEVELOPMENT_RE removed; see test_m2_artifacts.py")
def test_explicit_development_intent_matches_natural_confirmation():
    pass
