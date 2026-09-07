"""The Development agent's clone and PR reach the run row.

FOUND IN THE LIVE UI. Signed in, opened the Orchestrator on project `reall`, asked it
to pull code from ADO, answered its questions, and watched it reply:

    ✅ Cloned Company successfully. Here are the branches: ...

The clone was genuinely on disk —
`files/<user>/orchestrator/<run_id>/project/` held `.git` and the checked-out tree —
and `_run_dev_work_dir` resolved it. But the run row's `development_artifacts` was
`None`, and stayed `None`.

`grep -rn "development_artifacts" agents_orchestrator/orchestrator2/` returned NOTHING.
orchestrator2 never wrote it. The standalone wrapper it replaced did
(`development_agent_api._persist_pr_to_run`), so this is the same shape of gap as the
connector binding, the MCP tools and the per-run contextvars: state the standalone
`*_agent_api.py` provided that `orchestrator2` skipped.

WHAT IT COSTS. `deliverables.pointers_for_run` emits the `dev-code` ("Repository
code") and `dev-pr` ("Pull request") pointers ONLY when `dev_artifacts["repo_url"]` /
`["pr_url"]` are set. With the column `None`, neither can ever fire for an
orchestrator2 run — the panel cannot link the pulled repo or the raised PR, no matter
how well the clone worked.
"""
import uuid
from contextlib import asynccontextmanager

import pytest


_RUN = "9de55574-d9ca-44c5-8c2f-3b1ad04006f0"
_TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"


class _Row:
    def __init__(self):
        self.id = uuid.UUID(_RUN)
        self.tenant_id = uuid.UUID(_TENANT)
        self.development_artifacts = None


def _fake_factory(monkeypatch, row):
    """Patch the session factory, and let the row be mutated as the real one is."""
    import shared.db as shared_db

    class _Result:
        def __init__(self, item):
            self._item = item

        def scalars(self):
            return self

        def first(self):
            return self._item

    class _Session:
        def __init__(self):
            self.committed = False

        async def execute(self, stmt):
            return _Result(row)

        async def commit(self):
            self.committed = True

    session = _Session()

    @asynccontextmanager
    async def _fake(tenant_id):
        yield session

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", _fake)
    return session


def _dev_session(**fields):
    """Stand-in for the Development agent's in-memory session."""
    class _Artifacts:
        def __init__(self):
            self.repo_url = fields.get("repo_url")
            self.branch_name = fields.get("branch_name")
            self.pr_url = fields.get("pr_url")

        def model_dump(self):
            return {"repo_url": self.repo_url, "branch_name": self.branch_name,
                    "pr_url": self.pr_url}

    class _Session:
        dev_artifacts = _Artifacts()

    return _Session()


@pytest.mark.asyncio
async def test_a_clone_is_recorded_on_the_run(monkeypatch):
    """The whole bug: the agent cloned, and the run row learned nothing."""
    from agents_orchestrator.orchestrator2 import dev_artifacts as da

    row = _Row()
    _fake_factory(monkeypatch, row)
    monkeypatch.setattr(
        da, "_dev_session",
        lambda run_id: _dev_session(repo_url="https://dev.azure.com/x/_git/Company",
                                    branch_name="main"),
    )

    await da.persist(_RUN, tenant_id=_TENANT)
    assert row.development_artifacts, "the clone was not recorded on the run"
    assert row.development_artifacts["repo_url"].endswith("/Company")
    assert row.development_artifacts["branch_name"] == "main"


@pytest.mark.asyncio
async def test_the_recorded_artifacts_produce_the_panel_pointers(monkeypatch):
    """Ties this to what the user actually sees. Recording a shape
    `pointers_for_run` does not read would be a write nobody benefits from."""
    from agents_orchestrator.orchestrator2 import dev_artifacts as da
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run

    row = _Row()
    _fake_factory(monkeypatch, row)
    monkeypatch.setattr(
        da, "_dev_session",
        lambda run_id: _dev_session(repo_url="https://dev.azure.com/x/_git/Company",
                                    pr_url="https://dev.azure.com/x/_git/Company/pullrequest/7"),
    )

    await da.persist(_RUN, tenant_id=_TENANT)
    ids = {p["id"] for p in pointers_for_run(row.development_artifacts)}
    assert ids == {"dev-code", "dev-pr"}


