"""The Testing agent reads the project's record and files its documents into it.

WHAT WAS WRONG, found by reading the agent against the demo flow's steps 15–17
(generate test cases → approve → publish and report) before running them, after the
same class of defect had been fixed in the Design and Development agents:

  * "Generate the functional test cases from the approved BRD" contained
    "functional test", and the router answered "please provide the application URL"
    — the tester wanted a document, not a browser run;
  * the plan's requirements came from the text of the prompt (or an upload); the
    project's approved BRD and design were never consulted, and the upstream
    payloads were looked up by a session id a fresh chat never shares;
  * "publish the test cases to Confluence" as a first message was a greeting: the
    dispatcher knew 'generate_plan_only' and 'greeting' and nothing bound tools;
  * every document the run produced was a download link in a session directory
    that the next session deleted; nothing was filed as a project artifact, so
    there was nothing to approve and nothing for Confluence to publish;
  * a standalone "Hi" was answered as a lost Orchestrator handoff.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agents_orchestrator.testing_agent import project_record as pr
from agents_orchestrator.testing_agent.Nodes import ingest_input as ii

TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
PROJECT = "ed20b947-360d-4881-9a83-52decc68210a"


# ── routing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "Generate the functional and technical test cases for QuickLink from the approved BRD",
    "write test cases for the login feature",
    "Create a test plan based on the HLD",
    "Please draft functional test scenarios covering the redirect flow",
    "test cases for the URL shortener based on the requirements",
])
def test_asking_for_test_cases_is_a_writing_request(prompt):
    assert ii._asks_to_write_test_cases(prompt)


@pytest.mark.parametrize("prompt", [
    "run the functional tests on https://app.example.com",
    "Run unit tests",
    "execute the ui tests",
    "hi",
    "publish the test case document to Confluence",
])
def test_running_or_publishing_is_not_a_writing_request(prompt):
    assert not ii._asks_to_write_test_cases(prompt)


@pytest.mark.parametrize("prompt", [
    "publish the test case document to Confluence",
    "push the results to sharepoint",
    "which approved documents does this project have?",
    "read the BRD and summarise it",
    "Is there any epic assigned to me on the board?",
])
def test_tool_needing_requests_take_the_tool_path(prompt):
    assert ii._asks_for_a_tool(prompt)


async def test_a_test_case_request_routes_to_the_plan_even_though_it_says_functional_test():
    """The URL rule used to catch "functional test" in the sentence."""
    out = await ii.classify_intent({
        "user_prompt": "Generate the functional test cases for QuickLink from the approved BRD",
        "input_file_path": None, "chat_history": None,
    })
    assert out["classified_intent"] == "generate_plan_only"


async def test_a_publish_request_as_a_first_message_reaches_the_tools():
    out = await ii.classify_intent({
        "user_prompt": "publish the test case document to Confluence space QUICKLINK",
        "input_file_path": None, "chat_history": None,
    })
    assert out["classified_intent"] == "follow_up_query"


# ── grounding ────────────────────────────────────────────────────────────────


async def test_the_plan_reads_the_approved_documents_when_nothing_was_uploaded():
    out = await ii.read_input_content({
        "user_prompt": "test cases for QuickLink",
        "input_file_path": None,
        "approved_documents_text": "=== QuickLink_BRD_new.docx (requirements, approved) ===\nFR-1 Shorten a URL.",
        "plan_source": "the project's approved documents: QuickLink_BRD_new.docx",
    })
    assert "FR-1 Shorten a URL." in out["input_content"]
    assert out["plan_source"].startswith("the project's approved documents")


async def test_an_upload_still_wins_over_the_record(tmp_path):
    f = tmp_path / "stories.txt"
    f.write_text("As a user I can paste a URL", encoding="utf-8")
    out = await ii.read_input_content({
        "user_prompt": "test cases", "input_file_path": str(f),
        "approved_documents_text": "THE BRD",
    })
    assert "As a user I can paste a URL" in out["input_content"]
    assert "THE BRD" not in out["input_content"]
    assert out["plan_source"] == "stories.txt"


async def test_grounding_folds_in_the_source_stages_documents_and_records_the_read():
    docs = [
        {"id": "d-design", "title": "architecture.docx", "stage": "design", "approvedAt": "2026-09-15T14:55"},
        {"id": "d-brd", "title": "QuickLink_BRD_new.docx", "stage": "requirements", "approvedAt": "2026-09-15T11:18"},
        {"id": "d-test", "title": "test_cases.docx", "stage": "testing", "approvedAt": "2026-09-15T18:00"},
    ]
    reads = []

    async def _read(*, tenant_id, project_id, artifact_id, consumer_stage, consumer_run_id=None):
        reads.append((artifact_id, consumer_stage, consumer_run_id))
        return {"d-brd": "BRD TEXT", "d-design": "DESIGN TEXT"}.get(artifact_id, "TEST DOC"), ""

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch("shared.db.get_db_session_for_tenant", lambda t: _Session()), \
            patch("shared.services.artifact_versions.readable_documents", AsyncMock(return_value=docs)), \
            patch("shared.services.artifact_consumption.read_document_for_agent", _read):
        text, source = await pr.approved_documents_text(TENANT, PROJECT, consumer_run_id="sess-1")

    assert text.index("QuickLink_BRD_new.docx") < text.index("architecture.docx"), "requirements before design"
    assert "BRD TEXT" in text and "DESIGN TEXT" in text
    assert "TEST DOC" not in text, "testing's own documents are not a source for themselves"
    assert source == "the project's approved documents: QuickLink_BRD_new.docx, architecture.docx"
    assert all(stage == "testing" and run == "sess-1" for _, stage, run in reads), "each read is evidence"


async def test_grounding_is_bounded():
    docs = [{"id": f"d{i}", "title": f"doc{i}.docx", "stage": "requirements", "approvedAt": ""} for i in range(5)]

    async def _read(**kw):
        return "x" * 30_000, ""

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch("shared.db.get_db_session_for_tenant", lambda t: _Session()), \
            patch("shared.services.artifact_versions.readable_documents", AsyncMock(return_value=docs)), \
            patch("shared.services.artifact_consumption.read_document_for_agent", _read):
        text, _ = await pr.approved_documents_text(TENANT, PROJECT)
    assert len(text) <= pr.MAX_TOTAL_CHARS + 2_000
    assert "[document continues beyond what was folded in]" in text


# ── the greeting ─────────────────────────────────────────────────────────────


async def test_a_standalone_greeting_offers_the_three_things_not_a_lost_handoff():
    out = await ii.handle_greeting({"orchestrator_driven": False, "approved_documents_text": "BRD"})
    msg = out["final_user_message"]
    assert "handoff" not in msg
    assert "Write test cases" in msg and "Run tests" in msg and "Publish" in msg
    assert "approved documents" in msg


async def test_an_orchestrator_greeting_without_upstream_still_asks_for_the_repo():
    out = await ii.handle_greeting({"orchestrator_driven": True, "upstream_development": {}})
    assert "repo + branch" in out["final_user_message"]


# ── filing the outputs ───────────────────────────────────────────────────────


async def test_the_runs_documents_are_filed_once_as_testing_artifacts(tmp_path):
    for name in ("test_cases.docx", "test_plan.xlsx", "qa_report.pdf", "qa_report.html", "coverage.html", "Generated_Test_Code.py"):
        (tmp_path / name).write_bytes(b"x")
    registered = []

    async def _register(filename, file_path, url, *, stage, consented=None, note=None):
        registered.append((filename, stage, note, url))

    already: dict = {}
    with patch("shared.services.chat_artifacts.register_generated_file", _register):
        filed = await pr.record_outputs(str(tmp_path), session_id="s1", base_url="http://x", user_id="u", already=already)
        again = await pr.record_outputs(str(tmp_path), session_id="s1", base_url="http://x", user_id="u", already=already)

    assert filed == ["test_cases.docx", "test_plan.xlsx", "qa_report.pdf", "coverage.html"]
    assert again == [], "a follow-up turn files nothing twice"
    assert all(stage == "testing" for _, stage, _, _ in registered)
    assert "qa_report.html" not in [r[0] for r in registered], "the PDF stands in for the HTML"
    assert "Generated_Test_Code.py" not in [r[0] for r in registered], "code is not a document"
    assert registered[0][3] == "http://x/generated/u/orchestrator/s1/output/test_cases.docx"


async def test_a_regenerated_document_is_filed_again(tmp_path):
    (tmp_path / "test_plan.xlsx").write_bytes(b"v1")
    registered = []

    async def _register(filename, file_path, url, *, stage, consented=None, note=None):
        registered.append(filename)

    already: dict = {}
    with patch("shared.services.chat_artifacts.register_generated_file", _register):
        await pr.record_outputs(str(tmp_path), session_id="s1", base_url="http://x", user_id="u", already=already)
        (tmp_path / "test_plan.xlsx").write_bytes(b"v2, a later run")
        os.utime(tmp_path / "test_plan.xlsx", (1, 2_000_000_000))
        await pr.record_outputs(str(tmp_path), session_id="s1", base_url="http://x", user_id="u", already=already)
    assert registered == ["test_plan.xlsx", "test_plan.xlsx"]
