"""Task 8 — Security structured capture + current-run read + standalone preservation.

Covers:
  * `_capture_security_artifact` prefers the structured artifact written by
    `submit_security_review` into the scan session's `last_artifact` (falling back to
    the final-message summary only when no structured artifact exists), and
  * the dual-mode `read_design_artifacts` in `security_tools`: the new current-run
    reader exists AND the standalone reader is NOT removed (the live-tested standalone
    Security page must keep working byte-for-byte).

Any DB-gated behavioural assertion is deferred to the Task 17 integration pass.
"""
from workflows.activities.security_activity import (
    _capture_security_artifact,
    _to_persistence_artifact,
)


class _S:  # fake scan session state
    last_artifact = {"risk_score": "high", "findings": [{"id": "F1"}], "version": 1}


def test_capture_prefers_structured(monkeypatch):
    import workflows.activities.security_activity as m

    monkeypatch.setattr(m, "_get_scan_session", lambda rid: _S())
    art = _capture_security_artifact(run_id="r1", final_state={"messages": []})
    assert art["risk_score"] == "high" and art["findings"] == [{"id": "F1"}]


def test_capture_falls_back_to_summary(monkeypatch):
    import workflows.activities.security_activity as m

    class _Empty:
        last_artifact = None

    class _Msg:
        content = "no structured artifact — plain summary"

    monkeypatch.setattr(m, "_get_scan_session", lambda rid: _Empty())
    art = _capture_security_artifact(run_id="r2", final_state={"messages": [_Msg()]})
    assert art.get("scan_summary") == "no structured artifact — plain summary"


def test_idempotency_reconstructs_rich_artifact_without_crashing():
    """Regression: the Temporal idempotency guard must reconstruct the RICH persisted
    `submit_security_review` dict — where `remediation_plan` is a STRING — without
    raising. The old guard did `SecurityArtifact(**existing)`, which raises a
    ValidationError because the persistence model types `remediation_plan: List[dict]`.
    Routing through `_to_persistence_artifact` (tolerant `.get()` mapping) must succeed.
    """
    existing = {
        "risk_score": "high",
        "summary": "2 issues found",
        "remediation_plan": "do X then Y",  # STRING in the rich shape — would crash SecurityArtifact(**existing)
        "findings": [
            {"id": "F1", "category": "sca", "severity": "high"},
            {"id": "F2", "category": "sast", "severity": "medium"},
        ],
        "signoff": {"decision": "pass"},
        "version": 3,
    }

    art = _to_persistence_artifact(existing, version=1)

    assert art.risk_score == "high"
    assert art.scan_summary == "2 issues found"
    assert art.security_sign_off is True
    assert art.version == 3
    assert [f["id"] for f in art.dependency_findings] == ["F1"]
    assert [f["id"] for f in art.code_findings] == ["F2"]


def test_security_has_current_run_reader():
    from agents_orchestrator.security_agent.tools import security_tools

    # The new helper reads the design column off the CURRENT run, not project-latest.
    assert hasattr(security_tools, "_run_design_artifact")


def test_security_standalone_reader_preserved():
    from agents_orchestrator.security_agent.tools import security_tools

    # The standalone helper must still exist and remain the fallback path.
    assert hasattr(security_tools, "_latest_design_artifact")
