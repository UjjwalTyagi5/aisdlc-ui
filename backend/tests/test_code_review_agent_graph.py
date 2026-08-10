import pytest


def test_code_review_graph_importable():
    from agents_orchestrator.code_review_agent.agents.reviewer import app
    assert app is not None
    assert hasattr(app, "ainvoke")


def test_semgrep_tool_importable():
    from agents_orchestrator.code_review_agent.tools.semgrep_tool import run_semgrep_scan
    assert callable(run_semgrep_scan)


def test_diff_tool_importable():
    from agents_orchestrator.code_review_agent.tools.diff_tool import analyze_diff
    assert callable(analyze_diff)


def test_review_prompt_exists():
    from agents_orchestrator.code_review_agent.prompts.review_prompt import CODE_REVIEW_SYSTEM_PROMPT
    assert "code review" in CODE_REVIEW_SYSTEM_PROMPT.lower()
    assert len(CODE_REVIEW_SYSTEM_PROMPT) > 100
