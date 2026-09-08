"""`_project_admin_tier_for_run` itself — the lookup, not the socket that calls it.

`test_ws_project_scope.py` covers the CALL SITE: that the socket refuses a turn when
this function answers `None`. It stubs the function to do it, as does every other test
on the branch, so until this file existed the function's own body — the UUID guard, the
tenant-scoped session, the `Project.tenant_id` predicate, the project-not-found refusal
and the delegation to the shared rule — was never executed by any test. A mutation
reducing the whole body to `return "project"` (the per-project check simply gone) left
the suite green, as did swapping the tenant-scoped session for the RLS-bypassing
superuser one.

WHAT THIS FILE PATCHES, AND WHAT IT DELIBERATELY DOES NOT. It patches the SESSION
FACTORY and nothing else — the same discipline as `test_context.py`, and for the same
reason: asserting against a mock of the function under test would prove nothing. The
fake session EVALUATES the project query's where-clause against a two-row store holding
the SAME project id under two DIFFERENT tenants, so dropping the tenant predicate hands
back the other tenant's row rather than quietly passing. `get_db_session_superuser` is
replaced with something that raises, because a superuser session bypasses row-level
security and would make the explicit predicate the only thing standing between tenants.

The raw `role_bindings` queries the shared rule issues are answered from a script keyed
on their SQL, exactly as `tests/test_project_admin_tier_addressed_by_identity.py` does.
That file proves the shared rule is the same rule the HTTP routers use; this one proves
this socket actually reaches it, with the row it read and the identity it was given.

No database. The verdicts asserted here are the rule's, and the rule's own DB-backed
behaviour is covered by the existing project-scope tests.
"""
import uuid
from contextlib import asynccontextmanager

import pytest


#: A bound value the fake store could not read. Compares equal to nothing, so a
#: predicate whose parameter is unreadable matches no row — while a predicate that was
#: DELETED disappears from the mapping entirely and matches every row. That asymmetry
#: is what keeps the tenant tests below honest.
_UNREADABLE = object()

_TENANT = uuid.UUID("22222222-2222-2222-2222-222222222222")
_OTHER_TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")
_PROJECT = uuid.UUID("44444444-4444-4444-4444-444444444444")
_ABSENT_PROJECT = uuid.UUID("55555555-5555-5555-5555-555555555555")
_UNIT = uuid.UUID("66666666-6666-6666-6666-666666666666")
_OTHER_UNIT = uuid.UUID("77777777-7777-7777-7777-777777777777")

_USER = "u-caller"


class _ProjectRow:
    """Just enough of a `projects` row for the shared rule: it reads `id` (bound into
    the own-binding query) and `workspace_id` (compared against the administered units).
    """

    def __init__(self, project_id, tenant_id, workspace_id):
        self.id = project_id
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id


class _Result:
    """One result object serving all three shapes the two callers use:
    `.scalar_one_or_none()` for the ORM project lookup, `.first()` / `.fetchall()` for
    the shared rule's raw `role_bindings` queries.
    """

    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Session:
    """A fake session answering both queries this function reaches.

    * the `select(Project)` lookup — matched against `rows` by evaluating the
      statement's own where-clause, so a dropped predicate really does widen the match;
    * the shared rule's `text()` queries — answered from `unit_rows` / `own_binding`,
      keyed on the SQL, with every statement and its bound parameters recorded.
    """

    def __init__(self, rows, *, unit_rows=(), own_binding=False):
        self.rows = list(rows)
        self.unit_rows = list(unit_rows)
        self.own_binding = own_binding
        self.project_predicates = []
        self.role_binding_params = []

    @staticmethod
    def _predicates(statement):
        where = statement.whereclause
        assert where is not None, "the project lookup must be filtered, not a full scan"
        pairs = {}
        for clause in list(getattr(where, "clauses", [where])):
            # Fail loudly on an unexpected clause shape rather than silently matching
            # nothing, which would make every assertion in this file vacuous.
            assert hasattr(clause, "left") and hasattr(clause, "right"), clause
            pairs[clause.left.name] = getattr(clause.right, "value", _UNREADABLE)
        return pairs

    async def execute(self, statement, params=None):
        if params is None and hasattr(statement, "whereclause"):
            pairs = self._predicates(statement)
            self.project_predicates.append(pairs)
            for row in self.rows:
                if "id" in pairs and pairs["id"] != row.id:
                    continue
                if "tenant_id" in pairs and pairs["tenant_id"] != row.tenant_id:
                    continue
                return _Result(scalar=row)
            return _Result(scalar=None)

        sql = " ".join(str(statement).split())
        bound = dict(params or {})
        now = bound.pop("now", None)
        assert now is not None and now.tzinfo is not None, (
            f"live_binding requires an aware :now; got {now!r}"
        )
        self.role_binding_params.append((sql, bound))
        if "business_unit" in sql:
            return _Result(rows=[(u,) for u in self.unit_rows])
        return _Result(rows=[(1,)] if self.own_binding else [])


