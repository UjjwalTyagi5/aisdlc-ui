"""What one agent hands the next: `handoff_context`.

Three defects in `agents_orchestrator/orchestrator/copilot_api.py::_upstream_context`
define this file. Every test below exists to make one of them impossible to
reintroduce:

  1. It sliced `STAGE_ORDER[:idx]`, so an agent only ever saw the agents that come
     BEFORE it in a hardcoded pipeline. This engine has no such order — Design may
     run after Development because that is what the conversation asked for.
  2. It read the run through `get_db_session_superuser()` (which BYPASSES row-level
     security) with no tenant predicate, so a run id from another tenant read back
     fine.
  3. `except Exception: return ""` reported a failed read to the agent as "there is
     no prior work", so the agent re-asked the user for things that already existed.

The tenant tests deliberately patch the SESSION FACTORY, never `_load_run_artifacts`
itself, and the fake session EVALUATES the query's where-clause against a two-row
store holding the same run id under two different tenants. Asserting on a mock of
the function under test would prove nothing; this fake hands back the wrong tenant's
row the moment the predicate is dropped.
"""
import ast
import inspect
import textwrap
import uuid
from contextlib import asynccontextmanager

import pytest

from agents_orchestrator.orchestrator2.registry import AGENT_IDS


#: A bound value the fake store could not read. Compares equal to nothing.
_UNREADABLE = object()

_RUN = "11111111-1111-1111-1111-111111111111"
_TENANT = "22222222-2222-2222-2222-222222222222"
_OTHER_TENANT = "33333333-3333-3333-3333-333333333333"


# ── the two tests the brief fixes ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_run_yields_no_context():
    from agents_orchestrator.orchestrator2.context import handoff_context
    assert await handoff_context("no-such-run", "t", "design") == ""


