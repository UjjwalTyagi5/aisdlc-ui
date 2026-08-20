"""Proves the Security agent's actual tool loop end to end, without a live LLM key.

Same technique as tests/test_code_review_agent_live_e2e.py: no LLM provider is
configured and the .env ANTHROPIC_API_KEY fallback is dead (confirmed via a direct API
call -- 401), so only the model's own judgment can't be proven right now. Everything
else can: the graph's routing, and all four real scanners (Trivy/SCA, Semgrep/SAST,
Gitleaks/secrets, and the manifest-based SBOM builder) running for real against a fixture
directory with genuine, known issues, plus submit_security_review's real parsing and
persistence.

Fixture generated into a fresh OS temp dir at test time (not committed) -- Semgrep's
default ignore patterns silently skip anything path-matching test/fixtures (found while
verifying the Code Review agent earlier this session), so a committed fixture under
backend/tests/ would silently under-report.

Security's agent_node (agents/scanner.py) now resolves its model via _resolve_model
(added in this pass, mirroring Code Review's) -- mocked the same way Code Review's own
live_e2e test mocks it: patch.object(scanner, "_resolve_model", return_value=<a fake
model with a scripted .ainvoke()>).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.asyncio

_VULNERABLE_SOURCE = '''\
import subprocess

def run_cmd(user_input):
    subprocess.run(user_input, shell=True)
'''

_SECRET_SOURCE = '''\
GITHUB_TOKEN = "ghp_wF8yT3mK9pL2qR7vN4xB6cH1jD5sA0eG8kU3rY9tZ2n"
'''

_REQUIREMENTS_TXT = "flask==0.12.2\n"

_ALL_SCANNERS_INSTALLED = all(
    shutil.which(b) for b in ("semgrep", "gitleaks", "trivy")
)


@pytest.fixture
def scan_target_dir():
    d = tempfile.mkdtemp(prefix="sec_agent_live_e2e_")
    (Path(d) / "vulnerable.py").write_text(_VULNERABLE_SOURCE, encoding="utf-8")
    (Path(d) / "config.py").write_text(_SECRET_SOURCE, encoding="utf-8")
    (Path(d) / "requirements.txt").write_text(_REQUIREMENTS_TXT, encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


class _ScriptedModel:
    """Stands in for the real model. Each .ainvoke() call pops the next canned
    response off the script, so the test controls exactly what the "model" decides
    to do — but the graph, the tool node, and the tools themselves are 100% real
    code, not mocks. Mirrors test_code_review_agent_live_e2e.py's identical helper."""

    def __init__(self, script: list[AIMessage]):
        self._script = list(script)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return self._script.pop(0)


