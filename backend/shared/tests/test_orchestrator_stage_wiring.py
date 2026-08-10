from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "agents" / "orchestrator" / "orchestrator_api.py"


def test_orchestrator_routes_monitoring_feedback_stage():
    source = ORCHESTRATOR.read_text(encoding="utf-8")

    assert '"monitoring_feedback": call_monitoring_agent' in source
    assert '"monitoring_feedback": ("Monitoring Feedback Agent", call_monitoring_agent)' in source
    assert '("ingestion", "design", "development", "testing", "deployment", "monitoring_feedback")' in source
    assert "monitoring_feedback: Handles post-deployment monitoring" in source
