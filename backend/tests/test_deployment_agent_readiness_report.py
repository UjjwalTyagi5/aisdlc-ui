"""The Deployment agent files its release assessment like Security and Code Review: the
Deployment Readiness Report, a Word draft with a page view the chat can raise for approval.

WHAT IT DID. The assessment lived only in the chat session's memory — lost on a backend
restart, never approvable, never read by anybody who was not in the conversation. The
chat handler the page actually uses dropped the model picker's offering, wrote no
transcript, streamed the model's narration, and left the drawer on "Agent is working"
when the model failed.
"""
from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import litellm
import pytest
from langchain_core.messages import AIMessageChunk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent import deployment_standalone_api as api  # noqa: E402
from agents_orchestrator.deployment_agent.config.session_state import clear_session, get_session  # noqa: E402


def _release(**over) -> dict:
    base = {
        "context": {"repo_name": "QuickLink", "ado_project": "QuickLink", "mode": "branch",
                    "source_branch": "main", "pr_id": None, "head_sha": "082f91e49bf0", "environment": "staging",
                    "deploy_via": "azure_pipelines"},
        "summary": "## Posture\nNode 20 service with SQLite.",
        "readiness": "conditional", "risk_score": "high", "risk_rationale": "tar 6.2.1 via sqlite3 is unresolved.",
        "gate_summary": [
            {"name": "Security review", "status": "pass", "note": "acceptable risk"},
            {"name": "Quality gate", "status": "unknown", "note": "SonarQube not connected"},
        ],
        "generated_files": [
            {"path": "Dockerfile", "language": "dockerfile", "contents": "FROM node:20\nCOPY . .\n"},
            {"path": "azure-pipelines.yml", "language": "yaml", "contents": "trigger: [main]\n"},
        ],
        "deploy_runbook": "# Deploy\n1. Merge the PR.", "rollback_runbook": "Redeploy the previous image tag.",
        "iac_findings": [{"file": "Dockerfile", "severity": "medium", "rule": "DS002", "description": "runs as root | no USER", "remediation": "Add USER node"}],
        "compliance_evidence": {"captured_at": "", "gate_approvals": ["BRD approved"], "test_summary": "", "security_summary": "Pass, 2 findings", "sbom_present": True, "notes": ""},
        "release_decision": "conditional", "release_justification": "Go once the quality gate is read.",
        "pr_url": None, "pr_title": None, "status": "assessed",
    }
    base.update(over)
    return base


def test_the_report_carries_the_decision_gates_package_and_runbooks():
    from agents_orchestrator.deployment_agent.readiness_report import readiness_markdown

    md = readiness_markdown(_release())
    heads = [line[3:] for line in md.splitlines() if line.startswith("## ")]
    assert heads == ["Summary", "Release decision", "Readiness and risk", "Release gates", "Deployment package",
                     "Deploy runbook", "Rollback runbook", "Infrastructure-as-code findings", "Compliance evidence",
                     "Scope and method"]
    assert "**Conditional go**" in md and "Go once the quality gate is read." in md
    # An unread gate is Unknown, and said to be — never counted as passed.
    assert "| Quality gate | Unknown | SonarQube not connected |" in md
    assert "| Dockerfile | dockerfile | 2 |" in md and "not yet opened" in md
    # The agent's own headings are demoted, and a pipe in a cell cannot break a table.
    assert "### Posture" in md and "### Deploy" in md and "runs as root / no USER" in md
    assert "Nothing was deployed" in md


def test_the_word_report_is_written_with_its_page_copy_and_never_overwrites(tmp_path):
    from agents_orchestrator.deployment_agent.readiness_report import write_readiness_report

    first, first_md = write_readiness_report(_release(), str(tmp_path))
    second, _ = write_readiness_report(_release(), str(tmp_path))
    assert Path(first).name == "QuickLink_Deployment_Readiness_staging_main_082f91e.docx"
    assert Path(second).name == "QuickLink_Deployment_Readiness_staging_main_082f91e_v2.docx"
    assert Path(first).stat().st_size > 0 and Path(first_md).read_text(encoding="utf-8").startswith("## Summary")


