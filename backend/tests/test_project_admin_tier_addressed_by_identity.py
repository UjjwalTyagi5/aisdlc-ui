"""`project_admin_tier_for` must answer exactly what `project_admin_tier` answers.

WHY THE SPLIT EXISTS. The Orchestrator WebSocket has to ask "does this caller run the
project this run belongs to?" and has no FastAPI `Request` — it resolves the caller's
identity and permissions from a redeemed single-use ticket. The alternative was a second
hand-written `role_bindings` query on the socket, which is the mistake
`read_scope.live_binding`'s docstring records: one rule written four different ways,
disagreeing, so an elevation that had lapsed kept granting permission on one path while
being refused on another. Here the rule decides whether a Project Admin of project A may
drive all nine agents against project B's run, on B's model grant and B's budget.

So the wrapper must be a WRAPPER. These tests pin that, and they run WITHOUT A DATABASE:
the session is a fake that records the SQL it is handed and returns scripted rows, which
is enough to prove both entry points ask the same questions with the same parameters and
reach the same verdict. The DB-backed behaviour of the query itself is covered by the
existing project-scope tests; what is new here is only the second door into it.
"""
from types import SimpleNamespace

import pytest

from shared.authz import project_scope
from shared.authz.read_scope import ORG_WIDE_PERMISSIONS


_PROJECT = SimpleNamespace(id="proj-A", workspace_id="unit-1")


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row

    def fetchall(self):
        return self._row or []


class _RecordingSession:
    """Records every statement and its parameters, and answers from a script.

    `unit_rows` are what the administered-units query returns; `own_binding` is whether
    a project-scoped `project_admin` binding exists. Keyed on the SQL text because that
    is what distinguishes the two queries in this rule.
    """

    def __init__(self, *, unit_rows=(), own_binding=False):
        self.unit_rows = list(unit_rows)
        self.own_binding = own_binding
        self.seen = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        bound = dict(params or {})
        # `now` is `datetime.now()`, so it differs by microseconds between two calls and
        # would make every comparison below fail for a reason that says nothing about
        # the rule. It is asserted separately, for its PRESENCE and its tz-awareness —
        # `live_binding` requires it bound, and a naive datetime would compare wrongly
        # against a timestamptz column.
        now = bound.pop("now", None)
        assert now is not None and now.tzinfo is not None, (
            f"live_binding requires an aware :now; got {now!r}"
        )
        self.seen.append((" ".join(sql.split()), bound))
        if "business_unit" in sql:
            return _FakeResult([(u,) for u in self.unit_rows])
        return _FakeResult((1,) if self.own_binding else None)


def _request(user_id, permissions):
    return SimpleNamespace(state=SimpleNamespace(
        user_id=user_id, permissions=list(permissions)))


# The four verdicts this rule can reach, each with the state that produces it.
_CASES = [
    ("org-wide permission wins outright",
     "u-org", list(ORG_WIDE_PERMISSIONS)[:1], {}, "org"),
    ("administers the project's parent unit",
     "u-unit", [], {"unit_rows": ["unit-1"]}, "unit"),
    ("administers a DIFFERENT unit, and holds the project binding",
     "u-other", [], {"unit_rows": ["unit-2"], "own_binding": True}, "project"),
    ("administers a different unit and holds nothing on this project",
     "u-none", [], {"unit_rows": ["unit-2"]}, None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,user_id,perms,script,expected", _CASES,
                         ids=[c[0] for c in _CASES])
async def test_both_entry_points_reach_the_same_verdict(
    label, user_id, perms, script, expected
):
    via_request = _RecordingSession(**script)
    from_request = await project_scope.project_admin_tier(
        via_request, _request(user_id, perms), _PROJECT)

    via_identity = _RecordingSession(**script)
    from_identity = await project_scope.project_admin_tier_for(
        via_identity, user_id=user_id, permissions=perms, project=_PROJECT)

    assert from_request == expected, f"{label}: wrapper gave {from_request!r}"
    assert from_identity == from_request, (
        f"{label}: the two doors into this rule disagree — "
        f"request={from_request!r} identity={from_identity!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("label,user_id,perms,script,expected", _CASES,
                         ids=[c[0] for c in _CASES])
async def test_both_entry_points_ask_the_database_the_same_questions(
    label, user_id, perms, script, expected
):
    """Same verdict is not enough: two implementations can agree on these four cases
    and diverge on a fifth. Asserting the SQL and its bound parameters are identical is
    what makes the wrapper provably a wrapper rather than a lookalike."""
    via_request = _RecordingSession(**script)
    await project_scope.project_admin_tier(
        via_request, _request(user_id, perms), _PROJECT)

    via_identity = _RecordingSession(**script)
    await project_scope.project_admin_tier_for(
        via_identity, user_id=user_id, permissions=perms, project=_PROJECT)

    assert via_identity.seen == via_request.seen, (
        f"{label}: the two paths issued different queries.\n"
        f"  via request : {via_request.seen}\n"
        f"  via identity: {via_identity.seen}"
    )


@pytest.mark.asyncio
async def test_an_empty_identity_is_refused_rather_than_treated_as_org_wide():
    """`read_scope` warns that `[]` (no units) and `None` (every unit) must never be
    conflated — treating `[]` as "no filter" shows a brand-new account everything. The
    socket can reach this function with an empty user id if a ticket ever carries one,
    so the floor is asserted rather than assumed."""
    session = _RecordingSession()
    assert await project_scope.project_admin_tier_for(
        session, user_id="", permissions=[], project=_PROJECT) is None