@pytest.mark.asyncio
async def test_context_is_not_ordered_by_pipeline_position(monkeypatch):
    """Design may run after Development if that is what the conversation asked for.
    Context is 'what exists', never 'what comes before me in a list'."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"development": {"summary": "built X"}, "requirements": {"summary": "R1"}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context("r", "t", "design")
    assert "built X" in out and "R1" in out


# ── defect 1: never slice by pipeline position ───────────────────────────────


@pytest.mark.asyncio
async def test_the_first_agent_still_sees_every_later_agents_work(monkeypatch):
    """`requirements` is position 1, so `STAGE_ORDER[:idx]` gives it an EMPTY slice.
    Under this engine it must see all eight of the others."""
    from agents_orchestrator.orchestrator2 import context

    everything = {a: f"artifact-of-{a}" for a in AGENT_IDS}

    async def _fake(run_id, tenant_id):
        return everything

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "requirements")

    for agent_id in everything:
        assert f"artifact-of-{agent_id}" in out, (
            f"{agent_id}'s work is missing — this is the STAGE_ORDER[:idx] slice back"
        )


@pytest.mark.asyncio
async def test_no_agent_ordering_vocabulary_survives_in_the_code(monkeypatch):
    """Source-level, docstrings stripped: the prose explains the slice it replaced,
    so a raw scan would trip over its own explanation."""
    from agents_orchestrator.orchestrator2 import context

    code = _module_code(context)
    for banned in ("STAGE_ORDER[", "pipeline_position", "next_stage", "prior_stage",
                   "upstream"):
        assert banned not in code, f"{banned} reintroduces the ordering this replaces"


# ── defect 2: the read is tenant-scoped ──────────────────────────────────────


class _Row:
    """One `orchestrator_deliverables` row.

    Phase 4 moved this read off the run's `*_artifacts` columns and onto the
    deliverables table. The GUARANTEES these tests defend are unchanged — the read is
    tenant-scoped twice and never touches the RLS-bypassing session — so the fixtures
    were repointed rather than the tests deleted. What changed is only where the rows
    live; deleting the tests would have retired the guarantee along with the column.
    """

    def __init__(self, agent_id, content, *, run_id=None, tenant_id=None,
                 title=None, created_at=0):
        self.id = uuid.uuid4()
        self.run_id = uuid.UUID(run_id or _RUN)
        self.tenant_id = uuid.UUID(tenant_id or _TENANT)
        self.project_id = None
        self.agent_id = agent_id
        self.kind = "markdown"
        self.title = title or f"{agent_id} deliverable"
        self.content = content
        self.url = None
        self.language = None
        self.source = None
        self.created_at = created_at


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _RowStore:
    """A small 'database' holding rows under more than one tenant.

    Holds DELIVERABLE rows only. The conversation half of `handoff_context` is
    answered empty here — `test_transcript_memory.py` is where that half is exercised,
    against its own fixtures.

    `execute` reads the statement's where-clause and matches on the predicates it
    actually contains. Drop the tenant predicate and this store happily returns the
    other tenant's rows — which is exactly the regression the tests below catch.

    It also honours the statement's ORDER BY rather than imposing its own. Sorting
    here would let the real query be flipped to ascending with every test still green,
    and `latest_per_agent` takes the FIRST row per agent — so a stale document would
    be handed to every downstream agent, silently.
    """

    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self.tenants_scoped_to = []

    def _predicates(self, statement):
        where = statement.whereclause
        assert where is not None, "the deliverables query must be filtered, not a full scan"
        clauses = list(getattr(where, "clauses", [where]))
        pairs = {}
        for clause in clauses:
            # Fail loudly on an unexpected clause shape rather than silently matching
            # nothing, which would make every assertion below vacuous.
            assert hasattr(clause, "left") and hasattr(clause, "right"), clause
            # A bound value we cannot read becomes a sentinel that matches no row. A
            # predicate that was DROPPED disappears from `pairs` entirely and matches
            # every row, so the tenant tests keep their teeth.
            pairs[clause.left.name] = getattr(clause.right, "value", _UNREADABLE)
        return pairs

    def _ordering(self, statement):
        out = []
        for element in getattr(statement, "_order_by_clauses", ()):
            column = getattr(element, "element", element)
            name = getattr(column, "name", None)
            if name:
                out.append((name, "desc" in str(getattr(element, "modifier", None))))
        return out

    async def execute(self, statement):
        self.statements.append(statement)
        # `handoff_context` makes TWO reads now: the deliverables, and the run's
        # conversation. This store holds deliverable rows, so answering the transcript
        # query with them would hand `transcript.load_run_transcript` objects with no
        # `seq` — a fake failing in a way the real table cannot, which then surfaces as
        # "the conversation could not be read" and hides what these tests are about.
        #
        # Distinguished by the table the statement actually selects from, so a query
        # that changed shape stops being answered rather than being answered wrongly.
        if "conversation_messages" in str(statement.get_final_froms()[0]):
            return _Result([])
        pairs = self._predicates(statement)
        keep = []
        for row in self.rows:
            if "run_id" in pairs and pairs["run_id"] != row.run_id:
                continue
            if "tenant_id" in pairs and pairs["tenant_id"] != row.tenant_id:
                continue
            keep.append(row)
        for name, descending in reversed(self._ordering(statement)):
            keep.sort(key=lambda r: getattr(r, name), reverse=descending)
        return _Result(keep)


def _install_store(monkeypatch, store, *, raises=None):
    """Patch the tenant-scoped session factory — never the function under test — and
    make the superuser session explode if anything reaches for it."""
    @asynccontextmanager
    async def _for_tenant(tenant_id):
        store.tenants_scoped_to.append(tenant_id)
        if raises is not None:
            raise raises
        yield store

    def _superuser():
        raise AssertionError(
            "a superuser session bypasses row-level security — the tenant filter "
            "would be the only thing standing between tenants"
        )

    monkeypatch.setattr("shared.db.get_db_session_for_tenant", _for_tenant)
    monkeypatch.setattr("shared.db.get_db_session_superuser", _superuser)
    return store



@pytest.mark.asyncio
async def test_a_run_in_another_tenant_is_indistinguishable_from_one_that_does_not_exist(
    monkeypatch,
):
    """The regression itself. The same run id exists under `_OTHER_TENANT` and
    carries artifacts; a caller in `_TENANT` must get exactly what they would get
    for an id that was never issued."""
    from agents_orchestrator.orchestrator2 import context

    theirs = _Row("design", "another tenant's design", tenant_id=_OTHER_TENANT)
    store = _install_store(monkeypatch, _RowStore([theirs]))

    assert await context.handoff_context(_RUN, _TENANT, "development") == ""
    assert store.tenants_scoped_to and set(store.tenants_scoped_to) == {_TENANT}, (
        "every read must run in the caller's tenant session, not another's — "
        "handoff_context makes two (deliverables and conversation)"
    )
    # And the same call for a run id nobody issued is byte-identical.
    assert await context.handoff_context(
        "44444444-4444-4444-4444-444444444444", _TENANT, "development"
    ) == ""


@pytest.mark.asyncio
async def test_the_query_carries_an_explicit_tenant_predicate(monkeypatch):
    """Under a superuser session RLS is not a backstop; under a tenant session it is
    a second line. Both are wanted, so the predicate is written out and checked."""
    from agents_orchestrator.orchestrator2 import context

    mine = _Row("design", "mine")
    store = _install_store(monkeypatch, _RowStore([mine]))

    out = await context.handoff_context(_RUN, _TENANT, "development")
    assert "mine" in out

    # BOTH reads — the deliverables and the run's conversation. Checking only the
    # first would have let the transcript query ship with no tenant predicate at all,
    # which is the same defect this test was written for, on a newer table.
    assert len(store.statements) == 2, (
        "handoff_context reads the deliverables and the conversation"
    )
    for statement in store.statements:
        pairs = store._predicates(statement)
        assert pairs.get("run_id") in (uuid.UUID(_RUN), None)
        assert pairs.get("session_id") in (uuid.UUID(_RUN), None)
        assert pairs.get("tenant_id") == uuid.UUID(_TENANT), (
            f"a lookup ran without the caller's tenant predicate: {statement}"
        )


def test_the_reader_never_names_the_rls_bypassing_session():
    """The query moved into `deliverables._load` in Phase 4, so that is what this
    inspects now. The guarantee is unchanged: a superuser session bypasses
    row-level security unconditionally, and `copilot_api.py` has been caught
    reading through one with no tenant filter twice."""
    from agents_orchestrator.orchestrator2 import deliverables

    code = _code_of(deliverables._load)
    assert "get_db_session_for_tenant" in code
    assert "get_db_session_superuser" not in code
    assert "tenant_id" in code


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id,tenant_id", [
    ("not-a-uuid", _TENANT),
    (_RUN, "not-a-uuid"),
    ("", _TENANT),
    (_RUN, ""),
])
async def test_an_unusable_id_is_refused_before_any_query(monkeypatch, run_id, tenant_id):
    """A run id or tenant id that cannot name a row must never reach a UUID
    comparison, and must never produce a query with a predicate missing."""
    from agents_orchestrator.orchestrator2 import context

    store = _install_store(monkeypatch, _RowStore([]))
    assert await context.handoff_context(run_id, tenant_id, "design") == ""
    assert store.tenants_scoped_to == [], "no query may be issued for an unusable id"


# ── defect 3: a failed read is not "there is no prior work" ──────────────────


@pytest.mark.asyncio
async def test_a_read_failure_propagates_instead_of_looking_like_an_empty_run(monkeypatch):
    """`except Exception: return ""` told the agent the run was empty when the
    database was simply unreachable, and the agent re-asked the user for work that
    already existed."""
    from agents_orchestrator.orchestrator2 import context

    _install_store(monkeypatch, _RowStore([]), raises=RuntimeError("connection refused"))

    with pytest.raises(context.ContextUnavailableError):
        await context.handoff_context(_RUN, _TENANT, "design")


@pytest.mark.asyncio
async def test_the_original_cause_is_chained_not_discarded(monkeypatch):
    from agents_orchestrator.orchestrator2 import context

    boom = RuntimeError("connection refused")
    _install_store(monkeypatch, _RowStore([]), raises=boom)

    with pytest.raises(context.ContextUnavailableError) as caught:
        await context.handoff_context(_RUN, _TENANT, "design")
    assert caught.value.__cause__ is boom


def test_the_reader_has_no_blanket_return_empty_on_exception():
    """A structural guard on the shape, so the behaviour above cannot be satisfied
    by an `except` that returns "" for one exception type and raises for another."""
    from agents_orchestrator.orchestrator2 import context

    tree = ast.parse(textwrap.dedent(inspect.getsource(context._load_run_artifacts)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return):
                value = inner.value
                empty = (isinstance(value, ast.Constant) and value.value == "")
                empty_dict = isinstance(value, ast.Dict) and not value.keys
                assert not (empty or empty_dict), (
                    "an exception handler must not return an empty result — that is "
                    "the failed read disguised as an empty run"
                )


# ── the target agent's own artifact ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_targets_own_artifact_is_included_and_marked_as_its_own(monkeypatch):
    """DECISION: included. Several agents' graphs run on in-memory checkpointers, so
    after a restart an agent's own earlier output survives only in the run row —
    dropping it would be a silent information loss, which is the failure class this
    engine exists to remove. It is labelled distinctly so the agent can tell its own
    output from someone else's."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"design": {"summary": "my own earlier design"},
                "development": {"summary": "someone else's code"}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    assert "my own earlier design" in out

    # Asserted against LITERAL text, not against `_heading`'s own return value. The
    # first version of this test read both headings back out of `_heading` and
    # asserted they differed — which passes however `_heading` behaves, because the
    # two agents have different NAMES ("Design Agent" vs "Development Agent") and that
    # alone satisfies `!=`. Deleting the marking branch outright left this test green.
    # A test whose expected value comes from the function under test cannot fail.
    design_line = next(
        line for line in out.splitlines() if line.startswith("### ") and "Design" in line
    )
    development_line = next(
        line for line in out.splitlines()
        if line.startswith("### ") and "Development" in line
    )
    assert "(this agent's own earlier output on this run)" in design_line, (
        "the target's own artifact must be MARKED as its own, or the agent reads its "
        "own earlier output as an external requirement handed to it"
    )
    assert "(this agent's own earlier output on this run)" not in development_line, (
        "another agent's artifact must not be marked as the target's own"
    )