class _Manager:
    def __init__(self) -> None:
        self.frames: List[Dict[str, Any]] = []

    async def broadcast(self, payload):
        self.frames.append(payload)

    async def send_personal_message(self, message, _ws):
        self.frames.append(json.loads(message))

    async def send_agent_response(self, agent_name, message, session_id):
        self.frames.append({"type": "agent_response", "agent_name": agent_name, "message": message})

    def types(self):
        return [f.get("type") for f in self.frames]


@pytest.fixture
def files_root(tmp_path, monkeypatch):
    from config import sdlcSettings

    monkeypatch.setattr(sdlcSettings(), "FILES", str(tmp_path), raising=False)
    return tmp_path


async def test_a_submitted_assessment_is_filed_once_as_a_draft_report(files_root, monkeypatch):
    mgr = _Manager()
    monkeypatch.setattr(api, "manager", mgr)
    s = get_session("dep-file-test")
    s.last_artifact = _release()
    register = AsyncMock(return_value="art-9")
    try:
        with patch("shared.services.chat_artifacts.register_generated_file", register):
            await api._file_readiness_report("dep-file-test", "u1")
            await api._file_readiness_report("dep-file-test", "u1")  # a later turn about the same report
        doc = s.last_artifact["document"]
        assert doc["artifact_id"] == "art-9" and doc["filename"].endswith(".docx")
        register.assert_awaited_once()
        assert register.await_args.kwargs["stage"] == "deployment"
        [frame] = [f for f in mgr.frames if f.get("type") == "file_generated"]
        assert frame["artifact_id"] == "art-9"
    finally:
        clear_session("dep-file-test")


async def test_a_report_that_could_not_be_recorded_says_so_on_the_assessment(files_root, monkeypatch):
    monkeypatch.setattr(api, "manager", _Manager())
    s = get_session("dep-file-none")
    s.last_artifact = _release()
    try:
        with patch("shared.services.chat_artifacts.register_generated_file", AsyncMock(return_value=None)):
            await api._file_readiness_report("dep-file-none", "u1")
        assert "could not be recorded" in s.last_artifact["document"]["error"]
    finally:
        clear_session("dep-file-none")


def test_the_agent_can_raise_its_report():
    from agents_orchestrator.deployment_agent.agents import deployer
    from agents_orchestrator.deployment_agent.prompts.deploy_prompt import DEPLOY_SYSTEM_PROMPT

    assert "raise_document_for_approval" in {t.name for t in deployer._tools}
    assert "Deployment Readiness Report" in DEPLOY_SYSTEM_PROMPT and "DRAFT" in DEPLOY_SYSTEM_PROMPT