@pytest.mark.skipif(not _ALL_SCANNERS_INSTALLED, reason="semgrep/gitleaks/trivy CLI not all installed")
async def test_the_real_tool_loop_runs_all_four_scanners_and_produces_a_persisted_review(scan_target_dir):
    from agents_orchestrator.security_agent.agents import scanner
    from agents_orchestrator.security_agent.config.session_state import get_session
    from config.ws_helper import set_session_id

    # Unlike Code Review's tools (which take target_path as a direct argument), the
    # Security tools take zero arguments and read the target off session state -- so
    # the session must be bound to the fixture dir BEFORE the graph runs, the same way
    # the real WS/REST handlers bind it from a prepared scan target.
    session_id = "sec-live-e2e-test"
    set_session_id(session_id)
    s = get_session(session_id)
    s.work_dir = scan_target_dir
    s.tenant_id = "test-tenant"

    script = [
        # Turn 1: scan_dependencies alone, split out from the other three tool calls
        # so its last_trivy_findings cache write is guaranteed to land before
        # generate_sbom (turn 2) reads it -- ToolNode may run same-turn tool_calls
        # concurrently, so this ordering can't be left to chance.
        AIMessage(
            content="",
            tool_calls=[{"name": "scan_dependencies", "args": {}, "id": "call_1"}],
        ),
        # Turn 2: the remaining three scanners, including generate_sbom, which now
        # cross-references turn 1's cached Trivy findings.
        AIMessage(
            content="",
            tool_calls=[
                {"name": "scan_code", "args": {}, "id": "call_2"},
                {"name": "scan_secrets", "args": {}, "id": "call_3"},
                {"name": "generate_sbom", "args": {}, "id": "call_4"},
            ],
        ),
        # Turn 3: having seen real findings from all four, the "model" submits the
        # structured review -- exercises submit_security_review's real parsing,
        # Pydantic model construction, and artifact persistence.
        AIMessage(
            content="",
            tool_calls=[{
                "name": "submit_security_review",
                "args": {"review_json": json.dumps({
                    "summary": "Found a shell injection risk, a leaked GitHub token, and a vulnerable flask version.",
                    "risk_score": "critical",
                    "signoff": {"decision": "fail", "rationale": "Critical findings block release."},
                    "findings": [
                        {
                            "id": "F1", "severity": "critical", "category": "sast",
                            "title": "subprocess.run with shell=True", "file": "vulnerable.py", "line": 4,
                            "description": "Shell injection risk.", "remediation": "Use shell=False.",
                        },
                        {
                            "id": "F2", "severity": "critical", "category": "secret",
                            "title": "Leaked GitHub PAT", "file": "config.py", "line": 1,
                            "description": "A GitHub personal access token is hardcoded.",
                            "remediation": "Revoke and move to a secret store.",
                        },
                        {
                            "id": "F3", "severity": "high", "category": "sca",
                            "title": "flask 0.12.2 has a known DoS vulnerability", "cve": "CVE-2018-1000656",
                            "package": "flask", "file": "requirements.txt",
                            "description": "Outdated flask version.", "remediation": "Upgrade to flask>=1.0.",
                        },
                    ],
                    "sbom": [{"name": "flask", "version": "0.12.2"}],
                    "supply_chain": [],
                    "remediation_plan": "Fix the shell injection, revoke the leaked token, upgrade flask.",
                    "suppression_log": [],
                    "compliance_frameworks": ["OWASP Top 10"],
                })},
                "id": "call_5",
            }],
        ),
        # Turn 4: no more tool calls -- route_fn sends this straight to END.
        AIMessage(content="Security scan complete: 3 findings, signoff=fail."),
    ]
    model = _ScriptedModel(script)

    with patch.object(scanner, "_resolve_model", return_value=model):
        result = await scanner.app.ainvoke(
            {
                "messages": [HumanMessage(content="Please scan the prepared target.")],
                "tenant_id": "test-tenant",
                "model_id": None,
                "offering_id": None,
            },
            config={"configurable": {"thread_id": "sec-live-e2e-test"}},
        )

    # The model was actually driven through all 4 scripted turns.
    assert model.calls == 4

    def _tool_result(name: str) -> dict:
        msgs = [m for m in result["messages"] if getattr(m, "name", None) == name]
        assert len(msgs) == 1, f"expected exactly one {name} ToolMessage, got {len(msgs)}"
        return json.loads(msgs[0].content)

    # Real Trivy run found the real flask CVE -- not anything the test scripted into
    # the submit_security_review call above; this is what the scanner ACTUALLY found.
    trivy_out = _tool_result("scan_dependencies")
    assert trivy_out["status"] == "ok"
    assert any(f["cve"] == "CVE-2018-1000656" for f in trivy_out["findings"])

    # Real Semgrep run found the real shell=True pattern.
    semgrep_out = _tool_result("scan_code")
    assert semgrep_out["status"] == "ok"
    assert any("shell-true" in f["rule_id"] for f in semgrep_out["findings"])

    # Real Gitleaks run found the real hardcoded GitHub token.
    gitleaks_out = _tool_result("scan_secrets")
    assert gitleaks_out["status"] == "ok"
    assert any(f["rule_id"] == "github-pat" for f in gitleaks_out["findings"])

    # Real SBOM builder parsed the real requirements.txt.
    sbom_out = _tool_result("generate_sbom")
    flask_component = next(c for c in sbom_out["components"] if c["name"] == "flask" and c["version"] == "0.12.2")
    assert flask_component["vulnerabilities"] >= 1  # cross-referenced from turn 1's real Trivy run

    # And submit_security_review actually ran, producing a real confirmation.
    submit_msgs = [m for m in result["messages"] if getattr(m, "name", None) == "submit_security_review"]
    assert len(submit_msgs) == 1
    assert "risk=critical" in submit_msgs[0].content
    assert "signoff=fail" in submit_msgs[0].content
