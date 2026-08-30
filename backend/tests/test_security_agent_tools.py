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

import pytest


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


def test_resolve_model_propagates_a_resolution_failure():
    """A failed model resolution must surface, NOT fall back to the platform key.

    This test previously asserted the opposite. That fallback was the bug: the same
    catch-all swallowed the ImportError from `resolve_chat_model`, which did not exist
    in model_resolver at all, so every security scan for every tenant silently ran on
    the platform's ANTHROPIC_API_KEY — skipping budgets, model grants, rate limits and
    the no-training call kwargs.

    Whether a local-dev fallback is permitted is now resolve_chat_model's single
    decision, gated on AGENT_RUNTIME_MODE. See tests/test_byok_no_platform_fallback.py.
    """
    from agents_orchestrator.security_agent.agents.scanner import _resolve_model
    import sys

    mock_model_resolver = MagicMock()
    mock_model_resolver.resolve_chat_model = MagicMock(
        side_effect=RuntimeError("no provider configured")
    )

    with patch.dict(sys.modules, {"shared.services.model_resolver": mock_model_resolver}), patch(
        "langchain_anthropic.ChatAnthropic"
    ) as mock_chat_anthropic:
        with pytest.raises(RuntimeError, match="no provider configured"):
            _resolve_model({"model_id": "claude-caller-requested-model", "offering_id": None})

    mock_chat_anthropic.assert_not_called()


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


import pathlib as _pathlib


@pytest.mark.asyncio
async def test_generate_sbom_cross_references_cached_trivy_findings():
    from agents_orchestrator.security_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.security_agent.tools import security_tools
    from config.ws_helper import set_session_id

    session_id = "sbom-cross-ref-test"
    clear_session(session_id)
    set_session_id(session_id)
    s = get_session(session_id)
    s.last_trivy_findings = [
        {"cve": "CVE-2018-1000656", "package": "flask", "installed_version": "0.12.2"},
        {"cve": "CVE-2019-1010083", "package": "flask", "installed_version": "0.12.2"},
    ]

    with patch.object(
        security_tools, "_work_dir", return_value=_pathlib.Path("/fake/does/not/matter")
    ), patch.object(
        security_tools.pathlib.Path, "exists", return_value=True
    ), patch.object(
        security_tools.os, "walk", return_value=[("/fake/does/not/matter", [], ["requirements.txt"])]
    ), patch.object(
        security_tools.pathlib.Path, "read_text", return_value="flask==0.12.2\n"
    ):
        result_json = await security_tools.generate_sbom.ainvoke({})

    result = json.loads(result_json)
    assert result["vulnerability_data"] == "trivy"
    flask_component = next(c for c in result["components"] if c["name"] == "flask")
    assert flask_component["vulnerabilities"] == 2

    clear_session(session_id)


@pytest.mark.asyncio
async def test_generate_sbom_leaves_vulnerabilities_null_when_no_trivy_scan_ran_yet():
    from agents_orchestrator.security_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.security_agent.tools import security_tools
    from config.ws_helper import set_session_id

    session_id = "sbom-cross-ref-empty-test"
    clear_session(session_id)
    set_session_id(session_id)
    # No last_trivy_findings set -- get_session() default is None ("not scanned yet").

    with patch.object(
        security_tools, "_work_dir", return_value=_pathlib.Path("/fake/does/not/matter")
    ), patch.object(
        security_tools.pathlib.Path, "exists", return_value=True
    ), patch.object(
        security_tools.os, "walk", return_value=[("/fake/does/not/matter", [], ["requirements.txt"])]
    ), patch.object(
        security_tools.pathlib.Path, "read_text", return_value="flask==0.12.2\n"
    ):
        result_json = await security_tools.generate_sbom.ainvoke({})

    result = json.loads(result_json)
    assert result["vulnerability_data"] == "not_scanned_yet"
    flask_component = next(c for c in result["components"] if c["name"] == "flask")
    assert flask_component["vulnerabilities"] is None

    clear_session(session_id)


@pytest.mark.asyncio
async def test_generate_sbom_reports_a_real_zero_when_trivy_ran_but_found_no_match():
    from agents_orchestrator.security_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.security_agent.tools import security_tools
    from config.ws_helper import set_session_id

    session_id = "sbom-no-match-test"
    clear_session(session_id)
    set_session_id(session_id)
    s = get_session(session_id)
    # scan_dependencies DID run (list is non-empty, so not None) but found nothing for flask.
    s.last_trivy_findings = [
        {"cve": "CVE-9999-0001", "package": "django", "installed_version": "1.0"},
    ]

    with patch.object(
        security_tools, "_work_dir", return_value=_pathlib.Path("/fake/does/not/matter")
    ), patch.object(
        security_tools.pathlib.Path, "exists", return_value=True
    ), patch.object(
        security_tools.os, "walk", return_value=[("/fake/does/not/matter", [], ["requirements.txt"])]
    ), patch.object(
        security_tools.pathlib.Path, "read_text", return_value="flask==0.12.2\n"
    ):
        result_json = await security_tools.generate_sbom.ainvoke({})

    result = json.loads(result_json)
    assert result["vulnerability_data"] == "trivy"
    flask_component = next(c for c in result["components"] if c["name"] == "flask")
    # A REAL zero (scanned, no match) -- distinct from None (never scanned).
    assert flask_component["vulnerabilities"] == 0

    clear_session(session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trivy_output, expected_cache",
    [
        pytest.param(
            json.dumps(
                {
                    "status": "ok",
                    "findings": [
                        {"cve": "CVE-2018-1000656", "package": "flask", "installed_version": "0.12.2"}
                    ],
                }
            ),
            [{"cve": "CVE-2018-1000656", "package": "flask", "installed_version": "0.12.2"}],
            id="ok-caches-findings",
        ),
        pytest.param(
            json.dumps({"status": "unavailable", "message": "trivy not installed"}),
            None,
            id="unavailable-leaves-cache-untouched",
        ),
        pytest.param(
            "ERROR: trivy blew up and this is not JSON",
            None,
            id="non-json-leaves-cache-untouched",
        ),
    ],
)
async def test_scan_dependencies_caches_findings_only_on_a_successful_scan(
    trivy_output, expected_cache
):
    from agents_orchestrator.security_agent.config.session_state import get_session, clear_session
    from agents_orchestrator.security_agent.tools import security_tools
    from config.ws_helper import set_session_id

    session_id = "scan-deps-cache-test"
    clear_session(session_id)
    set_session_id(session_id)
    s = get_session(session_id)
    assert s.last_trivy_findings is None  # default sentinel: not scanned yet

    # Patch the whole tool object (a pydantic StructuredTool won't accept a patched
    # attribute), so no real Trivy binary is needed.
    fake_trivy_tool = MagicMock()
    fake_trivy_tool.invoke.return_value = trivy_output

    with patch.object(
        security_tools, "_work_dir", return_value=_pathlib.Path("/fake/does/not/matter")
    ), patch.object(
        security_tools.pathlib.Path, "exists", return_value=True
    ), patch.object(
        security_tools, "run_trivy_scan", fake_trivy_tool
    ):
        result = await security_tools.scan_dependencies.ainvoke({})

    fake_trivy_tool.invoke.assert_called_once_with(
        {"target_path": str(_pathlib.Path("/fake/does/not/matter"))}
    )
    assert result == trivy_output  # raw scanner output is returned verbatim
    assert get_session(session_id).last_trivy_findings == expected_cache

    clear_session(session_id)
