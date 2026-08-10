"""Unit tests for the pure advance-vs-gate decision rule.

`_advance_decision` is the single source of truth for whether a stage should
advance in-chat, open its approval gate, or wait — driven by (artifact
present) + (gate_type), never by whether a HANDOFF:: sentinel happened to
appear that turn. The pipeline NEVER auto-advances to the next agent: moving
on is always the user's explicit choice (they approve the gate, or switch).
Only the terminal `auto_approve` stage (Documentation) advances on its own.
Both `_advance_or_gate` (the HANDOFF path) and `_maybe_detect_conversational_gate`
(the artifact-present-no-handoff path) route through this same function.
"""
from agents_orchestrator.orchestrator.copilot_api import _advance_decision


def test_no_artifact_always_waits():
    assert _advance_decision(False, False, "auto_approve") == "wait"
    assert _advance_decision(False, True, "approval_required") == "wait"
    assert _advance_decision(False, False, "mandatory") == "wait"


def test_auto_approve_advances_once_artifact_present():
    assert _advance_decision(True, False, "auto_approve") == "advance"


def test_approval_required_always_gates_never_auto_advances():
    # Even a driver who CAN approve does not auto-advance — the gate opens and
    # waits for their explicit approval (no runaway stage cascade).
    assert _advance_decision(True, True, "approval_required") == "gate"
    assert _advance_decision(True, False, "approval_required") == "gate"


def test_mandatory_always_gates():
    assert _advance_decision(True, True, "mandatory") == "gate"
    assert _advance_decision(True, False, "mandatory") == "gate"
