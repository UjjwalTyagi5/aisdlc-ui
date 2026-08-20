"""Isolated unit coverage for Security agent internals that don't need the full
graph or a live LLM key — model resolution, and (added in later tasks of this same
plan) tool-output parsing details.

Deliberately no module-level `pytestmark = pytest.mark.asyncio` — this file mixes
sync tests (model resolution) with async ones (Task 3's tool calls), and marking
sync `def` tests with the asyncio marker is unnecessary. Async tests below are each
decorated individually with `@pytest.mark.asyncio`.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


def test_resolve_model_tries_byok_first_and_returns_it_on_success():
    from agents_orchestrator.security_agent.agents.scanner import _resolve_model
    import sys

    fake_byok_model = MagicMock(name="byok_model")

    # Mock resolve_chat_model at import time by mocking the entire module
    mock_model_resolver = MagicMock()
    mock_model_resolver.resolve_chat_model = MagicMock(return_value=fake_byok_model)

    with patch.dict(sys.modules, {"shared.services.model_resolver": mock_model_resolver}):
        result = _resolve_model({"model_id": "claude-x", "offering_id": "off-1"})

    assert result is fake_byok_model
    mock_model_resolver.resolve_chat_model.assert_called_once()
    call_kwargs = mock_model_resolver.resolve_chat_model.call_args.kwargs
    assert call_kwargs["model_id"] == "claude-x"
    assert call_kwargs["offering_id"] == "off-1"


def test_resolve_model_falls_back_to_raw_chat_anthropic_when_byok_raises():
    from agents_orchestrator.security_agent.agents.scanner import _resolve_model
    import sys

    fake_bound_model = MagicMock(name="fallback_model")
    fake_anthropic_instance = MagicMock()
    fake_anthropic_instance.bind_tools.return_value = fake_bound_model

    # Mock resolve_chat_model to raise, so we test the fallback path
    mock_model_resolver = MagicMock()
    mock_model_resolver.resolve_chat_model = MagicMock(
        side_effect=RuntimeError("no provider configured")
    )

    with patch.dict(sys.modules, {"shared.services.model_resolver": mock_model_resolver}), patch(
        "langchain_anthropic.ChatAnthropic",
        return_value=fake_anthropic_instance,
    ) as mock_chat_anthropic:
        result = _resolve_model({"model_id": None, "offering_id": None})

    assert result is fake_bound_model
    mock_chat_anthropic.assert_called_once()


def _fake_semgrep_completed_process(stdout_obj):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(stdout_obj)
    proc.stderr = ""
    return proc


def test_semgrep_sast_tool_preserves_cwe_tags_alongside_owasp():
    from agents_orchestrator.security_agent.tools import semgrep_sast_tool

    raw_semgrep_output = {
        "results": [
            {
                "check_id": "python.lang.security.audit.subprocess-shell-true",
                "path": "vulnerable.py",
                "start": {"line": 4},
                "end": {"line": 4},
                "extra": {
                    "severity": "ERROR",
                    "message": "shell=True is dangerous",
                    "metadata": {
                        "owasp": ["A03:2021"],
                        "cwe": ["CWE-78: OS Command Injection"],
                    },
                },
            }
        ]
    }

    with patch.object(semgrep_sast_tool, "_SEMGREP_BIN", "/fake/semgrep"), patch(
        "pathlib.Path.exists", return_value=True
    ), patch(
        "subprocess.run",
        return_value=_fake_semgrep_completed_process(raw_semgrep_output),
    ):
        result_json = semgrep_sast_tool.run_semgrep_sast.invoke(
            {"target_path": "/fake/target"}
        )

    result = json.loads(result_json)
    assert result["status"] == "ok"
    finding = result["findings"][0]
    assert finding["owasp_category"] == ["A03:2021"]
    assert finding["cwe"] == ["CWE-78: OS Command Injection"]