async def test_the_graph_resolves_the_model_for_the_turns_project(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    from agents_orchestrator.deployment_agent.agents import deployer
    from shared.services import model_resolver

    calls = []

    async def fake_resolve(tenant_id, model_id, **kw):
        calls.append(kw)
        return "resolved"

    class _Model:
        async def ainvoke(self, _m):
            return AIMessage(content="ok")

    model_resolver.set_resolved_model(None)
    monkeypatch.setattr(model_resolver, "resolve_model_for_run", fake_resolve)
    monkeypatch.setattr(deployer, "_resolve_model", lambda _s: _Model())
    await deployer.agent_node({"messages": [HumanMessage(content="hi")], "tenant_id": "t1", "project_id": "p1",
                               "model_id": None, "offering_id": "off-grok"})
    model_resolver.set_resolved_model(None)
    assert calls == [{"offering_id": "off-grok", "project_id": "p1"}]


# ── the turn ──────────────────────────────────────────────────────────────────


def _auth_error():
    return litellm.exceptions.AuthenticationError(
        message="AzureException AuthenticationError - invalid subscription key", llm_provider="azure", model="gpt-5-mini",
    )


@pytest.fixture
def turn(monkeypatch):
    mgr = _Manager()
    saved, states, order = [], [], []
    monkeypatch.setattr(api, "manager", mgr)

    @asynccontextmanager
    async def _db(_tenant):
        yield None

    async def _persist(session_id, role, content, **kwargs):
        saved.append((role, content))

    async def _file(session_id, user_id):
        order.append(("filed", len(mgr.frames)))

    monkeypatch.setattr(api, "get_db_session_for_tenant", _db)
    monkeypatch.setattr(api, "assert_agent_access_for_chat", AsyncMock(return_value="p1"))
    monkeypatch.setattr(api, "get_prepared", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "agent_trace", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(api, "_load_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "resolve_agent_turn", AsyncMock(return_value=("prompt", [])))
    monkeypatch.setattr(api, "_filed_documents_note", AsyncMock(return_value="\nFILED: QuickLink_Deployment_Readiness.docx (draft)\n"))
    monkeypatch.setattr(api, "_file_readiness_report", _file)
    monkeypatch.setattr(api, "persist_turn", _persist)

    async def _run(chunks=(), boom=None):
        class _App:
            @staticmethod
            async def astream(state, **_kwargs):
                states.append(state)
                for text in chunks:
                    yield (AIMessageChunk(content=text, id="m1"), {})
                if boom is not None:
                    raise boom

        monkeypatch.setattr(api, "deploy_app", _App)
        clear_session("dp1")
        await api._process_ws_message(
            {"type": "user_message_with_files", "session_id": "dp1", "project_id": "p1",
             "task_intent": "assess it", "offering_id": "off-grok"},
            websocket=object(), user_id="u1", tenant_id="t1",
        )
        return mgr, saved, states, order

    yield _run
    clear_session("dp1")


def _failed(frames):
    return any(f.get("type") == "agent_completed" and f.get("success") is False for f in frames)


def _complete(frames):
    return [f["activity"]["message"] for f in frames
            if f.get("type") == "activity_update" and f.get("activity", {}).get("type") == "complete"]


async def test_the_pages_model_project_and_filed_report_reach_the_agent(turn):
    _mgr, _saved, states, _order = await turn(chunks=["Assessed."])
    assert states[0]["offering_id"] == "off-grok" and states[0]["project_id"] == "p1"
    assert "FILED: QuickLink_Deployment_Readiness.docx" in states[0]["messages"][0].content


async def test_the_report_is_filed_before_stream_end_and_the_turn_is_transcribed(turn):
    mgr, saved, _states, order = await turn(chunks=["Assessed."])
    [(_, frames_before)] = order
    assert "stream_end" not in [f.get("type") for f in mgr.frames[:frames_before]]
    assert saved == [("user", "assess it"), ("agent", "Assessed.")]
    assert not _failed(mgr.frames) and _complete(mgr.frames) == ["Deployment assessment complete"]


async def test_a_model_failure_ends_the_run_as_failed_with_who_fixes_it(turn):
    mgr, saved, _states, _order = await turn(chunks=["Let me look…"], boom=_auth_error())
    assert _failed(mgr.frames) and "stream_end" in mgr.types()
    assert _complete(mgr.frames) == ["Deployment failed"]
    [said] = [f["message"] for f in mgr.frames if f.get("type") == "agent_response"]
    assert "rejected the configured credential" in said and "subscription key" not in said
    assert saved[-1][0] == "agent" and "rejected the configured credential" in saved[-1][1]


def test_a_runbook_that_points_at_a_staged_file_is_that_file():
    """LIVE: the runbook fields read "See deploy/deploy-runbook.md" and the report carried no runbook."""
    from agents_orchestrator.deployment_agent.readiness_report import readiness_markdown

    md = readiness_markdown(_release(
        deploy_runbook="See deploy/deploy-runbook.md",
        generated_files=[{"path": "deploy/deploy-runbook.md", "language": "markdown",
                          "contents": "# Deploy Runbook\n## Pre-requisites\n- Service connection"}],
    ))
    section = md.split("## Deploy runbook", 1)[1].split("## Rollback runbook", 1)[0]
    assert "### Pre-requisites" in section and "From the staged file `deploy/deploy-runbook.md`" in section
    assert "See deploy/deploy-runbook.md" not in section


# ── upstream evidence: about this repository, and readable whole ────────────


_BIG_SECURITY = {
    "context": {"repo_name": "QuickLink", "branch": "main", "head_sha": "082f91e"},
    "signoff": {"decision": "pass", "rationale": "build-time only"}, "risk_score": "low",
    "metrics": {"critical": 1, "high": 0, "medium": 0, "low": 1, "total": 2},
    "summary": "tar via sqlite3",
    "findings": [{"id": "S-001", "severity": "critical", "category": "sca", "title": "tar CVEs", "package": "tar@6.2.1",
                  "triage": "acceptable_risk", "reachability": "unreachable", "description": "x" * 2000}],
    "sbom": [{"name": f"pkg{i}", "version": "1.0.0", "license": "MIT", "vulnerabilities": 0} for i in range(475)],
    "scan": {
        "scanners": [{"name": "Trivy", "purpose": "deps", "status": "ok", "findings": 13, "seconds": 0.2, "message": ""}],
        "vulnerabilities": [{"id": f"CVE-{i}", "severity": "high" if i < 9 else "medium", "package": "tar", "installed": "6.2.1"} for i in range(12)],
        "sbom": {"components": [{"name": f"pkg{i}"} for i in range(475)]},
        "totals": {"vulnerabilities": 13, "vulnerabilities_high": 9},
    },
    "document": {"filename": "QuickLink_Security_Review.docx", "url": "http://x", "artifact_id": "a1"},
}


def test_a_security_result_is_compacted_to_its_verdict_findings_and_totals():
    from shared.services.upstream_results import compact

    out = compact("security", _BIG_SECURITY)
    text = json.dumps(out)
    assert len(text) < 4000
    assert out["metrics"]["critical"] == 1 and out["findings"][0]["package"] == "tar@6.2.1"
    assert out["scan"]["totals"] == {"vulnerabilities": 13, "vulnerabilities_high": 9}
    assert out["scan"]["vulnerable_packages"] == [{"package": "tar", "installed": "6.2.1", "worst": "high", "count": 12, "high_or_critical": 9}]
    assert out["sbom_components"] == 475 and "never as a clean scan" in out["note"]


async def test_the_deployment_gate_evidence_is_this_repositorys_and_compact(monkeypatch):
    from agents_orchestrator.deployment_agent.tools import deploy_tools
    from config.ws_helper import set_session_id
    from shared.services import artifact_consumption
    from shared.services.artifact_versions import UpstreamRead

    set_session_id("dep-upstream")
    s = get_session("dep-upstream")
    s.tenant_id, s.project_id, s.repo_name = "t1", "11111111-1111-1111-1111-111111111111", "QuickLink"
    payloads = {"security": _BIG_SECURITY, "testing": {"context": {"repo_name": "Company"}, "summary": "all green"}}

    async def fake_read(*, stage, **_kw):
        return UpstreamRead(stage=stage, payload=payloads[stage], unenforced=True)

    monkeypatch.setattr(artifact_consumption, "read_upstream_for_agent", fake_read)
    try:
        out = json.loads(await deploy_tools.read_upstream_artifacts.ainvoke({}))
    finally:
        clear_session("dep-upstream")
    assert out["security"]["metrics"]["critical"] == 1 and "sbom" not in out["security"]
    assert out["testing"] is None and "repository 'Company'" in out["testing_status"]