# ── the cap ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_cap_holds_with_nine_oversized_artifacts(monkeypatch):
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {a: "x" * 200_000 for a in AGENT_IDS}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    assert len(out) <= context.MAX_CONTEXT_CHARS, (
        f"rendered {len(out)} chars against a cap of {context.MAX_CONTEXT_CHARS}"
    )


@pytest.mark.asyncio
async def test_truncation_announces_itself(monkeypatch):
    """A silently shortened context is the failed read in another costume: the agent
    must be able to tell that it is not looking at the whole thing."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"requirements": {"blob": "y" * 200_000}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    assert "truncated" in out.lower()
    assert len(out) <= context.MAX_CONTEXT_CHARS


@pytest.mark.asyncio
async def test_one_huge_artifact_does_not_starve_the_others(monkeypatch):
    """The budget is SHARED, not first-come-first-served.

    A first-come-first-served budget survives a weaker version of this test: one
    200KB payload plus two tiny ones still fits under the cap by luck. So this uses
    a full run — one oversized artifact and eight substantial ones — where a greedy
    first section eats the budget and every agent rendered after it is trimmed off
    the end entirely.
    """
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        artifacts = {a: "m" * 3_000 for a in AGENT_IDS}
        artifacts["requirements"] = "z" * 200_000
        return artifacts

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    for agent_id in AGENT_IDS:
        heading = context._heading(agent_id, target_agent="design")
        assert heading in out, (
            f"{agent_id} was pushed out of the block — the oversized requirements "
            "payload took more than its share"
        )


@pytest.mark.asyncio
async def test_a_small_artifact_is_never_marked_truncated(monkeypatch):
    """The other half of the announcement: a claim of truncation that is not true
    would teach the agent to distrust a complete context."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"requirements": {"summary": "short and complete"}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    assert "short and complete" in out
    assert "truncated" not in out.lower()


