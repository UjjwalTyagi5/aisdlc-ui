"""Read is the project's; write is the agent's.

THE RULE THIS PINS: within a project every agent may read the approved documents by
default, but a write can only be done by the respective agent.

Both halves are already true. That is exactly why they are worth a test: nothing here
fails today, and each half is one plausible-looking edit away from being wrong in a way
nobody would notice. Adding `Artifact.stage == consumer_stage` to the read query looks
like tightening a permission and would silently sever every downstream agent from the
work it is supposed to build on — the failure is an agent that reports "no documents"
and reasons without them, not an error anybody sees.

WHY IT TESTS `read_document_for_agent` AND NOT ONLY `readable_documents`.
`readable_documents` takes no consumer at all, so "every agent may read it" is true
there by construction and untestable — there is no other agent to be refused as.
`read_document_for_agent` DOES take a `consumer_stage`. It is the one function that
knows who is asking, so it is the one place a stage check could be introduced, and the
only place the read half of this rule can actually be asserted.

THE CONTRAST IS THE POINT. The same document, owned by another stage, is readable here
and NOT publishable to SharePoint. Each half is asserted elsewhere in isolation; a
regression that collapsed the two — making publish follow read, which is the natural
thing to "fix" once you notice they differ — would leave both of those suites green.
"""
from __future__ import annotations

import sys
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.db import get_db_session_superuser  # noqa: E402
from shared.services.artifact_consumption import read_document_for_agent  # noqa: E402
from shared.tools.project_documents import make_document_tools  # noqa: E402
from shared.tools.sharepoint_artifacts import (  # noqa: E402
    _approved_documents, make_sharepoint_tools,
)

pytestmark = pytest.mark.usefixtures("purge_created_orgs")

OWNER = "owner@example.com"

#: The producing stage in every case below, and never the consumer.
PRODUCER_STAGE = "design"
#: A different agent entirely, asking about design's document.
CONSUMER_STAGE = "plan"


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
            "VALUES (:i, :s, 'Read Write Split Test')"
        ), {"i": org, "s": f"rw-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": org})
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'RW Project')"
        ), {"i": proj, "w": bu, "t": org})
        await s.commit()
    yield {"org": org, "project": proj}


async def _document(project, *, stage, status="approved", name="hld.pdf") -> str:
    art = str(_uuid.uuid4())
    approved = status == "approved"
    async with get_db_session_superuser() as s:
        await s.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"),
            {"t": project["org"]})
        await s.execute(text(
            "INSERT INTO artifacts (id, project_id, stage, tenant_id, artifact_type, "
            "  blob_path, approval_status, approved_by, approved_at) "
            "VALUES (CAST(:i AS uuid), CAST(:p AS uuid), :st, CAST(:t AS uuid), "
            "  'document', :bp, :s, :ab, :at)"
        ), {"i": art, "p": project["project"], "st": stage, "t": project["org"],
            "bp": f"{project['org']}/bu/proj/{stage or '_no-agent'}/x/document/{name}",
            "s": status, "ab": OWNER if approved else None,
            "at": datetime.now(timezone.utc) if approved else None})
        await s.commit()
    return art


async def _read_as(project, artifact_id, stage=CONSUMER_STAGE):
    return await read_document_for_agent(
        tenant_id=project["org"], project_id=project["project"],
        artifact_id=artifact_id, consumer_stage=stage,
    )


#: The refusal `read_document_for_agent` gives when the rule forbids the read. Matched
#: on rather than "did it return text", because there is no blob client in a test and
#: EVERY read ends in a refusal here — the question is only which one.
DENIED = "not readable in this project"
UNAPPROVED = "not approved"


# -- reading: the project's, not the stage's ----------------------------------


async def test_another_stages_approved_document_is_readable(project):
    """THE HEADLINE. `plan` asks for `design`'s approved document and is not refused
    on permission grounds.

    It cannot be read to completion — the bytes are in blob storage, which no test has
    — so this asserts the refusal is about the FILE and never about the rule. Getting
    as far as the bytes is proof the permission gate let it through, and it is the only
    proof available without standing up storage.
    """
    doc = await _document(project, stage=PRODUCER_STAGE)

    text_out, note = await _read_as(project, doc)

    assert text_out is None  # no blob client in a test
    assert DENIED not in note, f"a cross-stage approved read was refused: {note}"
    assert UNAPPROVED not in note, note


