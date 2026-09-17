"""The code review checklist: one fixed shape, filed as Word, parsed back by the Checklist tab."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SECTIONS = [
    {"section": "Security", "items": [
        {"check": "No hardcoded secrets", "how": "Gitleaks passed", "status": "pass", "note": "0 secrets"},
        {"check": "Inputs validated | encoded", "how": "Read the controllers", "status": "FAIL", "note": "F-001"},
    ]},
    {"section": "Testing", "items": ["New code has tests", {"check": ""}]},
]


def test_the_checklist_is_one_fixed_table_shape_per_section():
    from agents_orchestrator.code_review_agent.checklist import HEADER, checklist_markdown, normalise

    sections = normalise(json.dumps(SECTIONS))
    md = checklist_markdown("QuickLink Checklist", "Why we check.", sections)
    assert md.count(HEADER) == 2 and "## Security" in md and "## Testing" in md
    assert "| 1 | No hardcoded secrets | Gitleaks passed | Pass | 0 secrets |" in md
    # A pipe in a cell cannot break the table; an unknown status is "To check", never "Pass".
    assert "| 2 | Inputs validated / encoded | Read the controllers | Fail | F-001 |" in md
    assert "| 3 | New code has tests | — | To check | — |" in md
    assert "3 checks · 1 pass · 1 fail · 0 not applicable · 1 to check" in md


def test_a_checklist_with_no_checks_is_refused():
    from agents_orchestrator.code_review_agent.checklist import normalise

    with pytest.raises(ValueError):
        normalise("[]")


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    import config.connection_manager as cm
    from config.ws_helper import set_session_id, set_user_id
    from shared.tools import stage_documents

    class _M:
        frames = []

        async def broadcast(self, p):
            self.frames.append(p)

    set_session_id("cl-test")
    set_user_id("u1")
    mgr = _M()
    mgr.frames = []
    monkeypatch.setattr(cm, "manager", mgr)
    monkeypatch.setattr(stage_documents, "_FILES_DIR", str(tmp_path))
    return mgr


async def test_the_checklist_is_filed_as_word_under_code_review_and_announced_as_a_checklist(ctx):
    from agents_orchestrator.code_review_agent.checklist import create_review_checklist

    register = AsyncMock(return_value="cl-1")
    with patch("shared.services.chat_artifacts.register_generated_file", register):
        out = await create_review_checklist.ainvoke({"title": "QuickLink Code Review Checklist",
                                                     "sections_json": json.dumps(SECTIONS)})
    name, path, _url = register.await_args.args
    assert name == "QuickLink_Code_Review_Checklist.docx" and register.await_args.kwargs["stage"] == "code_review"
    assert Path(path).with_suffix(".md").read_text(encoding="utf-8").startswith("# QuickLink Code Review Checklist")
    [frame] = [f for f in ctx.frames if f.get("type") == "file_generated"]
    assert frame["kind"] == "checklist" and frame["artifact_id"] == "cl-1"
    assert "Checklist tab" in out and "DRAFT" in out


def test_the_agent_uses_the_checklist_tool_for_checklists():
    from agents_orchestrator.code_review_agent.agents import reviewer
    from agents_orchestrator.code_review_agent.prompts.review_prompt import CODE_REVIEW_SYSTEM_PROMPT

    assert "create_review_checklist" in {t.name for t in reviewer._tools}
    assert "create_review_checklist" in CODE_REVIEW_SYSTEM_PROMPT
