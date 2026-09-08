"""Uploading a file to an Orchestrator run, and the key the two halves must agree on.

WHY A RUN-SCOPED ROUTE RATHER THAN THE EXISTING CONVERSATION ONE.
`POST /conversations/{session_id}/attachments` already stores chat attachments, and the
Orchestrator's session id IS its run id — but that route authorises through
`conversation_service.session_owner`, and the Orchestrator's conversation row is created
by the SOCKET on the first turn (`sessions.ensure_session`). Attaching a file before
sending the first message would therefore 404 against a run that genuinely exists. This
route authorises through `_get_run_or_404` instead — the same tenant-and-scope chokepoint
the run's other sixteen routes use — so it holds on the very first turn.

THE TEST THAT ACTUALLY MATTERS is
`test_an_uploaded_file_is_found_by_the_reader_that_feeds_the_agent`. Upload and ingestion
are two halves of one key, `(user_id, run_id)`, written in two different modules. Tested
apart, both halves pass while the file lands somewhere the agent never looks — which on
screen is a chip in the composer and an agent that says no document was attached, the
exact failure this feature exists to prevent. So that test writes through the real route
and reads back through the real `attachment_context`, with nothing faked in between.
"""
import ast
import inspect
import io
import textwrap

import pytest
from fastapi import HTTPException, UploadFile

from shared.services import attachment_store

_RUN = "11111111-1111-1111-1111-111111111111"
_TENANT = "22222222-2222-2222-2222-222222222222"
_USER = "33333333-3333-3333-3333-333333333333"


def _code_of(func) -> str:
    """A function's source with docstrings stripped.

    NOT optional — see `test_deliverables_api._code_of`. A source assertion that can be
    satisfied by a comment is how a route kept its green test after its actual scoping
    call was removed.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return ast.unparse(tree)


class _Request:
    class state:
        tenant_id = _TENANT
        user_id = _USER


class _Run:
    id = _RUN
    project_id = None
    development_artifacts = None
    stage = None


@pytest.fixture
def route(monkeypatch, tmp_path):
    """The real route module, with the run resolved and the store rooted in tmp."""
    from shared.routers import runs

    async def _get_run(db, run_id, tenant_id, *, request):
        return _Run()

    monkeypatch.setattr(runs, "_get_run_or_404", _get_run)
    monkeypatch.setattr(attachment_store, "_FILES_ROOT", tmp_path / "files")
    return runs


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


# ── registration and scoping ────────────────────────────────────────────────


def test_both_endpoints_are_registered():
    from shared.routers.runs import runs_router

    methods = {
        m
        for r in runs_router.routes
        if r.path == "/{run_id}/attachments"
        for m in r.methods
    }
    assert {"POST", "GET"} <= methods


@pytest.mark.parametrize("name", ["upload_run_attachments", "get_run_attachments"])
def test_the_endpoints_resolve_the_run_through_the_scoped_chokepoint(name):
    """`_get_run_or_404` carries BOTH the tenant filter and the caller-scope check.

    Without it any authenticated caller could write files into — or read the file list
    of — another tenant's run by guessing a uuid. Upload is the more dangerous half:
    it is the only route in this module that WRITES to disk.
    """
    from shared.routers import runs

    src = _code_of(getattr(runs, name))
    assert "_get_run_or_404" in src
    assert "request.state.tenant_id" in src


@pytest.mark.parametrize("name", ["upload_run_attachments", "get_run_attachments"])
def test_the_endpoints_never_reach_for_the_rls_bypassing_session(name):
    from shared.routers import runs

    src = _code_of(getattr(runs, name))
    assert "get_db_session_superuser" not in src


# ── the key the two halves share ────────────────────────────────────────────


async def test_an_uploaded_file_is_found_by_the_reader_that_feeds_the_agent(route):
    """THE SEAM. Written through the real route, read through the real reader.

    `(user_id, run_id)` is one key written in two modules. If the route filed uploads
    under anything else — the project, the tenant, a conversation id — both halves
    would still pass their own tests while the user watched a chip appear in the
    composer and the agent answer that no document was attached.
    """
    from agents_orchestrator.orchestrator2 import attachments

    await route.upload_run_attachments(
        _RUN, _Request(), files=[_upload("brd.md", b"# BRD\n\nApple Pay is required.")],
        db=None,
    )

    context = await attachments.attachment_context(_RUN, user_id=_USER)
    assert "Apple Pay" in context, (
        "the uploader and the reader disagree about where a run's attachments live"
    )


async def test_the_file_is_filed_under_the_authenticated_caller(route):
    """The uploader is `request.state.user_id`, never anything in the request body.

    The socket reads attachments under the TICKET's user, so a route that let a caller
    name the owner would be a way to plant a document into somebody else's prompt.
    """
    from agents_orchestrator.orchestrator2 import attachments

    await route.upload_run_attachments(
        _RUN, _Request(), files=[_upload("mine.md", b"the caller's document")], db=None,
    )

    assert await attachments.attachment_context(_RUN, user_id=_USER) != ""
    assert await attachments.attachment_context(_RUN, user_id="someone-else") == "", (
        "an upload must not be readable under another user's id"
    )


async def test_the_upload_returns_refs_the_composer_can_render(route):
    out = await route.upload_run_attachments(
        _RUN, _Request(), files=[_upload("brd.md", b"content")], db=None,
    )
    assert [a["name"] for a in out["attachments"]] == ["brd.md"]
    assert out["attachments"][0]["url"].startswith("/generated/")


async def test_the_listing_returns_what_was_uploaded(route):
    """Reopening a conversation must show the files the agent is still being given.

    Without this the chips vanish on reload while the attachments keep reaching every
    turn — the user cannot see what the agent is being told, which is its own kind of
    lie.
    """
    await route.upload_run_attachments(
        _RUN, _Request(), files=[_upload("brd.md", b"content")], db=None,
    )
    out = await route.get_run_attachments(_RUN, _Request(), db=None)
    assert [a["name"] for a in out["attachments"]] == ["brd.md"]


# ── refusals ────────────────────────────────────────────────────────────────


async def test_a_rejected_file_type_is_a_400_not_a_500(route):
    """`AttachmentError` is a user mistake with a readable message. Letting it escape
    as a 500 turns "we do not accept .exe" into "something went wrong"."""
    with pytest.raises(HTTPException) as excinfo:
        await route.upload_run_attachments(
            _RUN, _Request(), files=[_upload("payload.exe", b"MZ")], db=None,
        )
    assert excinfo.value.status_code == 400
    assert "exe" in str(excinfo.value.detail).lower()


async def test_a_rejected_file_does_not_leave_its_siblings_half_written(route):
    """One bad file in a multi-file upload refuses the whole request.

    The alternative — storing what parsed and 400-ing anyway — leaves the run holding
    files the user was told were not accepted, and the agent then reads them.
    """
    from agents_orchestrator.orchestrator2 import attachments

    with pytest.raises(HTTPException):
        await route.upload_run_attachments(
            _RUN, _Request(),
            files=[_upload("ok.md", b"fine"), _upload("bad.exe", b"MZ")],
            db=None,
        )
    assert await attachments.attachment_context(_RUN, user_id=_USER) == "", (
        "a refused upload must not leave a partially-stored batch on the run"
    )