# ── where a deliverable is stored, and what stops it drifting ────────────────
#
# Phase 4 replaced the run's `*_artifacts` columns with the `orchestrator_deliverables`
# table, so the tests that proved "every agent resolves a real column" no longer have
# a column to check. The GUARANTEE they stood for is unchanged and did not go away
# with them:
#
#   · every agent can store something          -> the agent_id CHECK equals AGENT_IDS,
#                                                 asserted in test_deliverables_schema.py
#   · every agent renders under its own name   -> DISPLAY_NAME covers AGENT_IDS,
#                                                 asserted in test_deliverables_render.py
#                                                 and at import in deliverables.py
#   · nothing reads the standalone agents' columns -> asserted below
#
# Deleting these tests outright would have retired the guarantee along with the
# column, which is the failure this branch has already made eight times: prose (or a
# test) certifying something the code no longer does.


def test_the_context_reader_names_no_run_artifact_column():
    """The clearest statement of the separation the user asked for.

    `runs.design_artifacts` and friends are what the STANDALONE agents write. If this
    module ever names one again, the Orchestrator has started reading the other
    surface's storage and the two concepts have quietly merged back together.
    """
    from agents_orchestrator.orchestrator2 import context

    code = _module_code(context)
    for column in ("design_artifacts", "requirements_payload", "requirements_artifacts",
                   "development_artifacts", "plan_artifacts", "security_artifacts"):
        assert column not in code, (
            f"context reads {column} — that column belongs to the standalone agents"
        )