def _install(monkeypatch, session):
    """Patch the tenant-scoped session FACTORY — never the function under test — and
    make the RLS-bypassing superuser session explode if anything reaches for it."""
    opened = []

    @asynccontextmanager
    async def _for_tenant(tenant_id):
        opened.append(tenant_id)
        yield session

    def _superuser():
        raise AssertionError(
            "a superuser session BYPASSES row-level security — under one the tenant "
            "GUC does nothing and the explicit predicate is the only thing standing "
            "between tenants (see `_resolve_run`, which this read is shaped after)"
        )

    monkeypatch.setattr("shared.db.get_db_session_for_tenant", _for_tenant)
    monkeypatch.setattr("shared.db.get_db_session_superuser", _superuser)
    return opened


def _mine():
    return _ProjectRow(_PROJECT, _TENANT, _UNIT)


def _theirs():
    """The same project id under ANOTHER tenant, on the same unit, and reachable by
    this caller's own binding — so if the tenant predicate is dropped the lookup
    succeeds and the shared rule says "project" instead of the refusal asserted."""
    return _ProjectRow(_PROJECT, _OTHER_TENANT, _UNIT)


# ── the tenant boundary ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_project_in_another_tenant_does_not_resolve(monkeypatch):
    """The id reaching here came from a `runs` row already proven to be this tenant's,
    so this predicate is belt-and-braces — but belt-and-braces that is never tested is
    just braces. The store holds this project id ONLY under another tenant, and this
    caller holds a binding that would admit them if it were found."""
    from agents_orchestrator.orchestrator2 import ws

    session = _Session([_theirs()], unit_rows=[_OTHER_UNIT], own_binding=True)
    opened = _install(monkeypatch, session)

    tier = await ws._project_admin_tier_for_run(
        str(_PROJECT), str(_TENANT), user_id=_USER, permissions=[]
    )

    assert tier is None, (
        "another tenant's project resolved — the `Project.tenant_id` predicate is gone"
    )
    assert opened == [str(_TENANT)], (
        "the read must run in the caller's own tenant session, not another's"
    )
    assert session.project_predicates == [{"id": _PROJECT, "tenant_id": _TENANT}], (
        f"the lookup asked a different question: {session.project_predicates}"
    )


@pytest.mark.asyncio
async def test_a_project_that_does_not_exist_is_a_refusal(monkeypatch):
    """An unresolvable project is not a licence to run against it. The caller here
    would be admitted for a project that DID exist — the refusal is the missing row."""
    from agents_orchestrator.orchestrator2 import ws

    session = _Session([_mine()], unit_rows=[_UNIT], own_binding=True)
    _install(monkeypatch, session)

    tier = await ws._project_admin_tier_for_run(
        str(_ABSENT_PROJECT), str(_TENANT), user_id=_USER, permissions=[]
    )

    assert tier is None, "an unresolvable project was admitted"
    assert session.role_binding_params == [], (
        "the shared rule was consulted about a project that does not exist"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["proj-B", "", "  ", "not-a-uuid", None])
