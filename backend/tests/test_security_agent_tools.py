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


# ── the scanner tools are slices of ONE shared scan ─────────────────────────


_SCAN = {
    "scanners": [
        {"name": "Gitleaks", "purpose": "secrets", "status": "ok", "findings": 0, "seconds": 0.1, "message": ""},
        {"name": "Semgrep", "purpose": "sast", "status": "error", "findings": None, "seconds": 0.1, "message": "Semgrep exited with code 2"},
        {"name": "Trivy", "purpose": "deps", "status": "ok", "findings": 2, "seconds": 0.1, "message": ""},
    ],
    "secrets": [], "sast": [],
    "vulnerabilities": [
        {"id": "CVE-A", "severity": "critical", "package": "tar", "installed": "6.2.1", "fixed": "7.5.19", "title": "gzip bomb", "manifest": "package.json"},
        {"id": "CVE-B", "severity": "high", "package": "tar", "installed": "6.2.1", "fixed": "7.5.21", "title": "traversal", "manifest": "package.json"},
    ],
    "sbom": {
        "components": [
            {"name": "sqlite3", "version": "5.1.7", "license": "BSD-3-Clause", "direct": True, "via": "", "manifest": "package.json", "vulnerabilities": 0},
            {"name": "tar", "version": "6.2.1", "license": "ISC", "direct": False, "via": "sqlite3", "manifest": "package.json", "vulnerabilities": 2},
        ],
        "manifests": ["package.json"],
        "notes": ["package.json: no lockfile is committed — versions were resolved from the declared ranges at scan time"],
    },
    "totals": {"vulnerabilities": 2},
}


def _bind(session_id: str):
    from agents_orchestrator.security_agent.config.session_state import clear_session, get_session
    from config.ws_helper import set_session_id

    clear_session(session_id)
    set_session_id(session_id)
    return get_session(session_id)


@pytest.mark.asyncio
async def test_the_four_tools_share_one_scan_of_the_target():
    """THE SECURITY AGENT PASSED A BRANCH WITH NINE HIGH CVEs: its own Trivy call saw no
    lockfile and found nothing, while Code Review's scan of the same commit — which
    resolves the declared ranges first — found 13. The tools now answer from that scan,
    run once per target."""
    from agents_orchestrator.security_agent.tools import security_tools

    s = _bind("shared-scan-test")
    scan = MagicMock(return_value=dict(_SCAN))
    with patch.object(security_tools, "_work_dir", return_value=_pathlib.Path("/fake/checkout")), \
         patch.object(security_tools.pathlib.Path, "exists", return_value=True), \
         patch("shared.services.code_security_scan.run_code_security_scan", scan):
        deps = json.loads(await security_tools.scan_dependencies.ainvoke({}))
        sbom = json.loads(await security_tools.generate_sbom.ainvoke({}))
        code = json.loads(await security_tools.scan_code.ainvoke({}))
        secrets = json.loads(await security_tools.scan_secrets.ainvoke({}))

    scan.assert_called_once()
    assert scan.call_args.args[0] == str(_pathlib.Path("/fake/checkout"))
    assert deps["status"] == "ok" and deps["findings_count"] == 2
    assert deps["findings"][0] == {"cve": "CVE-A", "severity": "critical", "package": "tar", "installed_version": "6.2.1",
                                   "fixed_version": "7.5.19", "title": "gzip bomb", "target": "package.json"}
    assert "no lockfile" in deps["notes"][0]
    # The transitive package that carries the CVEs is in the SBOM, with what brings it in.
    assert sbom["vulnerability_data"] == "trivy"
    assert sbom["components"][0] == {"name": "tar", "version": "6.2.1", "license": "ISC", "direct": False,
                                     "via": "sqlite3", "manifest": "package.json", "vulnerabilities": 2}
    assert secrets["status"] == "ok" and secrets["findings_count"] == 0
    # A scanner that did not run says so — never a clean zero.
    assert code["status"] == "error" and code["findings_count"] is None and "exited with code 2" in code["message"]
    assert s.last_trivy_findings is not None and len(s.last_trivy_findings) == 2


