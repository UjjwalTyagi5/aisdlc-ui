"""Regression tests for dev agent handoff routing.

M2-06: The sentinel mechanism has been removed and replaced with
typed artifact persistence via artifact_service.py and Redis pub/sub.

The old _append_handoff_to_response and _persist_dev_handoff helpers no longer
exist. These tests document the historical bug and are kept as stubs to avoid
breaking test collection. The new mechanism is tested in test_m2_artifacts.py.
"""
import pytest


@pytest.mark.skip(reason="M2-06: sentinel mechanism removed; see test_m2_artifacts.py")
def test_append_handoff_when_missing():
    pass


@pytest.mark.skip(reason="M2-06: sentinel mechanism removed; see test_m2_artifacts.py")
def test_append_handoff_idempotent():
    pass


@pytest.mark.skip(reason="M2-06: sentinel mechanism removed; see test_m2_artifacts.py")
def test_append_handoff_empty_inputs():
    pass


@pytest.mark.skip(reason="M2-06: sentinel mechanism removed; see test_m2_artifacts.py")
async def test_persist_dev_handoff_sets_active_empty_and_gate_to_payload_to():
    pass


@pytest.mark.skip(reason="M2-06: sentinel mechanism removed; see test_m2_artifacts.py")
async def test_persist_dev_handoff_skips_invalid_payload():
    pass


@pytest.mark.skip(reason="M2-06: sentinel mechanism removed; see test_m2_artifacts.py")
def test_orchestrator_parse_handoff_catches_appended_sentinel():
    pass


@pytest.mark.skip(reason="M2-06: sentinel mechanism removed; see test_m2_artifacts.py")
def test_orchestrator_recognizes_deployment_intent_typos():
    pass
