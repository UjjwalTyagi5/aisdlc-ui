import pytest


def test_security_graph_importable():
    from agents_orchestrator.security_agent.agents.scanner import app
    assert app is not None
    assert hasattr(app, "ainvoke")


def test_trivy_tool_importable():
    from agents_orchestrator.security_agent.tools.trivy_tool import run_trivy_scan
    assert callable(run_trivy_scan)


def test_semgrep_sast_tool_importable():
    from agents_orchestrator.security_agent.tools.semgrep_sast_tool import run_semgrep_sast
    assert callable(run_semgrep_sast)


def test_gitleaks_tool_importable():
    from agents_orchestrator.security_agent.tools.gitleaks_tool import run_gitleaks_scan
    assert callable(run_gitleaks_scan)


def test_security_prompt_exists():
    from agents_orchestrator.security_agent.prompts.security_prompt import SECURITY_SYSTEM_PROMPT
    assert "security" in SECURITY_SYSTEM_PROMPT.lower()
    assert len(SECURITY_SYSTEM_PROMPT) > 100
