"""§1.5: "whoever ran the agent is never the one who accepts its own output."

This was stated in the design doc and enforced nowhere on the gate path — and it could
not have been, because nothing on `runs` recorded who started one. Migration 0038 adds
`runs.created_by`; these tests are the rule that column exists to serve.

WHY THE PERMISSION CHECK IS NOT ENOUGH. `_handle_gate_decision` already calls
`can_user_approve(perms, stage)`, which asks "may this ROLE approve this stage". A `ba`
who starts a Requirements run holds `artifact:approve_requirements` by definition, so
the permission check passes for exactly the person the rule is meant to stop. The two
checks answer different questions and both are needed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RUN = "44444444-4444-4444-4444-444444444444"
TENANT = "11111111-1111-1111-1111-111111111111"
INITIATOR = "user-who-ran-it"
OTHER = "user-who-reviews-it"


def _copilot():
    from agents_orchestrator.orchestrator import copilot_api

    return copilot_api


class _Run:
    def __init__(self, created_by):
        self.created_by = created_by


def _db_returning(run):
    """Stand in for `get_db_session_for_tenant(...)` yielding a session whose query
    resolves to `run`."""
    from contextlib import asynccontextmanager

    class _Result:
        def scalar_one_or_none(self):
            return run

    class _Session:
        async def execute(self, *_a, **_k):
            return _Result()

    @asynccontextmanager
    async def _cm(*_a, **_k):
        yield _Session()

    return _cm


# ── _is_run_initiator ────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_the_initiator_is_recognised():
    c = _copilot()
    with patch.object(c, "get_db_session_for_tenant", _db_returning(_Run(INITIATOR))):
        assert await c._is_run_initiator(RUN, TENANT, INITIATOR) is True


@pytest.mark.unit
async def test_somebody_else_is_not_the_initiator():
    c = _copilot()
    with patch.object(c, "get_db_session_for_tenant", _db_returning(_Run(INITIATOR))):
        assert await c._is_run_initiator(RUN, TENANT, OTHER) is False


@pytest.mark.unit
async def test_an_unknown_initiator_is_not_treated_as_a_match():
    """`created_by` is nullable on purpose: webhook runs have no human initiator, and
    every run predating 0038 has none recorded. NULL must not match the decider — that
    would block approval on every one of those runs rather than protect them."""
    c = _copilot()
    for stored in (None, ""):
        with patch.object(c, "get_db_session_for_tenant", _db_returning(_Run(stored))):
            assert await c._is_run_initiator(RUN, TENANT, OTHER) is False
            assert await c._is_run_initiator(RUN, TENANT, "") is False


@pytest.mark.unit
async def test_a_lookup_failure_does_not_block_the_gate():
    """Fail-open on THIS check only. A database hiccup must not make an approval
    impossible; the permission check above it is the one that fails closed."""
    from contextlib import asynccontextmanager

    c = _copilot()

    @asynccontextmanager
    async def _boom(*_a, **_k):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    with patch.object(c, "get_db_session_for_tenant", _boom):
        assert await c._is_run_initiator(RUN, TENANT, INITIATOR) is False


@pytest.mark.unit
async def test_a_missing_run_is_not_a_match():
    c = _copilot()
    with patch.object(c, "get_db_session_for_tenant", _db_returning(None)):
        assert await c._is_run_initiator(RUN, TENANT, INITIATOR) is False


# ── the gate decision ────────────────────────────────────────────────────────


async def _decide(*, decision, is_initiator, can_approve=True, artifact=False):
    """Drive `_handle_gate_decision` and return everything it sent to the socket."""
    c = _copilot()
    ws = AsyncMock()
    sent: list[dict] = []

    async def _capture(_ws, msg):
        sent.append(msg)

    with patch.object(c, "_send", _capture), \
            patch.object(c, "can_user_approve", lambda *_a: can_approve), \
            patch.object(c, "_is_run_initiator", AsyncMock(return_value=is_initiator)), \
            patch.object(c, "_stage_artifact_present", AsyncMock(return_value=artifact)):
        await c._handle_gate_decision(
            "requirements", RUN, TENANT, ["artifact:approve_requirements"],
            decision, None, ws, user_id=INITIATOR,
        )
    return " ".join(str(m.get("message", "")) for m in sent)


@pytest.mark.unit
async def test_the_person_who_ran_it_cannot_approve_it():
    out = await _decide(decision="approve", is_initiator=True)
    assert "started this run" in out
    assert "someone else" in out.lower()


@pytest.mark.unit
async def test_somebody_else_holding_the_permission_gets_past_the_check():
    """It must fail LATER (no artifact yet), not at the self-approval check — otherwise
    this test would pass for the wrong reason."""
    out = await _decide(decision="approve", is_initiator=False, artifact=False)
    assert "started this run" not in out
    assert "hasn't run yet" in out


@pytest.mark.unit
async def test_the_initiator_may_still_reject_their_own_run():
    """Sending your own work back is the one decision the initiator cannot abuse, and
    blocking it would leave a run nobody can move — the initiator often IS the person
    who notices the output is wrong."""
    out = await _decide(decision="reject", is_initiator=True)
    assert "started this run" not in out


@pytest.mark.unit
async def test_the_permission_check_still_runs_first():
    """Order matters: someone without the permission is told about the permission, not
    about self-approval, even when both apply."""
    out = await _decide(decision="approve", is_initiator=True, can_approve=False)
    assert "approval permission" in out
    assert "started this run" not in out


# ── the column the rule depends on ───────────────────────────────────────────


@pytest.mark.unit
def test_the_run_model_records_its_initiator():
    """If this column is ever dropped, `_is_run_initiator` silently returns False for
    every run and the rule above stops binding without a single test failing."""
    from shared.models.orm import Run

    col = Run.__table__.c["created_by"]
    assert col.nullable is True


@pytest.mark.unit
def test_the_manual_run_route_records_the_initiator():
    """POST /runs is the path that creates gate-bearing runs. A run created without an
    initiator is exempt from the rule, so this pins the one that matters."""
    import inspect

    from shared.routers import runs as runs_router

    src = inspect.getsource(runs_router)
    assert "created_by=_user_id(request)" in src