@pytest.mark.asyncio
async def test_each_agents_deliverable_is_attributed_to_that_agent(monkeypatch):
    """End to end through the real reading path: a row stored under an agent must come
    back under that agent's own heading. A structural assertion alone would still pass
    if rows were read correctly and then attributed to the wrong author."""
    from agents_orchestrator.orchestrator2 import context

    rows = [_Row(agent_id, f"work-of-{agent_id}", created_at=i)
            for i, agent_id in enumerate(AGENT_IDS)]
    _install_store(monkeypatch, _RowStore(rows))

    out = await context.handoff_context(_RUN, _TENANT, "design")
    for agent_id in AGENT_IDS:
        assert context._heading(agent_id, target_agent="design") in out
        assert f"work-of-{agent_id}" in out, f"{agent_id}'s deliverable was not read"


@pytest.mark.asyncio
async def test_the_project_manager_agent_is_read_like_any_other(monkeypatch):
    """`plan` had no branch in `sections_from_run`, so `plan_artifacts` was written by
    nothing and read by nothing. It must not be a special case here either."""
    from agents_orchestrator.orchestrator2 import context

    _install_store(monkeypatch, _RowStore([_Row("plan", "the sprint plan")]))
    out = await context.handoff_context(_RUN, _TENANT, "development")
    assert "the sprint plan" in out


@pytest.mark.asyncio
async def test_only_the_newest_row_per_agent_is_rendered(monkeypatch):
    """Through the REAL reader, not a stubbed `latest_per_agent`. Every version stays
    in the panel; only the newest is handed forward."""
    from agents_orchestrator.orchestrator2 import context

    _install_store(monkeypatch, _RowStore([
        _Row("requirements", "SUPERSEDED", created_at=1),
        _Row("requirements", "CURRENT", created_at=2),
    ]))
    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "CURRENT" in out
    assert "SUPERSEDED" not in out, (
        "two versions of one document leave the agent to guess which is current"
    )



# ── odds and ends ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unknown_target_agent_raises_rather_than_rendering_anything(monkeypatch):
    from agents_orchestrator.orchestrator2 import context
    from agents_orchestrator.orchestrator2.registry import UnknownAgentError

    async def _fake(run_id, tenant_id):
        raise AssertionError("must not read the run for an agent that does not exist")

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    with pytest.raises(UnknownAgentError):
        await context.handoff_context(_RUN, _TENANT, "marketing")


@pytest.mark.asyncio
async def test_empty_artifacts_are_nothing_to_report(monkeypatch):
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"design": {}, "plan": None, "development": ""}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    assert await context.handoff_context(_RUN, _TENANT, "security") == ""


@pytest.mark.asyncio
async def test_a_run_with_no_deliverables_at_all_yields_nothing(monkeypatch):
    """An empty run and a failed read must NOT look the same to the agent — the empty
    one returns "", the failed one raises. This is the empty half."""
    from agents_orchestrator.orchestrator2 import context

    store = _install_store(monkeypatch, _RowStore([]))
    assert await context.handoff_context(_RUN, _TENANT, "design") == ""
    assert store.tenants_scoped_to and set(store.tenants_scoped_to) == {_TENANT}, (
        "the run WAS read — twice, deliverables and conversation — and is simply empty"
    )


