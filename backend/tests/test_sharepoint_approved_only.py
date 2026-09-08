"""Only APPROVED documents reach SharePoint, and nothing can take one back out.

A SharePoint library is the business's record. Things filed there get read by people who
were not in the chat and cannot tell a draft from a signed-off document — and putting one
there is hard to walk back, because deleting it is not something this platform can do.

So the gate is approval, and the interesting tests are the refusals: a pending document
must be refused BY NAME with the reason, not quietly skipped. A silent skip reads as
"nothing to publish" and sends somebody looking for a bug that is not there.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.tools import sharepoint_artifacts as sp  # noqa: E402

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project():
    org, bu, proj = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (:i, :s, 'SharePoint Test')"
        ), {"i": org, "s": f"sp-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'SP Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "project": proj}


async def _doc(project, name, *, status="approved", stage="requirements"):
    art = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status, content_type) "
            "VALUES (CAST(:i AS uuid), CAST(:p AS uuid), :st, CAST(:t AS uuid), "
            "  'document', :bp, :s, 'application/pdf')"
        ), {"i": art, "p": project["project"], "st": stage, "t": project["org"],
            "bp": f"{project['org']}/x/{stage or 'project'}/document/{name}", "s": status})
        await s.commit()
    return art


# -- which documents are eligible ---------------------------------------------


async def test_only_approved_documents_are_eligible(project):
    """THE RULE. Pending and rejected are both excluded — approval is the gate, and a
    rejected document is not a lesser draft, it is one somebody refused."""
    await _doc(project, "signed.pdf", status="approved")
    await _doc(project, "draft.pdf", status="pending")
    await _doc(project, "refused.pdf", status="rejected")

    docs = await sp._approved_documents(project["org"], project["project"], "requirements")

    assert [d["name"] for d in docs] == ["signed.pdf"]


async def test_a_project_wide_document_is_eligible_from_any_stage(project):
    """It belongs to every stage by definition — the same reach `readable_documents`
    gives an agent."""
    await _doc(project, "policy.pdf", stage=None)

    docs = await sp._approved_documents(project["org"], project["project"], "design")

    assert [d["name"] for d in docs] == ["policy.pdf"]


async def test_another_stages_document_is_not_eligible(project):
    """Publishing from the Requirements screen files Requirements' documents. A design
    document is Design's to publish."""
    await _doc(project, "hld.pdf", stage="design")

    docs = await sp._approved_documents(project["org"], project["project"], "requirements")

    assert docs == []


async def test_stories_are_not_documents(project):
    """`story` rows are payload projections with no blob — there is nothing to upload."""
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status) VALUES (gen_random_uuid(), CAST(:p AS uuid), "
            "  'requirements', CAST(:t AS uuid), 'story', :bp, 'approved')"
        ), {"p": project["project"], "t": project["org"], "bp": "x/story/s.json"})
        await s.commit()

    docs = await sp._approved_documents(project["org"], project["project"], "requirements")

    assert docs == []


# -- the refusal says WHY -----------------------------------------------------


async def test_an_unapproved_document_is_named_with_its_status(project):
    """"No approved document called that" is true and useless when the document is
    sitting right there, pending. The caller needs to know which problem it has."""
    await _doc(project, "draft.pdf", status="pending")

    status = await sp._unapproved_named(project["org"], project["project"], "draft.pdf")

    assert status == "pending"


async def test_an_approved_document_is_not_reported_as_unapproved(project):
    """Non-vacuity: the helper answers about the document's status, not about every
    document."""
    await _doc(project, "signed.pdf", status="approved")

    assert await sp._unapproved_named(project["org"], project["project"], "signed.pdf") is None


# -- what the tool set does and does not contain ------------------------------


def test_there_is_no_delete_tool():
    """THE CONSTRAINT, asserted rather than assumed. Taking a file out of the business's
    document library is a person's job, done in SharePoint where that system's own
    permissions and recycle bin apply. A tool named plausibly enough for a model to try
    is exactly what must not exist."""
    names = {t.name for t in sp.make_sharepoint_tools(agent_id="requirements", stage="requirements")}

    assert names == {
        "publish_approved_to_sharepoint",
        "list_sharepoint_documents",
        "read_sharepoint_document",
    }
    assert not any("delete" in n or "remove" in n for n in names)


def test_the_requirements_agent_binds_them():
    """Wired, not merely written. The module existing is not the same as the agent
    being able to call it."""
    from agents_orchestrator.requirements_agent.agents.planning import tools

    names = {getattr(t, "name", "") for t in tools}
    assert "publish_approved_to_sharepoint" in names
    assert "read_sharepoint_document" in names
    # And still no delete, on the agent's whole tool set.
    assert not any("sharepoint" in n and ("delete" in n or "remove" in n) for n in names)