@pytest.mark.asyncio
async def test_nothing_is_written_when_the_agent_pulled_nothing(monkeypatch):
    """An empty `development_artifacts` would make `pointers_for_run` emit a code tree
    over a clone that does not exist — an empty tree reads as a pull that FAILED,
    which is worse than no tree at all."""
    from agents_orchestrator.orchestrator2 import dev_artifacts as da

    row = _Row()
    session = _fake_factory(monkeypatch, row)
    monkeypatch.setattr(da, "_dev_session", lambda run_id: _dev_session())

    await da.persist(_RUN, tenant_id=_TENANT)
    assert row.development_artifacts is None
    assert not session.committed, "an empty write still cost a commit"


@pytest.mark.asyncio
async def test_an_empty_pull_never_opens_a_database_session(monkeypatch):
    """Pins the early return, which the no-op check further down otherwise makes look
    redundant — mutation showed removing it changed no observable outcome.

    It is not redundant: EVERY agent's turn calls `persist`, and only Development ever
    has anything to record. Without the early return, all nine agents pay a
    tenant-scoped session and a SELECT on every turn to discover there is nothing to
    write.
    """
    import shared.db as shared_db
    from agents_orchestrator.orchestrator2 import dev_artifacts as da

    opened = []

    @asynccontextmanager
    async def _counting(tenant_id):
        opened.append(tenant_id)
        raise AssertionError("a database session was opened for an empty pull")
        yield  # pragma: no cover

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", _counting)
    monkeypatch.setattr(da, "_dev_session", lambda run_id: _dev_session())

    await da.persist(_RUN, tenant_id=_TENANT)
    assert opened == []


@pytest.mark.asyncio
async def test_a_later_turn_does_not_erase_what_an_earlier_one_recorded(monkeypatch):
    """A conversation clones once and raises a PR several turns later. Replacing the
    whole column each turn would drop `repo_url` the moment a turn knew only the PR."""
    from agents_orchestrator.orchestrator2 import dev_artifacts as da

    row = _Row()
    _fake_factory(monkeypatch, row)

    monkeypatch.setattr(
        da, "_dev_session",
        lambda run_id: _dev_session(repo_url="https://dev.azure.com/x/_git/Company",
                                    branch_name="main"),
    )
    await da.persist(_RUN, tenant_id=_TENANT)

    # A later turn: the session now knows the PR. `repo_url` must survive.
    monkeypatch.setattr(
        da, "_dev_session",
        lambda run_id: _dev_session(pr_url="https://dev.azure.com/x/_git/Company/pullrequest/7"),
    )
    await da.persist(_RUN, tenant_id=_TENANT)

    assert row.development_artifacts["repo_url"].endswith("/Company")
    assert row.development_artifacts["branch_name"] == "main"
    assert row.development_artifacts["pr_url"].endswith("/pullrequest/7")


@pytest.mark.asyncio
async def test_a_missing_session_is_not_an_error(monkeypatch):
    """Every agent's turn calls this; only Development has a session to read. An
    exception here would fail turns for the other eight."""
    from agents_orchestrator.orchestrator2 import dev_artifacts as da

    row = _Row()
    _fake_factory(monkeypatch, row)

    def _boom(run_id):
        raise KeyError(run_id)

    monkeypatch.setattr(da, "_dev_session", _boom)
    await da.persist(_RUN, tenant_id=_TENANT)  # must not raise
    assert row.development_artifacts is None


@pytest.mark.asyncio
async def test_the_write_is_scoped_to_the_runs_tenant(monkeypatch):
    """RLS is inert in this deployment — the app connects as a rolbypassrls
    superuser — so the tenant-scoped session is what actually stands between tenants.
    """
    import shared.db as shared_db
    from agents_orchestrator.orchestrator2 import dev_artifacts as da

    seen = {}

    @asynccontextmanager
    async def _fake(tenant_id):
        seen["tenant"] = tenant_id

        class _S:
            async def execute(self, stmt):
                class _R:
                    def scalars(self_inner):
                        return self_inner

                    def first(self_inner):
                        return None
                return _R()

            async def commit(self):
                pass
        yield _S()

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", _fake)
    monkeypatch.setattr(
        da, "_dev_session",
        lambda run_id: _dev_session(repo_url="https://dev.azure.com/x/_git/Company"),
    )
    await da.persist(_RUN, tenant_id=_TENANT)
    assert seen["tenant"] == _TENANT


