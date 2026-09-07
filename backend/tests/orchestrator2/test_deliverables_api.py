"""The deliverables read endpoint.

It is NEW READ SURFACE for documents that were previously unreachable over HTTP, so
it is tested for the access control its neighbours have — not merely for returning
rows. `assert_all_routes_protected` covers REST routes (it skips only WebSocket ones),
and `runs_router` is mounted with a router-level view dependency, so this endpoint
inherits the same gate as `/runs/{id}/artifacts`. What it does NOT inherit, and what
is checked here, is the tenant-and-scope resolution: that is per-route code.
"""
import ast
import inspect
import textwrap

import pytest


def _code_of(func) -> str:
    """A function's source with its docstring stripped.

    NOT optional. The first version of this file grepped the raw source for
    `_get_run_or_404`, and the endpoint's own docstring says "Resolved through
    `_get_run_or_404`" — so replacing the actual call with an unscoped query left
    every test green. The prose satisfied the check the code had stopped satisfying,
    which is the defect this branch has now produced nine times. A source-level
    assertion must never be able to pass on a comment.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return ast.unparse(tree)


def _route():
    from shared.routers.runs import runs_router
    return next(r for r in runs_router.routes if r.path == "/{run_id}/deliverables")


def test_endpoint_is_registered():
    assert _route() is not None


def test_endpoint_resolves_the_run_through_the_scoped_chokepoint():
    """`_get_run_or_404` carries BOTH the tenant filter and the caller-scope check.
    Its own docstring calls itself the chokepoint for sixteen routes and says an
    optional check here is one that will eventually be forgotten at one call site.
    An endpoint that read by run id alone would let any authenticated caller pull
    another tenant's documents by guessing a uuid."""
    from shared.routers import runs

    src = _code_of(runs.get_run_deliverables)
    assert "_get_run_or_404" in src
    assert "request.state.tenant_id" in src


def test_endpoint_never_reaches_for_the_rls_bypassing_session():
    """`get_db_session_superuser` bypasses row-level security unconditionally.
    `copilot_api.py` has been caught reading through one with no tenant filter twice."""
    from shared.routers import runs

    src = _code_of(runs.get_run_deliverables)
    assert "get_db_session_superuser" not in src


def test_the_tenant_it_reads_with_is_the_callers_not_the_runs():
    """Subtle and worth pinning: passing `run.tenant_id` into the deliverables read
    would make the read agree with whatever row came back, so a scoping bug upstream
    would be laundered into a successful cross-tenant read rather than an empty one."""
    from shared.routers import runs

    src = _code_of(runs.get_run_deliverables)
    assert "deliverables_for_run(str(run.id), str(tenant_id))" in src, (
        "the read must be scoped to the CALLER's tenant, not the run's"
    )


def test_it_is_mounted_under_the_routers_view_dependency():
    """The router-level gate is what protects every read route in this module; a
    route added outside it would be reachable unauthenticated."""
    import process_api

    src = inspect.getsource(process_api)
    assert 'app.include_router(runs_router, prefix="/runs"' in src
    assert "_VIEW_DEP" in src


@pytest.mark.asyncio
async def test_pointers_are_returned_alongside_stored_rows(monkeypatch):
    """The Development code tree and the PR link are synthesized, not stored, so a
    read that returned only table rows would show a run that pulled a repo no tree
    at all — indistinguishable from a pull that failed."""
    from shared.routers import runs

    class _Run:
        id = "11111111-1111-1111-1111-111111111111"
        project_id = None
        development_artifacts = {"repo_url": "https://dev.azure.com/x/_git/y"}

    async def _get_run(db, run_id, tenant_id, *, request):
        return _Run()

    async def _stored(run_id, tenant_id):
        return [{"id": "d1", "agent": "security", "kind": "markdown",
                 "title": "Security Report", "content": "c"}]

    async def _no_files(run_id, stage, **kwargs):
        return None

    monkeypatch.setattr(runs, "_get_run_or_404", _get_run)
    monkeypatch.setattr(runs, "_run_stage_output_dir", _no_files)
    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.deliverables.deliverables_for_run", _stored
    )

    class _Request:
        class state:
            tenant_id = "22222222-2222-2222-2222-222222222222"

    out = await runs.get_run_deliverables(_Run.id, _Request(), db=None)
    ids = [d["id"] for d in out["deliverables"]]
    assert "d1" in ids, "stored deliverables must be returned"
    assert "dev-code" in ids, "the code tree pointer must be synthesized on read"


