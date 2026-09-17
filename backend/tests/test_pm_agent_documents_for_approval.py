"""The Project Manager's documents are the Plan stage's, and can be raised for approval.

LIVE, 17 Sep 2026: an effort estimate exported from the Project Manager page was filed under
`design` (the agent borrowed the Design agent's export tool), as `.md`. The Plan page showed
"0 artifacts", "send it for approval" had no tool to act with, Requests & Approvals never
saw it, and the chat's link opened raw markdown.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ESTIMATE = """**Effort Estimation Document – QuickLink**

### 1. Summary of Work Items
| Type | Count |
|---|---|
| Epic | 5 |

### 2. Effort Estimate by Epic
- Short Link Creation: 13 SP
"""


class _Manager:
    def __init__(self):
        self.frames = []

    async def broadcast(self, payload):
        self.frames.append(payload)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    import config.connection_manager as cm
    from config.ws_helper import set_session_id, set_user_id
    from shared.tools import stage_documents

    set_session_id("pm-doc-test")
    set_user_id("u1")
    mgr = _Manager()
    monkeypatch.setattr(cm, "manager", mgr)
    monkeypatch.setattr(stage_documents, "_FILES_DIR", str(tmp_path))
    return mgr, tmp_path


async def test_an_export_is_a_word_draft_filed_under_plan_with_its_page_copy(ctx):
    from agents_orchestrator.pm_agent.plan_documents import export_document

    mgr, root = ctx
    register = AsyncMock(return_value="art-pm")
    with patch("shared.services.chat_artifacts.register_generated_file", register):
        out = await export_document.ainvoke({"content": ESTIMATE, "filename": "QuickLink_Effort_Estimation.md"})

    name, path, _url = register.await_args.args
    assert name == "QuickLink_Effort_Estimation.docx" and register.await_args.kwargs["stage"] == "plan"
    assert Path(path).stat().st_size > 0
    page = Path(path).with_suffix(".md").read_text(encoding="utf-8")
    # The bold title becomes the title, and the ### sections become the page's sections.
    assert page.startswith("# Effort Estimation Document – QuickLink")
    assert "## Summary of Work Items" in page and "## Effort Estimate by Epic" in page
    [frame] = [f for f in mgr.frames if f.get("type") == "file_generated"]
    assert frame["artifact_id"] == "art-pm"
    assert "DRAFT" in out and 'raise_document_for_approval with "QuickLink_Effort_Estimation.docx"' in out


async def test_an_export_that_could_not_be_recorded_says_so(ctx):
    from agents_orchestrator.pm_agent.plan_documents import export_document

    mgr, _ = ctx
    with patch("shared.services.chat_artifacts.register_generated_file", AsyncMock(return_value=None)):
        out = await export_document.ainvoke({"content": ESTIMATE, "filename": "estimate.docx"})
    assert "could NOT be recorded" in out and not mgr.frames


def test_the_project_manager_exports_as_plan_and_can_raise():
    from agents_orchestrator.pm_agent.agents import schedule
    from agents_orchestrator.pm_agent.plan_documents import export_document

    by_name = {t.name: t for t in schedule.tools}
    assert by_name["export_document"] is export_document
    assert "raise_document_for_approval" in by_name
    assert "raise_document_for_approval" in schedule.PM_SYS_MESSAGE and "DRAFT" in schedule.PM_SYS_MESSAGE