async def test_an_unapproved_document_is_refused_from_the_same_place(project):
    """NON-VACUITY, and the assertion that makes the one above mean something. If no
    input ever produced a refusal, `DENIED not in note` would pass on everything
    including a genuine breach.
    """
    doc = await _document(project, stage=PRODUCER_STAGE, status="pending")

    text_out, note = await _read_as(project, doc)

    assert text_out is None
    assert UNAPPROVED in note, note


async def test_a_document_outside_this_project_is_still_refused(project):
    """The read is project-wide, not global: `project_id` is still a wall."""
    doc = await _document(project, stage=PRODUCER_STAGE)

    text_out, note = await read_document_for_agent(
        tenant_id=project["org"], project_id=str(_uuid.uuid4()),
        artifact_id=doc, consumer_stage=CONSUMER_STAGE,
    )

    assert text_out is None
    assert "does not exist in this project" in note, note


# -- writing: the agent's own -------------------------------------------------


async def test_the_same_document_is_not_publishable_by_another_agent(project):
    """THE CONTRAST, and the reason this file exists rather than two additions to two
    existing suites.

    Publishing files a document into the business's SharePoint library, under a folder
    the project chose, where people who were not in the chat will read it — and nothing
    in this platform can take it back out again. That is a write to somebody else's
    record, made on the producing stage's behalf, so it stays with the producing stage.
    Reading is the opposite: it costs nothing and leaves no trace outside the evidence
    trail.
    """
    doc = await _document(project, stage=PRODUCER_STAGE)

    _text, note = await _read_as(project, doc)
    publishable = await _approved_documents(
        project["org"], project["project"], CONSUMER_STAGE)

    assert DENIED not in note, note  # readable, as above
    assert [d["id"] for d in publishable] == [], (
        f"{CONSUMER_STAGE} can publish {PRODUCER_STAGE}'s document {doc}")


async def test_the_producing_agent_can_publish_its_own(project):
    """NON-VACUITY: the rule is about WHOSE document it is, not a blanket refusal."""
    doc = await _document(project, stage=PRODUCER_STAGE)

    publishable = await _approved_documents(
        project["org"], project["project"], PRODUCER_STAGE)

    assert [d["id"] for d in publishable] == [doc]


async def test_a_project_wide_document_is_publishable_by_any_agent(project):
    """It belongs to no stage, so there is no other agent for it to belong to."""
    doc = await _document(project, stage=None, name="policy.pdf")

    publishable = await _approved_documents(
        project["org"], project["project"], CONSUMER_STAGE)

    assert [d["id"] for d in publishable] == [doc]


# -- the mechanism that keeps a write with its own agent ----------------------


#: Identity a tool must never accept from the model. `stage` and `agent_id` decide
#: WHOSE documents an operation acts on; `tenant_id` and `project_id` decide whose data
#: it reaches at all.
IDENTITY_ARGS = {"stage", "agent_id", "agent", "consumer_stage", "tenant_id",
                 "project_id", "tenant", "project"}


@pytest.mark.parametrize("tools", [
    pytest.param(make_document_tools(CONSUMER_STAGE), id="documents"),
    pytest.param(
        make_sharepoint_tools(agent_id=CONSUMER_STAGE, stage=CONSUMER_STAGE),
        id="sharepoint"),
])
def test_no_tool_lets_the_model_say_which_agent_it_is(tools):
    """THE ACTUAL ENFORCEMENT, and it is structural rather than a check anywhere.

    Every stage binding is a literal written at the agent's own registration —
    `make_sharepoint_tools(agent_id="design", stage="design")` — closed over by the
    factory and unreachable from the model. Nothing above would hold if a tool took
    `stage` as an argument instead: a prompt could claim to be the Design agent and
    publish its documents, and every permission test in this repo would still pass,
    because the permission logic would be doing exactly what it was told.

    Which makes this the load-bearing test of the write half. It fails the moment
    somebody adds the parameter that would make the rule bypassable — a far more
    likely regression than the rule itself being rewritten.
    """
    assert tools, "no tools to check"
    for t in tools:
        exposed = set(getattr(t, "args", {}) or {})
        leaked = exposed & IDENTITY_ARGS
        assert not leaked, f"{t.name} lets the model choose {sorted(leaked)}"


def test_the_factories_do_bind_an_identity():
    """NON-VACUITY for the test above: the arguments it forbids are ones these tools
    genuinely need, supplied once at registration. A tool set that took no identity
    from anywhere would pass that test while being useless.
    """
    import inspect

    for factory in (make_document_tools, make_sharepoint_tools):
        params = set(inspect.signature(factory).parameters)
        assert params & IDENTITY_ARGS, f"{factory.__name__} binds no identity at all"
