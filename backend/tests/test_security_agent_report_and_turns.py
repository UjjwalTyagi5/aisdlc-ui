"""The Security agent gets what Code Review got: a filed report, honest turns, approval.

- the scan is filed as a Security Review Report (Word + page copy), a DRAFT in Documents,
  and the saved scan links it;
- a request about the report is not a scan: the submit nudge fires only on a scan turn;
- a failed turn ends the run as failed with a reason a person can act on;
- every turn is written to the transcript; the raise-for-approval tool is bound.
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
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.graph import END

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.security_agent import security_agent_api as api  # noqa: E402
from agents_orchestrator.security_agent.config.session_state import clear_session  # noqa: E402

pytestmark = pytest.mark.unit


def _artifact(**over):
    art = {
        "context": {"repo_name": "QuickLink", "ado_project": "QuickLink", "mode": "branch",
                    "branch": "feature/116-117-link-management", "head_sha": "082f91e49bf0"},
        "summary": "## Overview\nA small Express service with one open redirect.",
        "risk_score": "high",
        "signoff": {"decision": "fail", "rationale": "One high finding must be fixed."},
        "findings": [
            {"id": "S-002", "severity": "medium", "category": "dependency", "title": "tar 6.2.1 vulnerable",
             "package": "tar", "cve": "CVE-2026-59873", "description": "gzip bomb", "remediation": "Upgrade sqlite3."},
            {"id": "S-001", "severity": "high", "category": "injection", "title": "Open redirect",
             "file": "src/routes/ui.js", "line": 19, "description": "destination is unvalidated.",
             "remediation": "Allow http(s) only.", "compliance": ["OWASP A01:2021"]},
        ],
        "sbom": [{"name": "express", "version": "4.22.3", "license": "MIT", "vulnerabilities": 0},
                 {"name": "tar", "version": "6.2.1", "license": "ISC", "vulnerabilities": 12}],
        "supply_chain": [{"package": "tar", "risk": "high", "note": "brought in by sqlite3"}],
        "remediation_plan": "1. Validate destination.\n2. Upgrade sqlite3.",
        "suppression_log": [],
        "compliance_frameworks": ["OWASP Top 10"],
        "metrics": {"critical": 0, "high": 1, "medium": 1, "low": 0, "total": 2},
    }
    art.update(over)
    return art


# ── the report ────────────────────────────────────────────────────────────────


def test_the_report_is_built_from_the_scan_with_single_block_tables():
    from agents_orchestrator.security_agent.review_document import security_markdown

    md = security_markdown(_artifact())
    sections = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert sections == ["## Summary", "## Sign-off", "## Findings", "## Remediation plan", "## Supply chain",
                        "## Software bill of materials", "## Scope and method"]
    assert "**Fail** — Blocking security findings must be fixed" in md
    findings = md.split("## Findings", 1)[1].split("### Finding detail", 1)[0].strip()
    table = findings[findings.index("| ID | Severity"):]
    rows = table.splitlines()
    assert "S-001" in rows[2] and "S-002" in rows[3], "high before medium"
    assert "\n\n|" not in table
    assert "| S-001 | High | injection | Open redirect | src/routes/ui.js:19 |" in md
    assert "| S-002 | Medium | dependency | tar 6.2.1 vulnerable | tar (CVE-2026-59873) |" in md
    assert "### Overview" in md, "the agent's headings are demoted, not numbered sections"


def test_the_scanners_results_are_on_the_report_unedited_apart_from_the_triage():
    """Live: the reviewer judged every CVE unreachable and the report showed nothing of
    what the scanners found. Their results are their own section now."""
    from agents_orchestrator.security_agent.review_document import security_markdown

    scan = {
        "scanners": [
            {"name": "Gitleaks", "purpose": "Hardcoded secrets", "status": "ok", "findings": 0, "seconds": 0.3, "message": ""},
            {"name": "Semgrep", "purpose": "Static analysis", "status": "error", "findings": None, "seconds": 1, "message": "Semgrep exited with code 2"},
            {"name": "Trivy", "purpose": "Known vulnerabilities", "status": "ok", "findings": 2, "seconds": 0.2, "message": ""},
        ],
        "secrets": [], "sast": [],
        "vulnerabilities": [
            {"id": "CVE-A", "severity": "critical", "package": "tar", "installed": "6.2.1", "fixed": "7.5.19", "title": "gzip bomb"},
            {"id": "CVE-B", "severity": "high", "package": "tar", "installed": "6.2.1", "fixed": "7.5.21", "title": "traversal"},
        ],
        "sbom": {"components": [
            {"name": "sqlite3", "declared": "^5.1.7", "version": "5.1.7", "license": "BSD-3-Clause", "direct": True, "via": "", "vulnerabilities": 0},
            {"name": "tar", "version": "6.2.1", "license": "ISC", "direct": False, "via": "sqlite3", "vulnerabilities": 2},
        ], "notes": ["package.json: no lockfile is committed — versions were resolved from the declared ranges at scan time"]},
        "totals": {"vulnerabilities": 2, "vulnerabilities_high": 2, "secrets": 0, "sast": 0, "components": 2},
    }
    md = security_markdown(_artifact(findings=[], metrics={"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0},
                                     signoff={"decision": "pass", "rationale": "unreachable"}, scan=scan))
    assert "## Scanner results" in md and md.index("## Scanner results") < md.index("## Findings")
    assert "2 known vulnerabilities (2 high or critical)" in md
    assert "| Trivy | Known vulnerabilities | Failed | 2 | Ran in 0.2 s |" in md
    assert "| Semgrep | Static analysis | Blocked | not run | Semgrep exited with code 2 |" in md
    assert "**Not established:** Semgrep did not run" in md
    assert "| tar | 6.2.1 | Critical | 2 (1 critical, 1 high) | CVE-A, CVE-B | sqlite3 |" in md
    assert "The reviewer recorded no findings; the scanner results above stand on their own." in md
    assert "> package.json: no lockfile is committed" in md
    assert "| sqlite3 | ^5.1.7 | 5.1.7 | BSD-3-Clause | 0 |" in md


def test_the_word_report_is_written_with_its_page_copy(tmp_path):
    from agents_orchestrator.security_agent.review_document import write_security_report

    docx, md = write_security_report(_artifact(), str(tmp_path))
    assert docx.endswith("QuickLink_Security_Review_feature_116_117_link_management_082f91e.docx")
    assert Path(docx).stat().st_size > 10_000 and Path(md).read_text(encoding="utf-8").startswith("## Summary")
    docx2, _ = write_security_report(_artifact(), str(tmp_path))
    assert docx2.endswith("_v2.docx"), "a second report for the same target keeps both"


# ── the graph: the nudge only on a scan turn ─────────────────────────────────


def _prose() -> AIMessage:
    return AIMessage(content="## Findings\n- S-001 open redirect …")


def test_a_request_about_the_report_is_not_nudged_into_a_new_scan():
    from agents_orchestrator.security_agent.agents.scanner import route_fn

    state = {"messages": [
        HumanMessage(content="Send the security report for approval."),
        AIMessage(content="", tool_calls=[{"name": "raise_document_for_approval", "args": {"filename": "r.docx"}, "id": "9"}]),
        ToolMessage(content="Raised 'r.docx' for approval.", tool_call_id="9"),
        AIMessage(content="Raised. The approver decides in Requests & Approvals."),
    ]}
    assert route_fn(state) == END
    assert route_fn({"messages": [HumanMessage(content="Hi"), AIMessage(content="Hello!")]}) == END


def test_a_scan_turn_without_a_submission_is_nudged_once():
    from agents_orchestrator.security_agent.agents.scanner import _SUBMIT_NUDGE, route_fn

    for asked in ("Please run the security scan and submit your review.", "scan this branch", "re-scan the PR"):
        assert route_fn({"messages": [HumanMessage(content=asked), _prose()]}) == "finalize", asked
    ran = {"messages": [
        HumanMessage(content="have a look please"),
        AIMessage(content="", tool_calls=[{"name": "scan_secrets", "args": {}, "id": "1"}]),
        ToolMessage(content="0 secrets", tool_call_id="1"),
        _prose(),
    ]}
    assert route_fn(ran) == "finalize"
    nudged = {"messages": ran["messages"] + [HumanMessage(content=_SUBMIT_NUDGE), _prose()]}
    assert route_fn(nudged) == END, "one nudge per turn"


def test_the_agent_can_raise_its_report_for_approval():
    from agents_orchestrator.security_agent.agents import scanner
    from agents_orchestrator.security_agent.prompts.security_prompt import SECURITY_SYSTEM_PROMPT

    assert "raise_document_for_approval" in {t.name for t in scanner._tools}
    assert "DRAFT" in SECURITY_SYSTEM_PROMPT and "raise_document_for_approval" in SECURITY_SYSTEM_PROMPT


# ── the turn ──────────────────────────────────────────────────────────────────


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


def _auth_error():
    return litellm.exceptions.AuthenticationError(
        message="AzureException AuthenticationError - invalid subscription key", llm_provider="azure", model="gpt-5-mini",
    )


@pytest.fixture
def turn(monkeypatch):
    mgr = _Manager()
    saved = []
    monkeypatch.setattr(api, "manager", mgr)

    @asynccontextmanager
    async def _db(_tenant):
        yield None

    async def _persist(session_id, role, content, **kwargs):
        saved.append((role, content))

    monkeypatch.setattr(api, "get_db_session_for_tenant", _db)
    monkeypatch.setattr(api, "assert_agent_access_for_chat", AsyncMock(return_value="p1"))
    monkeypatch.setattr(api, "get_prepared", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "agent_trace", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(api, "_load_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "resolve_agent_turn", AsyncMock(return_value=("prompt", [])))
    monkeypatch.setattr(api, "_persist_scan_to_run", AsyncMock())
    monkeypatch.setattr(api, "persist_turn", _persist)

    async def _run(chunks=(), boom=None):
        class _App:
            @staticmethod
            async def astream(_state, **_kwargs):
                for text in chunks:
                    yield (AIMessageChunk(content=text, id="m1"), {})
                if boom is not None:
                    raise boom

        monkeypatch.setattr(api, "scan_app", _App)
        clear_session("s1")
        await api._process_ws_message(
            {"type": "user_message_with_files", "session_id": "s1", "project_id": "p1", "task_intent": "scan it"},
            websocket=object(), user_id="u1", tenant_id="t1",
        )
        return mgr, saved

    yield _run
    clear_session("s1")


def _failed(frames):
    return any(f.get("type") == "agent_completed" and f.get("success") is False for f in frames)


def _complete(frames):
    return [f["activity"]["message"] for f in frames
            if f.get("type") == "activity_update" and f.get("activity", {}).get("type") == "complete"]


async def test_a_model_failure_ends_the_run_as_failed_with_who_fixes_it(turn):
    mgr, saved = await turn(boom=_auth_error())
    assert _failed(mgr.frames) and "stream_end" in mgr.types()
    assert _complete(mgr.frames) == ["Scan failed"]
    said = [f["message"] for f in mgr.frames if f.get("type") == "agent_response"]
    assert "rejected the configured credential" in said[0] and "subscription key" not in said[0]
    assert saved[0] == ("user", "scan it") and "rejected the configured credential" in saved[-1][1]


async def test_a_successful_turn_saves_before_stream_end_and_writes_the_transcript(turn):
    mgr, saved = await turn(chunks=["Scan submitted."])
    assert not _failed(mgr.frames) and _complete(mgr.frames) == ["Scan complete"]
    api._persist_scan_to_run.assert_awaited_once()
    assert saved == [("user", "scan it"), ("agent", "Scan submitted.")]


async def test_the_saved_scan_links_its_filed_report(tmp_path, monkeypatch):
    from config.ws_helper import set_user_id

    set_user_id("user-1")
    monkeypatch.setattr("config.sdlcSettings", lambda: type("S", (), {"FILES": str(tmp_path)})())
    registered = AsyncMock(return_value="art-7")
    broadcast = AsyncMock()
    with patch("shared.services.chat_artifacts.register_generated_file", registered), \
         patch.object(api.manager, "broadcast", broadcast):
        doc = await api._write_security_document("sess-1", _artifact())
    assert doc["artifact_id"] == "art-7" and doc["filename"].endswith(".docx")
    assert "/generated/user-1/security/sess-1/output/" in doc["url"]
    assert registered.await_args.kwargs["stage"] == "security"
    assert broadcast.await_args.args[0]["type"] == "file_generated"
    assert broadcast.await_args.args[0]["artifact_id"] == "art-7"


# ── submit: the scan is the scanners', and it gates the sign-off ─────────────


_SCAN = {
    "scanners": [
        {"name": "Gitleaks", "purpose": "secrets", "status": "ok", "findings": 0, "seconds": 0.1, "message": ""},
        {"name": "Semgrep", "purpose": "sast", "status": "ok", "findings": 0, "seconds": 1.0, "message": ""},
        {"name": "Trivy", "purpose": "deps", "status": "ok", "findings": 2, "seconds": 0.2, "message": ""},
    ],
    "secrets": [], "sast": [],
    "vulnerabilities": [
        {"id": "CVE-A", "severity": "critical", "package": "tar", "installed": "6.2.1", "fixed": "7.5.19", "title": "gzip bomb", "manifest": "package.json"},
        {"id": "CVE-B", "severity": "high", "package": "tar", "installed": "6.2.1", "fixed": "7.5.21", "title": "traversal", "manifest": "package.json"},
    ],
    "sbom": {"components": [
        {"name": "sqlite3", "version": "5.1.7", "license": "BSD-3-Clause", "direct": True, "via": "", "vulnerabilities": 0},
        {"name": "tar", "version": "6.2.1", "license": "ISC", "direct": False, "via": "sqlite3", "vulnerabilities": 2},
    ], "manifests": ["package.json"], "notes": []},
    "totals": {"vulnerabilities": 2, "vulnerabilities_high": 2, "secrets": 0, "sast": 0, "components": 2},
    "_work_dir": "C:/checkout",
}


@pytest.fixture
def scan_session():
    from agents_orchestrator.security_agent.config.session_state import get_session
    from config.ws_helper import set_session_id

    set_session_id("sec-submit-test")
    s = get_session("sec-submit-test")
    s.repo_name, s.branch, s.head_sha, s.mode = "QuickLink", "feature/x", "082f91e", "branch"
    s.security_scan = dict(_SCAN)
    s.last_artifact = None
    yield s
    clear_session("sec-submit-test")


async def test_a_pass_that_hides_high_cves_is_refused(scan_session):
    """Live: "pass, risk none, 0 findings" on a branch whose scan had 9 high/critical CVEs."""
    from agents_orchestrator.security_agent.tools.security_tools import submit_security_review

    out = await submit_security_review.ainvoke({"review_json": json.dumps({
        "summary": "Clean.", "risk_score": "none", "signoff": {"decision": "pass", "rationale": "nothing"}, "findings": [],
    })})
    assert out.startswith("ERROR") and "tar 6.2.1" in out and "suppression_log" in out
    assert scan_session.last_artifact is None


async def test_a_triaged_finding_or_a_reasoned_suppression_lets_it_through(scan_session):
    from agents_orchestrator.security_agent.tools.security_tools import submit_security_review

    out = await submit_security_review.ainvoke({"review_json": json.dumps({
        "summary": "One transitive package.", "risk_score": "low",
        "signoff": {"decision": "conditional", "rationale": "Build-time only."},
        "findings": [{"id": "S-001", "severity": "critical", "category": "sca", "title": "tar 6.2.1 via sqlite3",
                      "package": "tar", "cve": "CVE-A, CVE-B", "reachability": "unreachable", "triage": "acceptable_risk",
                      "description": "Only used at install time.", "remediation": "Commit a lockfile; upgrade sqlite3."}],
    })})
    assert "Security review submitted" in out
    art = scan_session.last_artifact
    # The scanners' results ride along unedited, and the SBOM is theirs, not the model's.
    assert art["scan"]["vulnerabilities"] == _SCAN["vulnerabilities"] and "_work_dir" not in art["scan"]
    assert [c["name"] for c in art["sbom"]] == ["sqlite3", "tar"] and art["sbom"][1]["vulnerabilities"] == 2

    scan_session.last_artifact = None
    out = await submit_security_review.ainvoke({"review_json": json.dumps({
        "summary": "Suppressed.", "risk_score": "none", "signoff": {"decision": "pass", "rationale": "x"}, "findings": [],
        "suppression_log": [{"finding_id": "tar", "reason": "build-time dependency, not shipped"}],
    })})
    assert "Security review submitted" in out


async def test_without_the_scan_nothing_can_be_submitted(scan_session):
    from agents_orchestrator.security_agent.tools.security_tools import submit_security_review

    scan_session.security_scan = None
    out = await submit_security_review.ainvoke({"review_json": json.dumps({"summary": "x", "signoff": {"decision": "pass"}})})
    assert out.startswith("ERROR") and "scan_dependencies" in out


def _review(findings=(), suppression_log=()):
    return json.dumps({
        "summary": "Transitive tar via sqlite3.", "risk_score": "low",
        "signoff": {"decision": "conditional", "rationale": "Build-time only."},
        "findings": list(findings), "suppression_log": list(suppression_log),
    })


_TAR_AS_THE_MODEL_WROTE_IT = {
    "id": "SCA-001", "severity": "critical", "category": "sca", "title": "tar: multiple high/critical CVEs",
    "cve": "CVE-A,CVE-B", "file": "package.json", "line": None, "package": "tar@6.2.1",
    "reachability": "unreachable", "triage": "acceptable_risk",
    "description": "Transitive dependency brought in by sqlite3.", "remediation": "Upgrade sqlite3.",
}


async def test_a_package_written_with_its_version_is_addressed(scan_session):
    """LIVE: the model recorded `tar@6.2.1`; the gate compared it with the scanner's `tar`,
    refused a review that addressed it, and the model resent it until the recursion limit."""
    from agents_orchestrator.security_agent.tools.security_tools import submit_security_review

    out = await submit_security_review.ainvoke({"review_json": _review([_TAR_AS_THE_MODEL_WROTE_IT])})
    assert "Security review submitted" in out, out


@pytest.mark.parametrize("finding", [
    {"package": "", "cve": ["CVE-A", "CVE-B"]},
    {"package": "tar 6.2.1"},
    {"package": "TAR==6.2.1"},
])
async def test_a_package_is_addressed_by_any_spelling_or_by_its_cve(scan_session, finding):
    from agents_orchestrator.security_agent.tools.security_tools import submit_security_review

    base = {k: v for k, v in _TAR_AS_THE_MODEL_WROTE_IT.items() if k not in ("package", "cve")}
    out = await submit_security_review.ainvoke({"review_json": _review([{**base, **finding}])})
    assert "Security review submitted" in out, out


async def test_a_suppression_must_name_the_package_or_its_cve_not_an_id_that_does_not_exist(scan_session):
    from agents_orchestrator.security_agent.tools.security_tools import submit_security_review

    dangling = {"finding_id": "SCA-001", "reason": "tar unreachable"}
    out = await submit_security_review.ainvoke({"review_json": _review(suppression_log=[dangling])})
    assert out.startswith("ERROR") and "tar 6.2.1" in out and "finding_id = the package name or CVE id" in out
    out = await submit_security_review.ainvoke({"review_json": _review(suppression_log=[{"finding_id": "CVE-A, CVE-B", "reason": "x"}])})
    assert "Security review submitted" in out, out


def test_scoped_package_names_keep_their_scope():
    from agents_orchestrator.security_agent.tools.security_tools import _package_key

    assert _package_key("@tootallnate/once@1.1.2") == "@tootallnate/once"
    assert _package_key("@tootallnate/once") == "@tootallnate/once"
    assert _package_key("tar@6.2.1") == "tar"


async def test_a_review_the_gate_keeps_refusing_ends_the_turn_with_the_reason(scan_session):
    """The same refusal eleven times, then the recursion limit ten minutes later, with nothing
    said to the reader. Three refusals, then submit stops taking attempts and says why."""
    from agents_orchestrator.security_agent.tools.security_tools import submit_security_review

    hides = _review()
    first = await submit_security_review.ainvoke({"review_json": hides})
    second = await submit_security_review.ainvoke({"review_json": hides})
    third = await submit_security_review.ainvoke({"review_json": hides})
    assert first == second and "do not call" not in first
    assert "do not call submit_security_review again" in third and "NOT saved" in third
    fourth = await submit_security_review.ainvoke({"review_json": _review([_TAR_AS_THE_MODEL_WROTE_IT])})
    assert fourth.startswith("ERROR") and "will not take another attempt" in fourth and "tar 6.2.1" in fourth
    assert scan_session.last_artifact is None


async def test_each_turn_starts_with_no_refusals(turn, monkeypatch):
    from agents_orchestrator.security_agent.config.session_state import get_session

    await turn(chunks=["x"])  # warm the fixture; the run below keeps the session
    seen = []

    class _App:
        @staticmethod
        async def astream(_state, **_kwargs):
            seen.append(get_session("s1").submit_refusals)
            yield (AIMessageChunk(content="ok", id="m2"), {})

    get_session("s1").submit_refusals = 3
    monkeypatch.setattr(api, "scan_app", _App)
    await api._process_ws_message(
        {"type": "user_message_with_files", "session_id": "s1", "project_id": "p1", "task_intent": "scan it"},
        websocket=object(), user_id="u1", tenant_id="t1",
    )
    assert seen == [0]


def test_a_long_cve_list_is_never_cut_mid_id():
    """The first live report listed tar's CVEs as "…, CVE-2026-53655, CVE-2026" — cut at 120 characters."""
    from agents_orchestrator.security_agent.review_document import _vulnerable_packages

    scan = {"vulnerabilities": [
        {"id": f"CVE-2026-{20000 + n}", "severity": "high", "package": "tar", "installed": "6.2.1"} for n in range(12)
    ], "sbom": {"components": [{"name": "tar", "version": "6.2.1", "via": "sqlite3"}]}}
    [row] = _vulnerable_packages(scan)
    assert row[4] == "CVE-2026-20000, CVE-2026-20001, CVE-2026-20002, CVE-2026-20003, CVE-2026-20004, CVE-2026-20005 and 6 more"
    assert row[5] == "sqlite3"