@pytest.mark.asyncio
async def test_a_non_serialisable_artifact_still_renders(monkeypatch):
    """Artifact columns are JSONB and should always be plain data, but a value that
    slipped through must not take the whole turn down."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"design": {"when": uuid.UUID(_RUN), "note": "still here"}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "development")
    assert "still here" in out


@pytest.mark.asyncio
async def test_the_block_says_what_it_is_without_the_old_engines_vocabulary(monkeypatch):
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"requirements": {"summary": "R1"}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    assert "Requirements Agent" in out, "artifacts must be labelled by their author"
    for banned in ("PIPELINE CONTEXT", "prior stages", "upstream"):
        assert banned.lower() not in out.lower()


@pytest.mark.asyncio
async def test_an_unknown_key_from_the_reader_is_skipped_not_rendered(monkeypatch):
    """Only the nine can be labelled by author. Anything else is dropped rather than
    rendered under a made-up heading."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"marketing": {"summary": "not an agent"}, "design": {"summary": "real"}}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "development")

    assert "real" in out
    assert "not an agent" not in out


# ── helpers ──────────────────────────────────────────────────────────────────


def _strip_docstring(node):
    if (getattr(node, "body", None)
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]
    return node


def _code_of(fn) -> str:
    """A function's source with its docstring stripped (`ast.unparse` drops comments
    too). Assertions about what code DOES must not be satisfiable — or defeated — by
    what its prose SAYS about it."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return ast.unparse(_strip_docstring(tree.body[0]))


def _module_code(module) -> str:
    """The whole module with every docstring stripped, for the same reason."""
    tree = ast.parse(inspect.getsource(module))
    _strip_docstring(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _strip_docstring(node)
    return ast.unparse(tree)


# ── Phase 4: context reads deliverables, not the run's artifact columns ───────


@pytest.mark.asyncio
async def test_context_reads_deliverables_not_run_columns(monkeypatch):
    """The run's `*_artifacts` columns are what the STANDALONE agents write. The
    Orchestrator's agents share their names and capability and are a different thing;
    reading those columns here would mix the two concepts the separate deliverables
    table exists to keep apart."""
    from agents_orchestrator.orchestrator2 import context, deliverables

    async def _latest(run_id, tenant_id):
        return {"requirements": {"title": "PRD v2", "content": "the newest PRD"}}

    monkeypatch.setattr(deliverables, "latest_per_agent", _latest)
    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "the newest PRD" in out


@pytest.mark.asyncio
async def test_only_the_newest_version_reaches_an_agent(monkeypatch):
    """Every version stays visible in the panel. Feeding two versions of one PRD to a
    downstream agent makes it guess which is current, and it will sometimes guess
    wrong."""
    from agents_orchestrator.orchestrator2 import context, deliverables

    async def _latest(run_id, tenant_id):
        return {"requirements": {"title": "PRD v2", "content": "NEWEST"}}

    monkeypatch.setattr(deliverables, "latest_per_agent", _latest)
    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "NEWEST" in out and "PRD v1" not in out


@pytest.mark.asyncio
async def test_a_deliverable_reaches_the_agent_as_markdown_not_json(monkeypatch):
    """A document JSON-quoted into the prompt arrives with escaped newlines and
    surrounding quotes — technically present, and materially harder for the agent to
    read than the markdown the user saw."""
    from agents_orchestrator.orchestrator2 import context, deliverables

    async def _latest(run_id, tenant_id):
        return {"requirements": {"title": "PRD", "content": "# Heading\n\n- a bullet"}}

    monkeypatch.setattr(deliverables, "latest_per_agent", _latest)
    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "# Heading\n\n- a bullet" in out
    # No backslash-escaped newlines: that is exactly what json.dumps would have
    # produced. Built with chr(92) so the check cannot itself be softened by an
    # escape sequence being read one level too early.
    assert chr(92) + "n" not in out


@pytest.mark.asyncio
async def test_a_failed_deliverables_read_is_not_reported_as_an_empty_run(monkeypatch):
    """`""` reaches the agent as "nothing has happened on this run", so it re-asks the
    user for work already done. That was the old `_upstream_context` defect and it
    must not come back through the new reader."""
    from agents_orchestrator.orchestrator2 import context, deliverables

    async def _boom(run_id, tenant_id):
        raise RuntimeError("database gone")

    monkeypatch.setattr(deliverables, "latest_per_agent", _boom)
    with pytest.raises(context.ContextUnavailableError):
        await context.handoff_context(_RUN, _TENANT, "design")


def test_the_column_based_reader_is_gone(monkeypatch):
    """ARTIFACT_COLUMNS mapped each agent to a `runs` column. Nothing reads those
    columns now, and leaving the map behind would invite a future change to read from
    it again — reintroducing exactly the confusion between the standalone agents'
    artifacts and the Orchestrator's deliverables."""
    from agents_orchestrator.orchestrator2 import context
    assert not hasattr(context, "ARTIFACT_COLUMNS")


