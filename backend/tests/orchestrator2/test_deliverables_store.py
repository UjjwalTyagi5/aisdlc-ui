"""Persisting and reading deliverables.

The tenant tests patch the SESSION FACTORY, never the function under test, and the
fake session EVALUATES the query's where-clause against a store holding the same run
id under two different tenants. Asserting on a mock of the function under test would
prove nothing; this fake hands back the wrong tenant's rows the moment the predicate
is dropped. Same shape as tests/orchestrator2/test_context.py, deliberately.
"""
import datetime as dt
import uuid
from contextlib import asynccontextmanager

import pytest

_RUN = "11111111-1111-1111-1111-111111111111"
_TENANT = "22222222-2222-2222-2222-222222222222"
_OTHER_TENANT = "33333333-3333-3333-3333-333333333333"


def _at(minute: int) -> dt.datetime:
    return dt.datetime(2026, 9, 6, 10, minute, tzinfo=dt.timezone.utc)


class _Row:
    def __init__(self, tenant_id, agent_id, title, created_at, content="body",
                 run_id=_RUN):
        self.id = uuid.uuid4()
        self.run_id = uuid.UUID(run_id)
        self.tenant_id = uuid.UUID(tenant_id)
        self.project_id = None
        self.agent_id = agent_id
        self.kind = "markdown"
        self.title = title
        self.content = content
        self.url = None
        self.language = None
        self.source = None
        self.created_at = created_at


def _predicates(stmt):
    """The (column name, value) pairs of the statement's WHERE clause.

    Read off the real SQLAlchemy expression tree, so a dropped predicate genuinely
    disappears from this list rather than being something the test asserts about a
    mock.
    """
    clause = stmt.whereclause
    if clause is None:
        return []
    parts = getattr(clause, "clauses", None) or [clause]
    out = []
    for part in parts:
        left, right = getattr(part, "left", None), getattr(part, "right", None)
        if left is not None and hasattr(right, "value"):
            out.append((left.name, right.value))
    return out


def _ordering(stmt):
    """The statement's ORDER BY, as (column name, descending) pairs.

    The fake session MUST honour this rather than sorting rows itself. An earlier
    version of this file sorted newest-first unconditionally, and flipping the real
    query to `.asc()` left every test green — while `latest_per_agent`, which takes
    the FIRST row per agent, would then have fed the OLDEST version of a document to
    every downstream agent. A stale PRD handed forward silently is precisely the
    class of defect this suite exists to catch.
    """
    out = []
    for element in getattr(stmt, "_order_by_clauses", ()):
        modifier = getattr(element, "modifier", None)
        column = getattr(element, "element", element)
        name = getattr(column, "name", None)
        if name:
            out.append((name, "desc" in str(modifier)))
    return out


def _fake_factory(monkeypatch, rows, added=None, fail_on_commit=False):
    """Patch the session factory with one that EVALUATES the query's predicates."""
    import shared.db as shared_db

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return self

        def all(self):
            return list(self._items)

    class _Session:
        async def execute(self, stmt):
            preds = _predicates(stmt)
            keep = [
                r for r in rows
                if all(getattr(r, name, None) == value for name, value in preds)
            ]
            # The QUERY's ordering, not this fake's opinion of it. See `_ordering`.
            for name, descending in reversed(_ordering(stmt)):
                keep.sort(key=lambda r: getattr(r, name), reverse=descending)
            return _Result(keep)

        def add(self, obj):
            if added is not None:
                added.append(obj)

        async def commit(self):
            if fail_on_commit:
                raise RuntimeError("disk on fire")

    @asynccontextmanager
    async def _fake(tenant_id):
        yield _Session()

    monkeypatch.setattr(shared_db, "get_db_session_for_tenant", _fake)


# ── reads ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_is_newest_first(monkeypatch):
    from agents_orchestrator.orchestrator2 import deliverables
    _fake_factory(monkeypatch, [
        _Row(_TENANT, "security", "old", _at(0)),
        _Row(_TENANT, "security", "new", _at(30)),
    ])
    out = await deliverables.deliverables_for_run(_RUN, _TENANT)
    assert [d["title"] for d in out] == ["new", "old"]


@pytest.mark.asyncio
async def test_read_refuses_another_tenants_rows(monkeypatch):
    """The whole point of the explicit predicate. RLS is inert in this deployment —
    the app connects as a rolbypassrls superuser — so this predicate is what actually
    stands between tenants today."""
    from agents_orchestrator.orchestrator2 import deliverables
    _fake_factory(monkeypatch, [_Row(_OTHER_TENANT, "security", "theirs", _at(0))])
    assert await deliverables.deliverables_for_run(_RUN, _TENANT) == []


@pytest.mark.asyncio
async def test_read_refuses_another_runs_rows(monkeypatch):
    from agents_orchestrator.orchestrator2 import deliverables
    other_run = "44444444-4444-4444-4444-444444444444"
    _fake_factory(monkeypatch, [
        _Row(_TENANT, "security", "other run", _at(0), run_id=other_run),
    ])
    assert await deliverables.deliverables_for_run(_RUN, _TENANT) == []


