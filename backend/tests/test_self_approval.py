"""§1.5: "whoever ran the agent is never the one who accepts its own output."

THIS RULE WAS ENFORCED IN EXACTLY ONE PLACE, and Phase 5 deleted it.

`copilot_api._handle_gate_decision` checked it, and `tests/test_gate_self_approval.py`
was the only coverage. Retiring the Copilot removed both, leaving `runs.created_by` —
added by migration 0038 *specifically to serve this rule* — with no consumer at all.

WHY THE PERMISSION CHECK IS NOT ENOUGH, restated because it is the whole point.
`record_approval` asks `has_permission(actor, _PHASE_PERMISSION[stage])`, which answers
"may this ROLE approve this stage". A BA who starts a Requirements run holds
`artifact:approve_requirements` by definition, so the permission check passes for
exactly the person the rule exists to stop. The two checks answer different questions
and both are needed.

The approver is compared against `runs.created_by`. Where a run cannot be identified
the rule cannot be applied, and the request proceeds on the permission check alone —
stated here so that gap is a decision rather than an oversight.
"""
from __future__ import annotations

import inspect

import pytest

RUN = "44444444-4444-4444-4444-444444444444"
TENANT = "11111111-1111-1111-1111-111111111111"
INITIATOR = "user-who-ran-it"
OTHER = "user-who-reviews-it"


class _Run:
    def __init__(self, created_by):
        self.id = RUN
        self.created_by = created_by
        self.current_stage = "requirements"
        self.stage = "requirements"
        self.status = "running"
        self.tenant_id = TENANT
        self.project_id = None


class _Request:
    def __init__(self, user_id):
        self.state = type("S", (), {
            "tenant_id": TENANT,
            "user_id": user_id,
            "permissions": ["artifact:approve_requirements"],
        })()


class _Body:
    decision = "approve"
    reason = "looks good"
    idempotencyKey = None
    stage = "requirements"


def _install(monkeypatch, run):
    from shared.routers import runs

    async def _get_run(db, run_id, tenant_id, *, request):
        return run

    monkeypatch.setattr(runs, "_get_run_or_404", _get_run)


# ── the rule ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_person_who_started_the_run_cannot_approve_it(monkeypatch):
    from fastapi import HTTPException

    from shared.routers import runs

    _install(monkeypatch, _Run(created_by=INITIATOR))
    with pytest.raises(HTTPException) as caught:
        await runs.record_approval(RUN, _Body(), _Request(INITIATOR), db=None)
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_somebody_else_still_can(monkeypatch):
    """The other half. A rule that refuses everyone is not this rule."""
    from shared.routers import runs

    _install(monkeypatch, _Run(created_by=INITIATOR))
    committed = {}

    class _DB:
        def add(self, obj):
            committed["added"] = obj

        async def flush(self):
            pass

        async def commit(self):
            committed["commit"] = True

        async def refresh(self, obj):
            pass

    result = await runs.record_approval(RUN, _Body(), _Request(OTHER), db=_DB())
    assert result is not None
    # The audit row is ADDED and flushed; the session dependency commits. Asserting on
    # commit here would be asserting about the framework, not about this endpoint.
    assert committed.get("added") is not None, "the approval must be recorded"


@pytest.mark.asyncio
async def test_a_run_with_no_recorded_initiator_is_not_blocked(monkeypatch):
    """`runs.created_by` is nullable — webhook runs and rows predating migration 0038
    have none. The rule cannot be applied there, so it is not; blocking instead would
    make every historical run permanently unapprovable."""
    from shared.routers import runs

    _install(monkeypatch, _Run(created_by=None))

    class _DB:
        def add(self, obj):
            pass

        async def flush(self):
            pass

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    result = await runs.record_approval(RUN, _Body(), _Request(OTHER), db=_DB())
    assert result is not None


@pytest.mark.asyncio
async def test_the_permission_check_still_runs_first(monkeypatch):
    """Self-approval is refused for a permitted actor; an UNpermitted one must still be
    refused for the original reason, not accidentally allowed through the new branch."""
    from fastapi import HTTPException

    from shared.routers import runs

    _install(monkeypatch, _Run(created_by=INITIATOR))
    req = _Request(OTHER)
    req.state.permissions = []
    with pytest.raises(HTTPException) as caught:
        await runs.record_approval(RUN, _Body(), req, db=None)
    assert caught.value.status_code == 403


def test_the_rule_is_enforced_in_code_not_only_in_prose():
    """The defect this branch has corrected nine times: a docstring asserting a
    guarantee the code does not provide. `created_by` must appear in the FUNCTION,
    with its docstring stripped."""
    import ast
    import textwrap

    from shared.routers import runs

    tree = ast.parse(textwrap.dedent(inspect.getsource(runs.record_approval)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                node.body.pop(0)
    assert "created_by" in ast.unparse(tree)