async def test_a_project_id_that_is_not_a_uuid_never_reaches_the_database(
    monkeypatch, bad
):
    """`_as_run_uuid` returns None rather than the raw string precisely so unvalidated
    text is never pushed into a UUID comparison. Refusing before the session is opened
    is what makes that true — a guard that still issues the query has not guarded."""
    from agents_orchestrator.orchestrator2 import ws

    session = _Session([_mine()], unit_rows=[_UNIT], own_binding=True)
    opened = _install(monkeypatch, session)

    assert await ws._project_admin_tier_for_run(
        bad, str(_TENANT), user_id=_USER, permissions=[]
    ) is None
    assert await ws._project_admin_tier_for_run(
        str(_PROJECT), bad, user_id=_USER, permissions=[]
    ) is None
    assert opened == [], f"a non-UUID id opened a database session: {opened}"
    assert session.project_predicates == []


# ── delegation to the shared rule ────────────────────────────────────────────


_TIERS = [
    ("org-wide permission, no binding needed", ["admin:*"], {}, "org"),
    ("administers the project's parent unit", [], {"unit_rows": [_UNIT]}, "unit"),
    ("holds the project's own binding", [],
     {"unit_rows": [_OTHER_UNIT], "own_binding": True}, "project"),
    ("administers a different unit and holds nothing here", [],
     {"unit_rows": [_OTHER_UNIT]}, None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,permissions,script,expected", _TIERS,
                         ids=[c[0] for c in _TIERS])
async def test_the_verdict_is_the_shared_rules_verdict(
    monkeypatch, label, permissions, script, expected
):
    """All four answers `project_admin_tier_for` can reach, driven through this
    function. The last one is the security case and the first three are what stops a
    check that simply refuses everybody from passing it: a Project Admin of a DIFFERENT
    project in the SAME tenant is refused, and the three tiers that legitimately run the
    project are admitted."""
    from agents_orchestrator.orchestrator2 import ws

    session = _Session([_mine()], **script)
    _install(monkeypatch, session)

    tier = await ws._project_admin_tier_for_run(
        str(_PROJECT), str(_TENANT), user_id=_USER, permissions=permissions
    )

    assert tier == expected, f"{label}: got {tier!r}, expected {expected!r}"


@pytest.mark.asyncio
async def test_the_shared_rule_is_asked_about_the_row_that_was_read(monkeypatch):
    """The identity comes from the redeemed ticket and the project from the row this
    function just read under the tenant predicate — not from anything a caller named.
    Binding a client-supplied project id here would hand the rule a project the tenant
    check never saw."""
    from agents_orchestrator.orchestrator2 import ws

    session = _Session([_mine()], unit_rows=[_OTHER_UNIT], own_binding=True)
    _install(monkeypatch, session)

    tier = await ws._project_admin_tier_for_run(
        str(_PROJECT), str(_TENANT), user_id=_USER, permissions=[]
    )

    assert tier == "project"
    bound = [params for _sql, params in session.role_binding_params]
    assert {"u": _USER} in bound, (
        f"the rule was asked about a different identity: {bound}"
    )
    assert {"u": _USER, "p": _PROJECT} in bound, (
        f"the rule was asked about a different project: {bound}"
    )


@pytest.mark.asyncio
async def test_an_empty_permission_list_is_not_treated_as_org_wide(monkeypatch):
    """`[]` is a real answer meaning "this caller holds nothing", never "no filter" —
    the conflation `read_scope` warns about. The socket resolves permissions once per
    connection and hands them here, so an empty list must still be made to prove
    standing through the bindings."""
    from agents_orchestrator.orchestrator2 import ws

    session = _Session([_mine()], unit_rows=[], own_binding=False)
    _install(monkeypatch, session)

    assert await ws._project_admin_tier_for_run(
        str(_PROJECT), str(_TENANT), user_id=_USER, permissions=[]
    ) is None
