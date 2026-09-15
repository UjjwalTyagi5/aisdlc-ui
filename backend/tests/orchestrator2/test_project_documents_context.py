"""The project's approved documents, rendered for the Orchestrator's prompts.

`project_documents.approved_documents_context(project_id, tenant_id)` is what lets the
Orchestrator answer "are there any artifacts in this project?" from the record instead
of from nothing. Before it, a project with an approved BRD on the Requirements page was
described by the Orchestrator as "a fresh or empty project context" — the standalone
agents could list that BRD through `list_project_documents`, and the Orchestrator's own
router, which answers such questions itself, had never been told it existed.

What is pinned:

  * documents are grouped by the agent that produced them, under the agent's DISPLAY
    name, with project-wide ones under their own heading;
  * every row carries the id the agents need for `read_document`, and who approved it;
  * only approved documents are read — the loader goes through `readable_documents`,
    whose whole gate is approval;
  * a project with none, or no project at all, yields `""`;
  * a failed read RAISES — the split `context.py` and `attachments.py` draw, drawn again:
    "there are no documents" and "we could not find out" are different answers;
  * the block stays inside its size cap and says how many rows it left out.
"""
from __future__ import annotations

import pytest

from agents_orchestrator.orchestrator2 import project_documents as pd

_PROJECT = "44444444-4444-4444-4444-444444444444"
_TENANT = "22222222-2222-2222-2222-222222222222"


def _doc(**overrides):
    base = {
        "id": "942db0a8-8920-4864-9274-fa6cef071039",
        "title": "QuickLink_BRD_v1.docx",
        "type": "document",
        "scope": "agent",
        "stage": "requirements",
        "sizeBytes": 39065,
        "approvedBy": "sarthak2004@example.com",
        "approvedAt": "2026-09-15T06:02:34+00:00",
        "via": "agent",
    }
    base.update(overrides)
    return base


def _stub_loader(monkeypatch, docs):
    async def _fake(project_id, tenant_id):
        return list(docs)

    monkeypatch.setattr(pd, "_load_approved_documents", _fake)


# ── nothing to say ──────────────────────────────────────────────────────────


async def test_a_run_with_no_project_yields_nothing():
    assert await pd.approved_documents_context(None, _TENANT) == ""
    assert await pd.approved_documents_context("", _TENANT) == ""


async def test_an_id_that_cannot_address_a_row_yields_nothing_without_reading(monkeypatch):
    """`context.py`'s rule: caller-supplied text is never pushed into a UUID
    comparison, and "nothing" is still the well-defined answer."""
    async def _must_not_run(project_id, tenant_id):
        raise AssertionError("no read may happen for an unusable id")

    monkeypatch.setattr(pd, "_load_approved_documents", _must_not_run)
    assert await pd.approved_documents_context("proj-A", _TENANT) == ""
    assert await pd.approved_documents_context(_PROJECT, "t1") == ""


async def test_a_project_with_no_approved_documents_yields_nothing(monkeypatch):
    _stub_loader(monkeypatch, [])
    assert await pd.approved_documents_context(_PROJECT, _TENANT) == ""


# ── the rendering ───────────────────────────────────────────────────────────


async def test_documents_are_grouped_under_the_producing_agents_name(monkeypatch):
    _stub_loader(monkeypatch, [
        _doc(),
        _doc(id="a1", title="HLD.docx", stage="design"),
        _doc(id="a2", title="Schedule.xlsx", stage="plan"),
    ])
    out = await pd.approved_documents_context(_PROJECT, _TENANT)

    assert "### Requirements" in out
    assert "### Design" in out
    # `plan` is the Project Manager agent — the reader must see the name they know.
    assert "### Project Manager" in out
    assert "QuickLink_BRD_v1.docx" in out
    assert "HLD.docx" in out
    assert "Schedule.xlsx" in out


async def test_project_wide_documents_get_their_own_heading(monkeypatch):
    _stub_loader(monkeypatch, [
        _doc(id="p1", title="Coding-Standard.pdf", stage=None, scope="project", via="project"),
    ])
    out = await pd.approved_documents_context(_PROJECT, _TENANT)

    assert "### Project-wide" in out
    assert "Coding-Standard.pdf" in out