# ── the turn actually calls it ───────────────────────────────────────────────
#
# `persist` being correct is worth nothing if `dispatch` never reaches it — the same
# gap this module exists to close, one layer up. Mutation found it: deleting the call
# from `dispatch.py` broke no test.
#
# Driven through the registry-substitution harness the other dispatch tests use, so
# the REAL `run_agent` path runs. An earlier version of this file stubbed a
# `dispatch._run_graph` that does not exist, which quietly asserted nothing.


class _ScriptedGraph:
    def __init__(self, messages):
        self._messages = messages

    async def astream(self, state, stream_mode=None, config=None):
        for message in self._messages:
            yield (message, {})


class _FailingGraph:
    async def astream(self, state, stream_mode=None, config=None):
        raise RuntimeError("the provider refused")
        yield  # pragma: no cover — makes this an async generator


@pytest.fixture()
def _stub_model_resolution(monkeypatch):
    from agents_orchestrator.orchestrator2 import dispatch
    from shared.services import model_resolver as mr

    async def _fake_resolve(tenant_id, requested_model_id=None, **kwargs):
        return mr.ResolvedModel(
            provider="anthropic", litellm_provider="anthropic", model="m",
            api_key="k", base_url=None, alias="tenant:t1:p1",
        )

    monkeypatch.setattr(dispatch, "resolve_model_for_run", _fake_resolve)
    mr.set_resolved_model(None)
    mr.set_run_project(None)
    yield
    mr.set_resolved_model(None)
    mr.set_run_project(None)


async def _turn(monkeypatch, *, graph):
    """One `development` turn against `graph`."""
    from agents_orchestrator.orchestrator2 import dispatch
    from agents_orchestrator.orchestrator2 import registry as reg

    monkeypatch.setitem(
        reg.REGISTRY, "development",
        reg.AgentCapability(
            agent_id="development",
            load_graph=lambda: graph,
            load_prompt=lambda: "SYS",
            mode="stream",
        ),
    )

    async def _no_capture(*args, **kwargs):
        return []

    monkeypatch.setattr(dispatch.deliverables, "capture", _no_capture)
    return [e async for e in dispatch.run_agent(
        "development", text="pull the code", run_id=_RUN, tenant_id=_TENANT,
        model_id=None, offering_id=None, project_id=None, user_id="u1",
        context="", reason="r")]


@pytest.mark.asyncio
async def test_a_completed_turn_records_the_development_artifacts(
    monkeypatch, _stub_model_resolution,
):
    from agents_orchestrator.orchestrator2 import dispatch
    from langchain_core.messages import AIMessageChunk

    calls = []

    async def _fake_persist(run_id, *, tenant_id):
        calls.append((run_id, tenant_id))
        return None

    monkeypatch.setattr(dispatch.dev_artifacts, "persist", _fake_persist)
    await _turn(monkeypatch, graph=_ScriptedGraph([AIMessageChunk(content="cloned")]))

    assert calls == [(_RUN, _TENANT)], (
        "a completed turn did not record the Development agent's clone"
    )


@pytest.mark.asyncio
async def test_a_failed_turn_records_nothing(monkeypatch, _stub_model_resolution):
    """Same rule the deliverable capture follows: a turn that did not complete has
    produced nothing to point at."""
    from agents_orchestrator.orchestrator2 import dispatch

    calls = []

    async def _fake_persist(run_id, *, tenant_id):
        calls.append(run_id)

    monkeypatch.setattr(dispatch.dev_artifacts, "persist", _fake_persist)
    events = await _turn(monkeypatch, graph=_FailingGraph())

    assert any(e["type"] == "error" for e in events), "the turn was meant to fail"
    assert calls == []


@pytest.mark.asyncio
async def test_a_persist_failure_does_not_fail_the_turn(
    monkeypatch, _stub_model_resolution,
):
    """`persist` promises never to raise, but the CALL SITE must not rest on that
    promise — the guarantee has to hold at the layer that would lose the turn."""
    from agents_orchestrator.orchestrator2 import dispatch
    from langchain_core.messages import AIMessageChunk

    async def _boom(run_id, *, tenant_id):
        raise RuntimeError("the database is on fire")

    monkeypatch.setattr(dispatch.dev_artifacts, "persist", _boom)
    events = await _turn(monkeypatch, graph=_ScriptedGraph([AIMessageChunk(content="x")]))

    assert events[-1]["type"] == "stream_end", (
        "a failed artifact write cost the user the end of their turn"
    )