@pytest.mark.asyncio
async def test_when_the_vulnerability_scanner_did_not_run_nothing_is_counted():
    from agents_orchestrator.security_agent.tools import security_tools

    s = _bind("trivy-failed-test")
    failed = dict(_SCAN)
    failed["scanners"] = [dict(sc, status="error", findings=None, message="trivy: unavailable") if sc["name"] == "Trivy" else sc for sc in _SCAN["scanners"]]
    with patch.object(security_tools, "_work_dir", return_value=_pathlib.Path("/fake/checkout")), \
         patch.object(security_tools.pathlib.Path, "exists", return_value=True), \
         patch("shared.services.code_security_scan.run_code_security_scan", MagicMock(return_value=failed)):
        deps = json.loads(await security_tools.scan_dependencies.ainvoke({}))
        sbom = json.loads(await security_tools.generate_sbom.ainvoke({}))

    assert deps["status"] == "error" and deps["findings"] == [] and deps["findings_count"] is None
    assert sbom["vulnerability_data"] == "not_scanned"
    assert all(c["vulnerabilities"] is None for c in sbom["components"])
    assert s.last_trivy_findings is None


@pytest.mark.asyncio
async def test_a_new_target_is_scanned_again():
    from agents_orchestrator.security_agent.tools import security_tools

    _bind("rescan-test")
    scan = MagicMock(return_value=dict(_SCAN))
    with patch.object(security_tools.pathlib.Path, "exists", return_value=True), \
         patch("shared.services.code_security_scan.run_code_security_scan", scan):
        with patch.object(security_tools, "_work_dir", return_value=_pathlib.Path("/checkout/a")):
            await security_tools.scan_secrets.ainvoke({})
            await security_tools.scan_secrets.ainvoke({})
        with patch.object(security_tools, "_work_dir", return_value=_pathlib.Path("/checkout/b")):
            await security_tools.scan_secrets.ainvoke({})
    assert scan.call_count == 2


# ── run_trivy_scan: a crash is not a clean scan ──────────────────────────────


def _fake_run(returncode, stdout="", stderr="", seen=None):
    class _Result:
        pass

    def run(args, **kwargs):
        if seen is not None:
            seen.append(args)
        r = _Result()
        r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
        return r
    return run


def test_trivy_fatal_with_no_report_is_an_error_not_zero_findings(monkeypatch, tmp_path):
    """Found on a real legacy repository: Maven Central answered 429 while Trivy resolved a
    pom.xml, Trivy exited 1 with no report, and the tool said "ok, 0 findings"."""
    import json as _json

    import agents_orchestrator.security_agent.tools.trivy_tool as trivy_tool

    monkeypatch.setattr(trivy_tool, "_TRIVY_BIN", "trivy")
    monkeypatch.setattr(trivy_tool.subprocess, "run", _fake_run(
        1, stdout="", stderr="INFO scanning\nFATAL Error remote Maven repository returned 429 Too Many Requests"))
    out = _json.loads(trivy_tool.run_trivy_scan.invoke({"target_path": str(tmp_path)}))
    assert out["status"] == "error" and "429" in out["message"] and out["findings"] == []


def test_trivy_offline_adds_offline_scan_and_default_does_not(monkeypatch, tmp_path):
    import json as _json

    import agents_orchestrator.security_agent.tools.trivy_tool as trivy_tool

    seen: list = []
    monkeypatch.setattr(trivy_tool, "_TRIVY_BIN", "trivy")
    monkeypatch.setattr(trivy_tool.subprocess, "run", _fake_run(0, stdout='{"Results": []}', seen=seen))
    assert _json.loads(trivy_tool.run_trivy_scan.invoke({"target_path": str(tmp_path), "offline": True}))["status"] == "ok"
    assert _json.loads(trivy_tool.run_trivy_scan.invoke({"target_path": str(tmp_path)}))["status"] == "ok"
    assert "--offline-scan" in seen[0] and "--offline-scan" not in seen[1]
