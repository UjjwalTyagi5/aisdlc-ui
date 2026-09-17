"""Documents by name, stories remembered, epics on the board, and a PRD generator.

THE TURN THAT STALLED. "Read TEST_Project_BRD.docx fully and create user stories": the
agent read the approved BRD, then had to call generate_stories_from_brd(brd_content=<the
whole BRD>) — re-typing the document as a tool argument. The model call timed out at 90s,
twice. The prompt told it to do exactly that ("ask the user to paste the BRD text").
Writing the stories to ADO had the same shape: the stories JSON typed back out again.

And "generate a PRD" answered "no dedicated PRD generator exists".
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

TENANT = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
BRD_TEXT = "## Executive Summary\nQuickLink shortens internal URLs.\n\n## Requirements\n- Create short links\n- Track clicks"

STORIES = {
    "epics": [
        {"epic_title": "Link Creation", "epic_description": "Create links", "stories": ["As a comms officer, I want a short link"]},
        {"epic_title": "Click Tracking", "epic_description": "Measure", "stories": ["As a comms officer, I want click counts"]},
    ],
    "stories": [
        {"title": "As a comms officer, I want a short link", "description": "d1", "epic": "Link Creation",
         "acceptance_criteria": ["Given a URL When submitted Then a short link is returned"]},
        {"title": "As a comms officer, I want click counts", "description": "d2", "epic": "Click Tracking",
         "acceptance_criteria": ["Given clicks When viewing stats Then totals show"]},
    ],
}


@pytest.fixture
def turn(tmp_path, monkeypatch):
    from agents_orchestrator.requirements_agent.agents import planning
    from config.ws_helper import set_project_id, set_session_id, set_tenant_id, set_user_id

    session = f"sess-{tmp_path.name}"
    set_session_id(session)
    set_user_id("user-1")
    set_tenant_id(TENANT)
    set_project_id(PROJECT)
    monkeypatch.setattr(planning.esett, "FILES", str(tmp_path), raising=False)
    monkeypatch.setattr(planning, "_project_display_name", AsyncMock(return_value="QuickLink"))
    return planning, session


@asynccontextmanager
async def _no_db(tenant_id):
    yield MagicMock()


def _approved(docs, text=BRD_TEXT):
    return (
        patch("shared.db.get_db_session_for_tenant", _no_db),
        patch("shared.services.artifact_versions.readable_documents", AsyncMock(return_value=docs)),
        patch("shared.services.artifact_consumption.read_document_for_agent", AsyncMock(return_value=(text, "ok"))),
    )


APPROVED_BRD = {"id": "fdd398b7-3874-4784-b0e4-7ee1201748da", "title": "TEST_Project_BRD.docx", "stage": "requirements"}


# ── sources by name ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_approved_project_document_is_a_source_by_its_file_name(turn):
    planning, _ = turn
    p1, p2, p3 = _approved([APPROVED_BRD])
    with p1, p2, p3 as reader:
        paths, err = await planning._resolve_source_files(["TEST_Project_BRD.docx"])

    assert err is None and len(paths) == 1
    assert open(paths[0], encoding="utf-8").read() == BRD_TEXT
    assert reader.await_args.kwargs["artifact_id"] == APPROVED_BRD["id"]
    assert reader.await_args.kwargs["consumer_stage"] == "requirements", "the read is recorded as evidence"


@pytest.mark.asyncio
async def test_an_approved_document_is_also_a_source_by_its_id(turn):
    planning, _ = turn
    p1, p2, p3 = _approved([APPROVED_BRD])
    with p1, p2, p3:
        paths, err = await planning._resolve_source_files([APPROVED_BRD["id"]])
    assert err is None and len(paths) == 1


@pytest.mark.asyncio
async def test_an_unknown_source_names_what_can_be_used(turn):
    planning, _ = turn
    p1, p2, p3 = _approved([APPROVED_BRD])
    with p1, p2, p3:
        paths, err = await planning._resolve_source_files(["nope.docx"])
    assert paths == [] and err.startswith("Error:")
    assert "TEST_Project_BRD.docx" in err and "Nothing was generated" in err


@pytest.mark.asyncio
async def test_a_document_generated_in_this_conversation_is_a_source_by_its_name(turn, monkeypatch):
    planning, _ = turn
    monkeypatch.setattr(planning, "_openai_generate", lambda prompt, file_paths=None: BRD_TEXT)
    monkeypatch.setattr(planning, "broadcast_file_generated", AsyncMock(return_value="http://x/QuickLink_BRD.docx"))
    await planning.generate_brd.ainvoke({"file_names": [], "custom_prompt": "a link shortener"})

    p1, p2, p3 = _approved([])
    with p1, p2, p3:
        paths, err = await planning._resolve_source_files(["QuickLink_BRD.docx"])
    assert err is None and paths[0].endswith("QuickLink_BRD.md")


# ── stories from a named document, remembered for the board ─────────────────


@pytest.mark.asyncio
async def test_stories_are_generated_from_a_named_document_without_pasting_it(turn, monkeypatch):
    planning, session = turn
    seen = {}

    def _gen(prompt, file_paths=None):
        seen["prompt"], seen["files"] = prompt, list(file_paths or [])
        return json.dumps(STORIES)

    monkeypatch.setattr(planning, "_openai_generate", _gen)
    p1, p2, p3 = _approved([APPROVED_BRD])
    with p1, p2, p3:
        out = await planning.generate_stories_from_brd.ainvoke({"source_documents": ["TEST_Project_BRD.docx"]})

    assert json.loads(out)["stories"][0]["epic"] == "Link Creation"
    assert len(seen["files"]) == 1 and open(seen["files"][0], encoding="utf-8").read() == BRD_TEXT
    assert planning._LAST_STORIES[session]["stories"][1]["title"] == "As a comms officer, I want click counts"


@pytest.mark.asyncio
async def test_stories_need_a_source(turn):
    planning, _ = turn
    out = await planning.generate_stories_from_brd.ainvoke({})
    assert out.startswith("Error:")


@pytest.mark.asyncio
async def test_normalising_keeps_each_storys_epic(turn, monkeypatch):
    planning, session = turn
    planning._LAST_STORIES[session] = json.loads(json.dumps(STORIES))
    normalised = [
        {"id": None, "title": s["title"], "description": s["description"],
         "acceptance_criteria": ["Scenario: x\nGiven a\nWhen b\nThen c"]}
        for s in STORIES["stories"]
    ]
    monkeypatch.setattr(planning, "_openai_generate", lambda prompt, file_paths=None: json.dumps(normalised))

    out = await planning.normalize_acceptance_criteria.ainvoke({})

    kept = planning._LAST_STORIES[session]["stories"]
    assert [s["epic"] for s in kept] == ["Link Creation", "Click Tracking"]
    assert kept[0]["acceptance_criteria"] == ["Scenario: x\nGiven a\nWhen b\nThen c"]
    assert json.loads(out)[0]["title"] == STORIES["stories"][0]["title"]


def _board():
    created = []

    async def write_adapter(op, **kwargs):
        created.append(kwargs)
        return {"id": 100 + len(created)}

    board = MagicMock()
    board.write_adapter = AsyncMock(side_effect=write_adapter)
    return board, created


@pytest.mark.asyncio
async def test_the_generated_stories_go_to_the_board_under_their_epics(turn, monkeypatch):
    planning, session = turn
    planning._LAST_STORIES[session] = json.loads(json.dumps(STORIES))
    board, created = _board()
    monkeypatch.setattr(planning, "_board_connector", AsyncMock(return_value=(board, None)))

    out = await planning.write_stories_to_board.ainvoke({"project": "QuickLink", "create_epics": True})

    epics = [c for c in created if c["item_type"] == "Epic"]
    stories = [c for c in created if c["item_type"] == "User Story"]
    assert [e["title"] for e in epics] == ["Link Creation", "Click Tracking"]
    epic_ids = {e["title"]: str(101 + i) for i, e in enumerate(epics)}
    assert [s["parent_id"] for s in stories] == [epic_ids["Link Creation"], epic_ids["Click Tracking"]]
    assert "2 epic(s)" in out and "2 work item(s)" in out


@pytest.mark.asyncio
async def test_board_write_without_stories_says_so(turn, monkeypatch):
    planning, session = turn
    planning._LAST_STORIES.pop(session, None)
    board, created = _board()
    monkeypatch.setattr(planning, "_board_connector", AsyncMock(return_value=(board, None)))

    out = await planning.write_stories_to_board.ainvoke({"project": "QuickLink"})
    assert out.startswith("Error:") and created == []


@pytest.mark.asyncio
async def test_epics_and_a_single_parent_are_not_combined(turn, monkeypatch):
    planning, session = turn
    planning._LAST_STORIES[session] = json.loads(json.dumps(STORIES))
    board, created = _board()
    monkeypatch.setattr(planning, "_board_connector", AsyncMock(return_value=(board, None)))

    out = await planning.write_stories_to_board.ainvoke({"project": "QuickLink", "create_epics": True, "parent_id": "7"})
    assert out.startswith("Error:") and created == []


# ── a PRD, and no "no tool exists" ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_prd_writes_a_designed_product_requirements_document(turn, monkeypatch):
    from docx import Document

    planning, session = turn
    prd = "## Product Overview\nQuickLink.\n\n## Target Users\nComms officers.\n\n## Features\n- Short links\n\n## Success Metrics\n- Links under 30 chars"
    monkeypatch.setattr(planning, "_openai_generate", lambda prompt, file_paths=None: prd)
    monkeypatch.setattr(planning, "broadcast_file_generated", AsyncMock(side_effect=lambda s, name, p: f"http://x/{name}"))

    out = await planning.generate_prd.ainvoke({"file_names": [], "custom_prompt": "from the approved BRD"})

    assert "http://x/QuickLink_PRD.docx" in out
    import os
    path = os.path.join(planning.esett.FILES, "user-1", "requirements_agent", session, "output", "QuickLink_PRD.docx")
    band = "\n".join(c.text for t in Document(path).tables for r in t.rows for c in r.cells)
    assert "PRODUCT REQUIREMENTS DOCUMENT" in band


def test_the_prompt_never_asks_for_pasted_documents_and_offers_every_document():
    from agents_orchestrator.requirements_agent.agents import planning

    flat = " ".join(planning.INGESTION_SYS_MESSAGE.split())
    assert "paste the BRD" not in flat
    assert "with the pasted content" not in flat
    assert "source_documents" in flat
    assert "generate_prd" in flat and "generate_prd" in {t.name for t in planning.tools}
    assert "create_epics" in flat
    assert "never answer that no tool exists" in flat
