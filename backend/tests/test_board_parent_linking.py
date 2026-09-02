"""Creating a work item UNDER another one, for real.

An agent reported "created the 3 Tasks and linked them under Epic #1". No link was
ever sent: `create_work_item` built a JSON-patch document with a title, an area path,
a description and acceptance criteria, and nothing else. The Tasks were orphans and
the only trace of the Epic was the sentence "Parent Epic: #1" typed into a description
field — so the user had a board that looked organised and was not.

That is the worst shape a failure can take: silent, plausible, and discovered later by
somebody relying on the structure.

TWO BOARDS, TWO SHAPES. Azure DevOps links through a `relations` entry keyed by a
numeric work item id; Jira sets `fields.parent` keyed by an issue key. The
provider-neutral tool takes whatever the board reported, and each connector renders it.

ATOMIC ON BOTH. The link travels in the SAME request as the create, never a follow-up
PATCH — a second call that fails leaves exactly the orphan this is meant to stop.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Azure DevOps: a relations entry in the create patch ──────────────────────


def _patch_ops(**kwargs):
    """Capture the JSON-patch document create_work_item posts."""
    import config.ado_ingestion as ado

    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": 42, "fields": {"System.Title": kwargs.get("title", "")}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["ops"] = json
            return _Resp()

    return ado, captured, _Client


@pytest.mark.unit
async def test_ado_sends_a_hierarchy_reverse_relation():
    ado, captured, client = _patch_ops(title="Set up backlog")
    with patch.object(ado.httpx, "AsyncClient", lambda *a, **k: client()):
        await ado.create_work_item(
            org_url="https://dev.azure.com/acme", project="sdlc",
            work_item_type="Task", title="Set up backlog", parent_id="1", pat="x",
        )
    rels = [o for o in captured["ops"] if o.get("path") == "/relations/-"]
    assert len(rels) == 1
    value = rels[0]["value"]
    # REVERSE is child->parent. Forward would silently invert the tree.
    assert value["rel"] == "System.LinkTypes.Hierarchy-Reverse"
    assert value["url"] == "https://dev.azure.com/acme/_apis/wit/workItems/1"


@pytest.mark.unit
async def test_ado_sends_no_relation_when_no_parent_is_given():
    """The unparented path must stay byte-identical — every existing create uses it."""
    ado, captured, client = _patch_ops(title="Standalone")
    with patch.object(ado.httpx, "AsyncClient", lambda *a, **k: client()):
        await ado.create_work_item(
            org_url="https://dev.azure.com/acme", project="sdlc",
            work_item_type="Task", title="Standalone", pat="x",
        )
    assert not [o for o in captured["ops"] if o.get("path") == "/relations/-"]


@pytest.mark.unit
async def test_ado_links_in_the_same_request_as_the_create():
    """Not a follow-up PATCH: a second call that fails leaves the orphan behind."""
    ado, captured, client = _patch_ops(title="Child")
    with patch.object(ado.httpx, "AsyncClient", lambda *a, **k: client()):
        await ado.create_work_item(
            org_url="https://dev.azure.com/acme", project="sdlc",
            work_item_type="Task", title="Child", parent_id="7", pat="x",
        )
    assert "/_apis/wit/workitems/$Task" in captured["url"]
    assert any(o.get("path") == "/relations/-" for o in captured["ops"])
    assert any(o.get("path") == "/fields/System.Title" for o in captured["ops"])


# ── Jira: fields.parent, keyed by issue key ──────────────────────────────────


async def _jira_payload(**kwargs):
    from config.connectors.jira import JiraConnector

    conn = JiraConnector(org_url="https://acme.atlassian.net", tenant_id="t")
    captured = {}

    async def _req(method, path, json=None, **kw):
        captured["method"], captured["path"], captured["json"] = method, path, json
        return {"id": "10001", "key": "SCRUM-9"}, {}

    with patch.object(conn, "_resolve_project_key", AsyncMock(return_value="SCRUM")), \
            patch.object(conn, "_jira_request_with_retry", _req):
        await conn.create_item(project="My Software Team", title="A story", **kwargs)
    return captured


@pytest.mark.unit
async def test_jira_sets_fields_parent_by_key():
    captured = await _jira_payload(parent_id="SCRUM-1")
    assert captured["json"]["fields"]["parent"] == {"key": "SCRUM-1"}


@pytest.mark.unit
async def test_jira_omits_parent_when_none_is_given():
    captured = await _jira_payload()
    assert "parent" not in captured["json"]["fields"]


@pytest.mark.unit
async def test_jira_still_translates_the_item_type_alongside_the_parent():
    """The two features touch the same payload; neither may undo the other."""
    captured = await _jira_payload(item_type="User Story", parent_id="SCRUM-1")
    assert captured["json"]["fields"]["issuetype"] == {"name": "Story"}
    assert captured["json"]["fields"]["parent"] == {"key": "SCRUM-1"}


# ── the tools pass it, and say what they did ─────────────────────────────────


class _Board:
    display_name = "Azure Boards"
    connector_name = "azure_devops"

    def __init__(self):
        self.kwargs = None

    async def write_adapter(self, op, **kw):
        self.kwargs = kw
        return {"id": 5, "work_item_id": 5, "url": ""}


def _gated(board):
    from agents_orchestrator.requirements_agent.agents import planning

    return patch.object(planning, "_board_connector", AsyncMock(return_value=(board, None)))


@pytest.mark.unit
async def test_create_board_item_forwards_the_parent():
    from agents_orchestrator.requirements_agent.agents import planning

    board = _Board()
    with _gated(board):
        out = await planning.create_board_item.ainvoke(
            {"project": "sdlc", "title": "Child", "work_item_type": "Task",
             "parent_id": "1"}
        )
    assert board.kwargs["parent_id"] == "1"
    # The reply must SAY it was parented — "Created" alone is what allowed the
    # original false report to sound true.
    assert "child of #1" in out


@pytest.mark.unit
async def test_create_board_item_does_not_claim_a_parent_it_was_not_given():
    from agents_orchestrator.requirements_agent.agents import planning

    board = _Board()
    with _gated(board):
        out = await planning.create_board_item.ainvoke(
            {"project": "sdlc", "title": "Orphan", "work_item_type": "Task"}
        )
    assert board.kwargs["parent_id"] == ""
    assert "child of" not in out


@pytest.mark.unit
async def test_bulk_create_forwards_both_the_type_and_the_parent():
    """write_stories_to_board hardcoded item_type="User Story", so it failed every
    create on an ADO Basic project — the bulk twin of the bug fixed for the single
    create."""
    import json

    from agents_orchestrator.requirements_agent.agents import planning

    board = _Board()
    with _gated(board):
        await planning.write_stories_to_board.ainvoke({
            "stories_json": json.dumps([{"title": "S1"}]),
            "project": "sdlc",
            "work_item_type": "Issue",
            "parent_id": "1",
        })
    assert board.kwargs["item_type"] == "Issue"
    assert board.kwargs["parent_id"] == "1"


@pytest.mark.unit
def test_the_prompt_explains_both_id_shapes():
    """ADO takes a number, Jira a key. Passing the wrong one silently fails to link."""
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE

    assert "parent_id" in INGESTION_SYS_MESSAGE
    assert "SCRUM-1" in INGESTION_SYS_MESSAGE
    assert "create the Epic FIRST" in INGESTION_SYS_MESSAGE