# ── the shared output directory reaches the panel once ───────────────────────
#
# `plan`, `design` and `testing` all resolve `files/<user>/orchestrator/<run>/output`.
# Mapping all three (so the Project Manager's delivery plan would stop being invisible)
# made the endpoint emit the identical files three times, once per heading — the
# Testing agent's `test_plan.xlsx` appearing under Design.
#
# Found by mutation: `_dedupe_by_directory` was tested directly and the ENDPOINT was
# free to ignore it.


def _endpoint_fixture(monkeypatch, *, stage, shared_dir, tmp_path):
    from shared.routers import runs

    class _Run:
        id = "11111111-1111-1111-1111-111111111111"
        project_id = None
        development_artifacts = None

    _Run.stage = stage

    async def _get_run(db, run_id, tenant_id, *, request):
        return _Run()

    async def _stored(run_id, tenant_id):
        return []

    async def _dirs(run_id, stage_, **kwargs):
        # Exactly what the real resolver does: these three share one directory.
        return shared_dir if stage_ in ("plan", "design", "testing") else None

    monkeypatch.setattr(runs, "_get_run_or_404", _get_run)
    monkeypatch.setattr(runs, "_run_stage_output_dir", _dirs)
    monkeypatch.setattr(
        "agents_orchestrator.orchestrator2.deliverables.deliverables_for_run", _stored
    )

    class _Request:
        class state:
            tenant_id = "22222222-2222-2222-2222-222222222222"

    return runs, _Run, _Request


@pytest.mark.asyncio
async def test_one_shared_directory_yields_one_tree_from_the_endpoint(
    monkeypatch, tmp_path,
):
    """End to end through `get_run_deliverables`, with a real directory on disk so the
    `isdir`/`listdir` guard runs for real."""
    shared_dir = tmp_path / "output"
    shared_dir.mkdir()
    (shared_dir / "test_plan.xlsx").write_text("x", encoding="utf-8")

    runs, _Run, _Request = _endpoint_fixture(
        monkeypatch, stage="testing", shared_dir=str(shared_dir), tmp_path=tmp_path,
    )
    out = await runs.get_run_deliverables(_Run.id, _Request(), db=None)
    trees = [d for d in out["deliverables"] if d["kind"] == "file-tree"]
    assert len(trees) == 1, (
        f"the same directory was shown {len(trees)} times: "
        + ", ".join(t["agent"] for t in trees)
    )


@pytest.mark.asyncio
async def test_the_shared_tree_is_filed_under_the_agent_the_run_is_for(
    monkeypatch, tmp_path,
):
    """Without this the winner is whichever stage sorts first, so a run opened for the
    Project Manager files its delivery plan under Design."""
    shared_dir = tmp_path / "output"
    shared_dir.mkdir()
    (shared_dir / "Delivery_Plan.pdf").write_text("x", encoding="utf-8")

    runs, _Run, _Request = _endpoint_fixture(
        monkeypatch, stage="plan", shared_dir=str(shared_dir), tmp_path=tmp_path,
    )
    out = await runs.get_run_deliverables(_Run.id, _Request(), db=None)
    trees = [d for d in out["deliverables"] if d["kind"] == "file-tree"]
    assert [t["agent"] for t in trees] == ["plan"]
