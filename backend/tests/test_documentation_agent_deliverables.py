"""The Documentation agent produces handover and KT material, under the same approval.

WHAT WAS WRONG, and none of it raised an error. `save_document` wrote to local disk and
to `session.generated_docs` — an in-memory list that dies with the session — and never
created an `artifacts` row. So a document this agent produced could not be approved,
could not be read by another agent, and did not survive a restart. Meanwhile
`publish_to_sharepoint` and `publish_to_confluence` filed that same unreviewed list into
the business's document library and wiki. It was the only agent that could do that.

Worse, the shared document tools fanned out to this agent were INERT: they read the
tenant and project from `ws_helper` contextvars, and this was the one agent entry point
that never set them. `list_project_documents` answered "no project context in this
session" every time. Nothing failed — it just always said there was nothing there, which
is indistinguishable from a project with no documents.

That is the shape of every bug in this file: a silence rather than an error. So the
tests assert the wiring itself, not just the behaviour that depends on it.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.documentation_agent.tools import doc_tools  # noqa: E402


# -- the wiring the tools silently depend on ----------------------------------


def test_the_ws_handler_sets_the_tool_context():
    """THE INERT-TOOL BUG, asserted at its cause.

    `chat_artifacts.register_generated_file` and `shared/tools/project_documents` both
    read the tenant and project from `config.ws_helper` contextvars. Every other agent's
    entry point sets them; this one did not, so its document tools returned "no project
    context" and its generated files were never recorded.

    Source inspection rather than a live socket: the failure is a missing call, and the
    call has no observable effect until some other tool reads the contextvar a turn
    later. There is nothing to assert on at runtime that would not pass with the call
    removed.
    """
    from agents_orchestrator.documentation_agent import documentation_standalone_api as api

    src = inspect.getsource(api._process_ws_message)

    assert "set_tenant_id(" in src, "the documentation agent never sets the tenant"
    assert "set_project_id(" in src, "the documentation agent never sets the project"


def test_the_agent_binds_the_approved_only_publisher_and_not_the_other_one():
    """`publish_to_sharepoint` was DELETED, not merely unbound. Leaving it importable
    would let it be rebound by accident and quietly reopen the hole."""
    from agents_orchestrator.documentation_agent.agents.compiler import _tools

    names = {getattr(t, "name", "") for t in _tools}
    assert "publish_approved_to_sharepoint" in names
    assert "publish_to_sharepoint" not in names
    assert not hasattr(doc_tools, "publish_to_sharepoint")


def test_save_document_records_an_artifact():
    """The whole point of the change: a generated document joins the project's record
    as a pending artifact. Asserted on the source of `save_document` because the call
    is best-effort and swallows its own failure — a mocked-out run would pass whether
    or not the call was there."""
    src = inspect.getsource(doc_tools.save_document.coroutine)

    # THE CALL, not the import. `"register_generated_file" in src` also matched the
    # import line, so replacing the call with `pass` left this test green — which a
    # mutation run caught and is exactly the false confidence a source-level assertion
    # invites.
    assert "await register_generated_file(" in src
    assert 'stage="documentation"' in src, "recorded against the wrong stage"
    assert "PENDING" in src, "the reply must say the document is awaiting approval"


# -- the handover deliverable -------------------------------------------------


COMPLETE_HANDOVER = {
    "title": "Handover — Payments", "filename": "handover.md",
    "scope": "The payments service. Excludes the fraud engine.",
    "systems_and_access": "repo x, prod dashboard y; access granted by the platform team",
    "operational_runbook": "deployed by pipeline z; roll back with the previous tag",
    "in_flight_work": "PR 41, half-done idempotency keys",
    "known_risks": "the signing certificate expires in March",
    "key_contacts": "Ana for payments, the on-call rota for incidents",
}


@pytest.mark.parametrize("omit", sorted(COMPLETE_HANDOVER.keys() - {"title", "filename"}))
async def test_a_handover_missing_any_required_section_is_refused(omit):
    """A HANDOVER IS READ ONCE, UNDER PRESSURE, by somebody who cannot tell an omitted
    section from one that did not apply. Saving it with holes is worse than refusing:
    the receiving team finds the gap in production.

    Every section is parametrised rather than testing one, because the failure mode is a
    single field quietly slipping out of the required list."""
    args = dict(COMPLETE_HANDOVER)
    args[omit] = "   "  # whitespace: the check must not be a truthiness test

    result = await doc_tools.save_handover_document.ainvoke(args)

    assert result.startswith("ERROR"), result
    assert omit in result, f"the refusal does not say which section is missing: {result}"


async def test_a_complete_handover_is_saved_with_every_section(monkeypatch):
    """NON-VACUITY. The refusals above would all pass on a tool that refused
    everything."""
    captured = {}

    # The MODULE ATTRIBUTE is replaced, not a field on the tool: `@tool` returns a
    # pydantic StructuredTool, and `save_handover_document` resolves `save_document`
    # as a module global at call time.
    class _Stub:
        async def ainvoke(self, payload):
            captured.update(payload)
            return "Saved."

    monkeypatch.setattr(doc_tools, "save_document", _Stub())

    result = await doc_tools.save_handover_document.ainvoke(dict(COMPLETE_HANDOVER))

    assert not result.startswith("ERROR"), result
    assert captured["doc_type"] == "handover"
    body = captured["markdown_contents"]
    for heading in ("Scope of this handover", "Systems and access", "Operating the system",
                    "Work in flight", "Known risks", "Key contacts and escalation"):
        assert heading in body, f"missing section: {heading}"
    # The optional section is genuinely optional and absent when not supplied.
    assert "Recommended first 90 days" not in body


# -- the KT deliverable -------------------------------------------------------


COMPLETE_KT = {
    "title": "KT — Payments", "filename": "kt.md",
    "what_it_does": "takes card payments for the lending journey",
    "architecture_tour": "api/ -> service/ -> ledger/, see backend/payments",
    "environments_and_setup": "dev and prod; `make run` after `uv sync`",
    "common_tasks": "add a payment method; replay a failed settlement",
    "where_things_live": "the repo, the payments board, #payments",
}


@pytest.mark.parametrize("omit", sorted(COMPLETE_KT.keys() - {"title", "filename"}))
async def test_a_kt_document_missing_any_required_section_is_refused(omit):
    """KT material written from assumption is what makes a new joiner stop trusting the
    docs — and the reader is the one person least able to spot the gap."""
    args = dict(COMPLETE_KT)
    args[omit] = ""

    result = await doc_tools.save_kt_document.ainvoke(args)

    assert result.startswith("ERROR"), result
    assert omit in result, result


async def test_a_complete_kt_document_is_saved(monkeypatch):
    """NON-VACUITY, and it pins the doc_type: `kt` and `handover` are separate types
    because the reader is different — a team taking on accountability versus a person
    joining the existing one — and collapsing them is the obvious wrong simplification."""
    captured = {}

    class _Stub:
        async def ainvoke(self, payload):
            captured.update(payload)
            return "Saved."

    monkeypatch.setattr(doc_tools, "save_document", _Stub())

    result = await doc_tools.save_kt_document.ainvoke(dict(COMPLETE_KT))

    assert not result.startswith("ERROR"), result
    assert captured["doc_type"] == "kt"
    for heading in ("What this system does", "Architecture tour",
                    "Environments and local setup", "Common tasks", "Where things live"):
        assert heading in captured["markdown_contents"]


def test_the_two_deliverables_are_distinct_tools():
    """Their docstrings are what the model chooses between, so each has to say when the
    OTHER one applies. A tool that only describes itself gets picked by keyword."""
    assert "save_kt_document" in doc_tools.save_handover_document.description
    assert "save_handover_document" in doc_tools.save_kt_document.description


def test_the_new_types_are_accepted_by_save_document():
    """`save_document` silently downgrades an unknown doc_type to "custom". Both new
    deliverables would have been filed as "Doc" in the user's list — not an error, just
    a label quietly losing its meaning."""
    src = inspect.getsource(doc_tools.save_document.coroutine)

    assert '"handover"' in src and '"kt"' in src


# -- publishing stays behind approval ----------------------------------------


def test_confluence_publishing_is_gated_on_approval():
    """The company wiki is read by people who were not in the conversation and cannot
    tell a draft from a signed-off document. This published straight from the in-memory
    session list, which made approval mean nothing on the destination most likely to be
    read from outside the project.

    It also FAILS CLOSED: if the approved set cannot be read, nothing is published.
    Publishing unreviewed documents because a database call failed is the outcome the
    gate exists to prevent."""
    src = inspect.getsource(doc_tools.publish_to_confluence.coroutine)

    assert "_approved_documents" in src
    assert "approved_names" in src
    # The refusal names the blocked documents rather than reporting nothing to publish.
    assert "blocked" in src