@pytest.mark.asyncio
async def test_a_malformed_run_id_reads_nothing_rather_than_raising(monkeypatch):
    from agents_orchestrator.orchestrator2 import deliverables
    _fake_factory(monkeypatch, [_Row(_TENANT, "security", "x", _at(0))])
    assert await deliverables.deliverables_for_run("not-a-uuid", _TENANT) == []


@pytest.mark.asyncio
async def test_latest_per_agent_keeps_only_the_newest_of_each(monkeypatch):
    """Every version is VISIBLE, but only the newest FEEDS an agent — otherwise a
    downstream agent is handed two contradictory PRDs and has to guess."""
    from agents_orchestrator.orchestrator2 import deliverables
    _fake_factory(monkeypatch, [
        _Row(_TENANT, "requirements", "PRD v1", _at(0), content="one"),
        _Row(_TENANT, "requirements", "PRD v2", _at(30), content="two"),
        _Row(_TENANT, "design", "HLD", _at(15), content="hld"),
    ])
    latest = await deliverables.latest_per_agent(_RUN, _TENANT)
    assert set(latest) == {"requirements", "design"}
    assert latest["requirements"]["title"] == "PRD v2"
    assert latest["requirements"]["content"] == "two"


# ── writes ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_appends_and_never_updates(monkeypatch):
    """A re-run must not destroy the document it replaces."""
    from agents_orchestrator.orchestrator2 import deliverables
    added = []
    _fake_factory(monkeypatch, [], added=added)
    body = "x" * 400
    await deliverables.capture("security", body, run_id=_RUN, tenant_id=_TENANT,
                               project_id=None)
    await deliverables.capture("security", body, run_id=_RUN, tenant_id=_TENANT,
                               project_id=None)
    assert len(added) == 2, "the second capture replaced the first instead of appending"
    assert added[0].id != added[1].id, "two versions must not share an id"


@pytest.mark.asyncio
async def test_capture_records_the_project_it_ran_for(monkeypatch):
    """Project scope is load-bearing: the Orchestrator lives inside a project and
    uses that project's models and connectors."""
    from agents_orchestrator.orchestrator2 import deliverables
    added = []
    _fake_factory(monkeypatch, [], added=added)
    project = "55555555-5555-5555-5555-555555555555"
    await deliverables.capture("plan", "x" * 400, run_id=_RUN, tenant_id=_TENANT,
                               project_id=project)
    assert str(added[0].project_id) == project
    assert str(added[0].tenant_id) == _TENANT


@pytest.mark.asyncio
async def test_capture_of_a_short_reply_writes_nothing(monkeypatch):
    from agents_orchestrator.orchestrator2 import deliverables
    added = []
    _fake_factory(monkeypatch, [], added=added)
    out = await deliverables.capture("security", "ok", run_id=_RUN,
                                     tenant_id=_TENANT, project_id=None)
    assert out == [] and added == []


@pytest.mark.asyncio
async def test_a_write_failure_is_typed_not_swallowed(monkeypatch):
    """The CALLER decides a failed capture must not fail the turn. That decision is
    made once, visibly, at the call site — not hidden here behind a bare except."""
    from agents_orchestrator.orchestrator2 import deliverables
    _fake_factory(monkeypatch, [], added=[], fail_on_commit=True)
    with pytest.raises(deliverables.DeliverableWriteError):
        await deliverables.capture("security", "x" * 400, run_id=_RUN,
                                   tenant_id=_TENANT, project_id=None)


# ── pointers ─────────────────────────────────────────────────────────────────


def test_pointers_are_not_versioned_and_carry_stable_ids():
    """The panel de-dupes the Development tree on the literal id `dev-code`; a
    per-turn uuid there would stack a new tree on every turn."""
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    out = pointers_for_run({"repo_url": "https://dev.azure.com/x/_git/y",
                            "pr_url": "https://dev.azure.com/x/_git/y/pullrequest/3"})
    by_id = {p["id"]: p for p in out}
    assert by_id["dev-code"]["kind"] == "code-tree"
    assert by_id["dev-code"]["agent"] == "development"
    assert by_id["dev-pr"]["kind"] == "link"
    assert by_id["dev-pr"]["url"].endswith("/pullrequest/3")


def test_no_pointers_without_a_pulled_repo():
    """An empty code tree implies the agent pulled something. It must not."""
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    assert pointers_for_run(None) == []
    assert pointers_for_run({}) == []


def test_an_agent_that_generated_files_gets_a_file_tree():
    """Each agent's own frontend quirks carry over — a stage that wrote files to disk
    surfaces them under its own heading, the way Development surfaces its clone."""
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    out = pointers_for_run(None, {"testing", "documentation"})
    by_id = {p["id"]: p for p in out}
    assert by_id["testing-files"]["kind"] == "file-tree"
    assert by_id["testing-files"]["source"] == "testing"
    assert by_id["documentation-files"]["agent"] == "documentation"


def test_development_does_not_get_a_second_tree_over_the_same_clone():
    from agents_orchestrator.orchestrator2.deliverables import pointers_for_run
    out = pointers_for_run({"repo_url": "https://x"}, {"development"})
    assert [p["id"] for p in out] == ["dev-code"]