async def test_every_row_carries_the_id_and_the_approver(monkeypatch):
    """The id is what `read_document` takes; the approver is what makes "approved"
    mean something to the reader rather than being a bare adjective."""
    _stub_loader(monkeypatch, [_doc()])
    out = await pd.approved_documents_context(_PROJECT, _TENANT)

    assert "942db0a8-8920-4864-9274-fa6cef071039" in out
    assert "sarthak2004@example.com" in out
    assert "2026-09-15" in out


async def test_the_block_tells_the_agent_how_to_read_a_document(monkeypatch):
    """Metadata only, and it must say so: an agent handed a list of titles with no
    instruction infers the contents from the names."""
    _stub_loader(monkeypatch, [_doc()])
    out = await pd.approved_documents_context(_PROJECT, _TENANT)

    assert "read_document" in out
    assert pd.HEADER_LINE in out
    assert pd.FOOTER_LINE in out


async def test_an_unknown_stage_is_still_listed_under_its_own_name(monkeypatch):
    """A document from a stage this engine cannot name must not vanish — the record
    holds it, so the block must too."""
    _stub_loader(monkeypatch, [_doc(id="x1", title="legacy.pdf", stage="some_new_stage")])
    out = await pd.approved_documents_context(_PROJECT, _TENANT)

    assert "legacy.pdf" in out
    assert "some_new_stage" in out


async def test_the_size_is_shown_in_a_readable_unit(monkeypatch):
    _stub_loader(monkeypatch, [_doc(sizeBytes=39065)])
    out = await pd.approved_documents_context(_PROJECT, _TENANT)
    assert "38.1 KB" in out  # 39065 / 1024
    assert "39065" not in out, "a raw byte count is not a size a reader can use"


# ── the loader ──────────────────────────────────────────────────────────────


async def test_the_loader_reads_through_the_approval_gate(monkeypatch):
    """`readable_documents` is the ONE query whose gate is approval. Going around it —
    a direct `select(Artifact)` here — would be a second, unguarded way to list a
    pending or rejected document."""
    seen = {}

    class _Session:
        pass

    class _Factory:
        def __init__(self, tenant_id):
            seen["tenant"] = tenant_id

        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    async def _fake_readable(db, project_id, *, covered_ids=None):
        seen["project"] = project_id
        seen["session"] = db
        return [_doc()]

    monkeypatch.setattr(pd, "get_db_session_for_tenant", _Factory)
    monkeypatch.setattr(pd, "readable_documents", _fake_readable)

    docs = await pd._load_approved_documents(_PROJECT, _TENANT)

    assert seen["tenant"] == _TENANT
    assert seen["project"] == _PROJECT
    assert isinstance(seen["session"], _Session)
    assert [d["id"] for d in docs] == ["942db0a8-8920-4864-9274-fa6cef071039"]


async def test_a_failed_read_raises_rather_than_reporting_no_documents(monkeypatch):
    """`""` has to keep meaning "the project holds nothing". A database that is down
    reported as an empty project would have the Orchestrator tell the user their
    approved BRD does not exist."""
    async def _boom(project_id, tenant_id):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(pd, "_load_approved_documents", _boom)

    with pytest.raises(pd.ProjectDocumentsUnavailableError) as info:
        await pd.approved_documents_context(_PROJECT, _TENANT)
    assert isinstance(info.value.__cause__, RuntimeError)


# ── the size cap ────────────────────────────────────────────────────────────


async def test_a_huge_project_stays_inside_the_cap_and_says_what_it_left_out(monkeypatch):
    docs = [
        _doc(id=f"{i:08x}-0000-0000-0000-000000000000", title=f"Document-{i}.docx")
        for i in range(2000)
    ]
    _stub_loader(monkeypatch, docs)
    out = await pd.approved_documents_context(_PROJECT, _TENANT)

    assert len(out) <= pd.MAX_CONTEXT_CHARS
    assert "Document-0.docx" in out
    assert "more approved document" in out, "the omission must be said out loud"
    assert pd.FOOTER_LINE in out, "the footer must survive the cut"


async def test_a_small_project_is_rendered_whole(monkeypatch):
    _stub_loader(monkeypatch, [_doc(id=f"id-{i}", title=f"D{i}.docx") for i in range(5)])
    out = await pd.approved_documents_context(_PROJECT, _TENANT)
    assert "more approved document" not in out
    for i in range(5):
        assert f"D{i}.docx" in out