# ── carried debt #6: what a shortened document keeps ─────────────────────────


@pytest.mark.asyncio
async def test_a_shortened_document_keeps_its_ending(monkeypatch):
    """CARRIED DEBT #6, the mechanical half.

    Truncation was `body[:share]` — head only. On a full nine-agent run each artifact
    got ~2,400 characters, so a PRD was cut to its title, its background and nothing
    else: the requirements themselves, the acceptance criteria and every decision live
    at the END of such a document. The agent downstream then read an introduction and
    inferred the rest.

    Keeping the head AND the tail costs nothing extra and preserves both the framing
    and the conclusions. It does not make the budget larger; it makes the same budget
    carry the parts that matter.
    """
    from agents_orchestrator.orchestrator2 import context

    head = "OPENING-MARKER " + ("a" * 40_000)
    body = head + " CLOSING-MARKER"

    async def _fake(run_id, tenant_id):
        return {a: body for a in AGENT_IDS}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    assert "OPENING-MARKER" in out, "the opening must survive"
    assert "CLOSING-MARKER" in out, (
        "the ending must survive too — a PRD's requirements and acceptance criteria "
        "are at the bottom, and head-only truncation threw them away"
    )
    assert len(out) <= context.MAX_CONTEXT_CHARS


@pytest.mark.asyncio
async def test_the_cut_says_where_it_happened(monkeypatch):
    """A document with its middle removed must not read as continuous prose — an agent
    that cannot see the seam will treat two unrelated paragraphs as consecutive."""
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {"requirements": "x" * 200_000}

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "truncated" in out.lower()


def test_the_budget_is_large_enough_for_a_real_document():
    """The other half of #6, and a judgement call recorded as one.

    24,000 characters over nine agents is ~2,400 each — under a page. The ceiling is
    raised so a full run still hands each agent something usable, while staying a
    bounded and predictable slice of the target's window rather than an open tap.
    """
    from agents_orchestrator.orchestrator2 import context
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS as _IDS

    per_agent = context.MAX_CONTEXT_CHARS // len(_IDS)
    assert per_agent >= 5_000, f"only {per_agent} chars per agent on a full run"
    # Still bounded: ~4 chars/token, so this must stay a modest slice of a large window.
    assert context.MAX_CONTEXT_CHARS <= 120_000


def test_shorten_never_returns_more_than_its_share():
    """The invariant `_render`'s budget arithmetic depends on.

    Found by mutation: dropping the elision from the budget made `_shorten` return
    `share + len(_ELISION)`, and every existing test still passed — because the
    hard-trim fallback at the end of `_render` silently rescued the total. A rescue
    path covering for a broken caller is exactly the kind of thing that keeps working
    until the day it does not.
    """
    from agents_orchestrator.orchestrator2.context import _shorten

    body = "x" * 100_000
    for share in (200, 500, 2_400, 8_000):
        assert len(_shorten(body, share)) <= share, share


def test_shorten_marks_the_seam():
    """Head and tail joined with nothing between them read as continuous prose, so an
    agent treats two unrelated paragraphs as consecutive. Found by mutation: removing
    the marker broke nothing else."""
    from agents_orchestrator.orchestrator2.context import _ELISION, _shorten

    out = _shorten("A" * 5_000 + "Z" * 5_000, 2_000)
    assert _ELISION.strip() in out


def test_shorten_leaves_a_short_document_alone():
    from agents_orchestrator.orchestrator2.context import _shorten

    assert _shorten("short", 500) == "short"


def test_shorten_degrades_to_head_only_when_the_share_is_tiny():
    """Below roughly two markers' worth there is no room for both halves, and a
    document that is mostly marker is worse than one that is merely cut short."""
    from agents_orchestrator.orchestrator2.context import _ELISION, _shorten

    tiny = len(_ELISION)
    out = _shorten("A" * 5_000, tiny)
    assert len(out) <= tiny
    assert _ELISION.strip() not in out
