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

    everything = {a: {"marker": f"artifact-of-{a}"} for a in context.ARTIFACT_COLUMNS}

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
    """A `runs` row with every artifact column present, defaulting to None, so a
    query that reads a column this test did not set gets None rather than an
    AttributeError that would look like a different bug."""

    def __init__(self, **columns):
        from agents_orchestrator.orchestrator2 import context

        for column in context.ARTIFACT_COLUMNS.values():
            setattr(self, column, None)
        for key, value in columns.items():
            setattr(self, key, value)


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _RowStore:
    """A two-row 'database': the SAME run id under two DIFFERENT tenants.

    `execute` reads the statement's where-clause and matches on the predicates it
    actually contains. Drop the tenant predicate and this store happily returns the
    other tenant's row — which is exactly the regression the tests below catch.
    """

    def __init__(self, rows):
        self.rows = rows          # list of (id, tenant_id, row)
        self.statements = []
        self.tenants_scoped_to = []

    def _predicates(self, statement):
        where = statement.whereclause
        assert where is not None, "the run query must be filtered, not a full scan"
        clauses = list(getattr(where, "clauses", [where]))
        pairs = {}
        for clause in clauses:
            # Fail loudly on an unexpected clause shape rather than silently
            # matching nothing, which would make every assertion below vacuous.
            assert hasattr(clause, "left") and hasattr(clause, "right"), clause
            # A bound value we cannot read (e.g. `Run.id == None`, which compiles to
            # IS NULL) becomes a sentinel that matches no row. A predicate that was
            # DROPPED disappears from `pairs` entirely and matches every row, so the
            # tenant tests keep their teeth.
            pairs[clause.left.name] = getattr(clause.right, "value", _UNREADABLE)
        return pairs

    async def execute(self, statement):
        self.statements.append(statement)
        pairs = self._predicates(statement)
        for row_id, row_tenant, row in self.rows:
            if "id" in pairs and str(pairs["id"]) != row_id:
                continue
            if "tenant_id" in pairs and str(pairs["tenant_id"]) != row_tenant:
                continue
            return _Result(row)
        return _Result(None)


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

    theirs = _Row(design_artifacts={"secret": "another tenant's design"})
    store = _install_store(monkeypatch, _RowStore([(_RUN, _OTHER_TENANT, theirs)]))

    assert await context.handoff_context(_RUN, _TENANT, "development") == ""
    assert store.tenants_scoped_to == [_TENANT], (
        "the read must run in the caller's tenant session, not another's"
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

    mine = _Row(design_artifacts={"summary": "mine"})
    store = _install_store(monkeypatch, _RowStore([(_RUN, _TENANT, mine)]))

    out = await context.handoff_context(_RUN, _TENANT, "development")
    assert "mine" in out

    assert len(store.statements) == 1
    pairs = store._predicates(store.statements[0])
    assert pairs.get("id") == uuid.UUID(_RUN)
    assert pairs.get("tenant_id") == uuid.UUID(_TENANT), (
        "the run lookup must filter on the caller's tenant"
    )


def test_the_reader_never_names_the_rls_bypassing_session():
    from agents_orchestrator.orchestrator2 import context

    code = _code_of(context._load_run_artifacts)
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
    own_heading = context._heading("design", target_agent="design")
    other_heading = context._heading("development", target_agent="design")
    assert own_heading in out
    assert other_heading in out
    assert own_heading != other_heading, (
        "the target's own output must be distinguishable from another agent's"
    )


# ── the cap ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_cap_holds_with_nine_oversized_artifacts(monkeypatch):
    from agents_orchestrator.orchestrator2 import context

    async def _fake(run_id, tenant_id):
        return {a: {"blob": "x" * 200_000} for a in context.ARTIFACT_COLUMNS}

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
        artifacts = {a: {"body": "m" * 3_000} for a in context.ARTIFACT_COLUMNS}
        artifacts["requirements"] = {"body": "z" * 200_000}
        return artifacts

    monkeypatch.setattr(context, "_load_run_artifacts", _fake)
    out = await context.handoff_context(_RUN, _TENANT, "design")

    for agent_id in context.ARTIFACT_COLUMNS:
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


# ── the column names come from the registry ──────────────────────────────────


def test_every_agent_has_an_artifact_column_that_exists_on_the_run_row():
    from agents_orchestrator.orchestrator2 import context
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS
    from shared.models.orm import Run

    assert set(context.ARTIFACT_COLUMNS) == set(AGENT_IDS)
    for agent_id, column in context.ARTIFACT_COLUMNS.items():
        assert hasattr(Run, column), f"{agent_id} -> {column} is not a runs column"


def test_requirements_writes_the_payload_column_not_an_artifacts_column():
    """The one the plan's earlier draft got wrong. `requirements_artifacts` also
    exists on the row and is NOT what the Requirements agent writes."""
    from agents_orchestrator.orchestrator2 import context

    assert context.ARTIFACT_COLUMNS["requirements"] == "requirements_payload"


@pytest.mark.asyncio
async def test_each_agents_artifact_is_read_from_the_column_the_registry_names(monkeypatch):
    """End to end through the real row-reading path: a value written to the column
    `AGENT_REGISTRY` names for an agent must come back attributed to that agent. The
    structural assertions above would still pass if the mapping were built correctly
    and then read from the wrong attribute."""
    from agents_orchestrator.orchestrator2 import context

    row = _Row(**{column: {"marker": f"in-{column}"}
                  for column in context.ARTIFACT_COLUMNS.values()})
    _install_store(monkeypatch, _RowStore([(_RUN, _TENANT, row)]))

    out = await context.handoff_context(_RUN, _TENANT, "design")
    for agent_id, column in context.ARTIFACT_COLUMNS.items():
        assert context._heading(agent_id, target_agent="design") in out
        assert f"in-{column}" in out, f"{agent_id} was not read from {column}"


@pytest.mark.asyncio
async def test_the_decoy_requirements_artifacts_column_is_never_read(monkeypatch):
    """`runs.requirements_artifacts` exists and is NOT what the Requirements agent
    writes. Reading it would give every run a phantom requirements section (or, with
    the mapping the plan's earlier draft had, hide the real payload)."""
    from agents_orchestrator.orchestrator2 import context

    row = _Row(requirements_artifacts={"marker": "decoy-column"},
               requirements_payload={"marker": "the-real-payload"})
    _install_store(monkeypatch, _RowStore([(_RUN, _TENANT, row)]))

    out = await context.handoff_context(_RUN, _TENANT, "design")
    assert "the-real-payload" in out
    assert "decoy-column" not in out


def test_the_columns_are_derived_from_the_registry_not_typed_out():
    from agents_orchestrator.orchestrator2 import context

    code = _module_code(context)
    assert "AGENT_REGISTRY" in code
    for hardcoded in ('"design_artifacts"', "'design_artifacts'",
                      '"requirements_payload"', "'requirements_payload'"):
        assert hardcoded not in code, (
            "column names must be derived from the registry, never restated here"
        )


def test_an_agent_with_no_output_artifact_breaks_the_import(monkeypatch):
    """The pattern `registry.py` and `router.py` use: an agent that would otherwise
    be silently unreachable fails at import, not at request time."""
    from agents_orchestrator.orchestrator2 import context

    class _NoColumn:
        output_artifact = None

    doctored = {"design": _NoColumn()}
    with pytest.raises(context.ArtifactColumnError) as caught:
        context._build_artifact_columns(agent_ids=("design",), registry=doctored)
    assert "design" in str(caught.value)


def test_an_artifact_column_missing_from_the_row_breaks_the_import():
    from agents_orchestrator.orchestrator2 import context

    class _Bogus:
        output_artifact = "column_that_does_not_exist"

    with pytest.raises(context.ArtifactColumnError) as caught:
        context._build_artifact_columns(agent_ids=("design",), registry={"design": _Bogus()})
    assert "column_that_does_not_exist" in str(caught.value)


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
async def test_a_run_with_no_artifacts_at_all_yields_nothing(monkeypatch):
    from agents_orchestrator.orchestrator2 import context

    store = _install_store(monkeypatch, _RowStore([(_RUN, _TENANT, _Row())]))
    assert await context.handoff_context(_RUN, _TENANT, "design") == ""
    assert store.tenants_scoped_to == [_TENANT], "the run WAS read; it is simply empty"


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
