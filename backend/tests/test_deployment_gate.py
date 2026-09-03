"""The deployment approval gate — PHASE 1.

WHAT THIS PROTECTS. Everything else in the deployment agent generates files, which is
free and reversible. These four calls are the ones that change something outside the
platform, and each of them is a way a deploy could happen that nobody agreed to:

  · a deploy nobody approved
  · a deploy the requester approved for themselves
  · a second deploy on one approval
  · a deploy whose request changed after it was read

The database enforces most of this too (migration 0043). That is deliberate belt and
braces: a mistake in this module should still be unable to reach an environment.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.services import deployment_gate as gate  # noqa: E402
from shared.services.deployment_gate import DeploymentGateError  # noqa: E402

TENANT = str(uuid.uuid4())
PROJECT = str(uuid.uuid4())


class _Dep:
    """A deployment row, close enough to the ORM object for the gate's purposes."""

    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.tenant_id = kw.get("tenant_id", TENANT)
        self.project_id = kw.get("project_id", PROJECT)
        self.run_id = None
        self.action = kw.get("action", "run_pipeline")
        self.target_kind = kw.get("target_kind", "azure_pipelines")
        self.environment = kw.get("environment", "prod")
        self.request = kw.get("request", {"pipeline_id": 12, "branch": "main"})
        self.requested_by = kw.get("requested_by", "alice")
        self.requested_at = datetime.now(timezone.utc)
        self.approval_status = kw.get("approval_status", "pending")
        self.approved_by = kw.get("approved_by")
        self.approved_at = kw.get("approved_at")
        self.rejection_reason = None
        self.execution_status = kw.get("execution_status", "not_started")
        self.executed_at = kw.get("executed_at")
        self.external_id = None
        self.external_url = None
        self.outcome = None


class _DB:
    """Enough AsyncSession to exercise the gate, with the claim UPDATE modelled.

    `claim_rows` is what the conditional UPDATE is allowed to return — the whole point
    of the claim is that the DATABASE decides, so the test controls that directly.
    """

    def __init__(self, dep: Optional[_Dep] = None, claim_succeeds: bool = True):
        self.dep = dep
        self.added: List[Any] = []
        self.claim_succeeds = claim_succeeds
        self.claims = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def execute(self, stmt):
        text = str(stmt).strip().upper()
        db = self

        class _R:
            @staticmethod
            def scalar_one_or_none():
                if text.startswith("UPDATE"):
                    db.claims += 1
                    # One claim succeeds; every later one loses the race, exactly as
                    # the WHERE executed_at IS NULL clause makes the database behave.
                    if db.claim_succeeds and db.claims == 1:
                        db.dep.executed_at = datetime.now(timezone.utc)
                        db.dep.execution_status = "running"
                        return db.dep.id
                    return None
                return db.dep

            @staticmethod
            def scalars():
                class _S:
                    @staticmethod
                    def all():
                        return [db.dep] if db.dep else []
                return _S()

        return _R()

    def events(self) -> list[str]:
        return [getattr(a, "event_type", "") for a in self.added]


# -- requesting ----------------------------------------------------------------


@pytest.mark.unit
async def test_a_request_starts_pending_never_approved():
    """The default must be the unapproved value. A request that arrived approved is a
    deploy nobody agreed to."""
    db = _DB()
    dep = await gate.request_deployment(
        db, tenant_id=TENANT, project_id=PROJECT, action="run_pipeline",
        target_kind="azure_pipelines", environment="prod",
        request={"pipeline_id": 1}, requested_by="alice",
    )
    assert dep.approval_status == "pending"
    assert dep.executed_at is None


@pytest.mark.unit
async def test_an_ungated_action_cannot_sneak_through_this_door():
    db = _DB()
    with pytest.raises(DeploymentGateError, match="not a gated deployment action"):
        await gate.request_deployment(
            db, tenant_id=TENANT, project_id=PROJECT, action="rm_minus_rf",
            target_kind="kubernetes", environment="prod", request={},
            requested_by="alice",
        )


@pytest.mark.unit
async def test_a_request_must_name_who_asked():
    """An unattributed request cannot be checked for self-approval, which makes the
    self-approval rule silently unenforceable."""
    db = _DB()
    with pytest.raises(DeploymentGateError, match="who asked"):
        await gate.request_deployment(
            db, tenant_id=TENANT, project_id=PROJECT, action="run_pipeline",
            target_kind="azure_pipelines", environment="prod", request={},
            requested_by="",
        )


@pytest.mark.unit
async def test_a_request_must_name_an_environment():
    """"Deploy it" without saying where is not something anyone can approve."""
    db = _DB()
    with pytest.raises(DeploymentGateError, match="environment"):
        await gate.request_deployment(
            db, tenant_id=TENANT, project_id=PROJECT, action="run_pipeline",
            target_kind="azure_pipelines", environment="", request={},
            requested_by="alice",
        )


@pytest.mark.unit
async def test_the_request_is_audited_when_it_is_made():
    db = _DB()
    await gate.request_deployment(
        db, tenant_id=TENANT, project_id=PROJECT, action="run_pipeline",
        target_kind="azure_pipelines", environment="prod", request={"x": 1},
        requested_by="alice",
    )
    assert "deployment_request" in db.events()


# -- self-approval -------------------------------------------------------------


@pytest.mark.unit
async def test_the_requester_cannot_approve_their_own_deployment():
    """THE RULE. Matches the run-gate rule in tests/test_gate_self_approval.py."""
    db = _DB(_Dep(requested_by="alice"))
    with pytest.raises(DeploymentGateError, match="cannot approve your own") as e:
        await gate.approve_deployment(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="alice")
    assert e.value.code == "self_approval"


@pytest.mark.unit
async def test_somebody_else_can_approve_it():
    db = _DB(_Dep(requested_by="alice"))
    dep = await gate.approve_deployment(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="bob")
    assert dep.approval_status == "approved"
    assert dep.approved_by == "bob"
    assert "deployment_approve" in db.events()


@pytest.mark.unit
async def test_an_anonymous_approval_is_refused():
    """An approval that names nobody is not an approval — there is no one to ask."""
    db = _DB(_Dep(requested_by="alice"))
    with pytest.raises(DeploymentGateError, match="name its approver"):
        await gate.approve_deployment(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="")


@pytest.mark.unit
async def test_withdrawing_your_own_request_is_allowed():
    """Rejecting your own request is not the risk the self-approval rule guards
    against — refusing it would trap a requester with a deploy they no longer want."""
    db = _DB(_Dep(requested_by="alice"))
    dep = await gate.reject_deployment(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="alice",
        reason="wrong branch")
    assert dep.approval_status == "rejected"
    assert dep.rejection_reason == "wrong branch"


# -- decisions are final -------------------------------------------------------


@pytest.mark.unit
async def test_a_rejection_cannot_be_quietly_overturned():
    db = _DB(_Dep(approval_status="rejected"))
    with pytest.raises(DeploymentGateError, match="was rejected") as e:
        await gate.approve_deployment(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="bob")
    assert e.value.code == "already_rejected"


@pytest.mark.unit
async def test_a_second_approver_cannot_rewrite_who_signed_for_it():
    db = _DB(_Dep(approval_status="approved", approved_by="bob"))
    with pytest.raises(DeploymentGateError, match="Already approved by bob"):
        await gate.approve_deployment(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="carol")


@pytest.mark.unit
async def test_the_same_approver_clicking_twice_is_harmless():
    db = _DB(_Dep(approval_status="approved", approved_by="bob"))
    dep = await gate.approve_deployment(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="bob")
    assert dep.approved_by == "bob"


@pytest.mark.unit
async def test_rejecting_something_that_already_ran_says_roll_back():
    """A rejection after the fact does not undo a deployment, and letting the UI show
    "rejected" would claim it did."""
    db = _DB(_Dep(approval_status="approved", approved_by="bob",
                  executed_at=datetime.now(timezone.utc)))
    with pytest.raises(DeploymentGateError, match="roll back") as e:
        await gate.reject_deployment(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="carol")
    assert e.value.code == "already_executed"


# -- one approval, one deployment ---------------------------------------------


@pytest.mark.unit
async def test_an_approved_deployment_can_be_claimed_once():
    db = _DB(_Dep(approval_status="approved", approved_by="bob"))
    dep = await gate.claim_for_execution(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT)
    assert dep.execution_status == "running"
    assert dep.executed_at is not None
    assert "deployment_execute" in db.events()


@pytest.mark.unit
async def test_a_second_claim_on_the_same_approval_is_refused():
    """ONE APPROVAL, ONE DEPLOYMENT. Otherwise an approval is a standing licence."""
    db = _DB(_Dep(approval_status="approved", approved_by="bob"))
    await gate.claim_for_execution(db, deployment_id=str(db.dep.id), tenant_id=TENANT)
    with pytest.raises(DeploymentGateError, match="already ran") as e:
        await gate.claim_for_execution(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT)
    assert e.value.code == "already_executed"


@pytest.mark.unit
async def test_two_workers_racing_for_one_approval_produce_one_deployment():
    """The claim is a conditional UPDATE, not a read-then-write: both callers would
    pass a Python-level check and both would deploy."""
    db = _DB(_Dep(approval_status="approved", approved_by="bob"))
    ok = 0
    for _ in range(5):
        try:
            await gate.claim_for_execution(
                db, deployment_id=str(db.dep.id), tenant_id=TENANT)
            ok += 1
        except DeploymentGateError:
            pass
    assert ok == 1, f"{ok} of 5 racing claims succeeded"


@pytest.mark.unit
async def test_an_unapproved_deployment_cannot_be_claimed():
    db = _DB(_Dep(approval_status="pending"), claim_succeeds=False)
    with pytest.raises(DeploymentGateError, match="not been approved") as e:
        await gate.claim_for_execution(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT)
    assert e.value.code == "not_approved"


@pytest.mark.unit
async def test_a_rejected_deployment_cannot_be_claimed():
    db = _DB(_Dep(approval_status="rejected"), claim_succeeds=False)
    with pytest.raises(DeploymentGateError, match="was rejected"):
        await gate.claim_for_execution(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT)


@pytest.mark.unit
async def test_a_refused_claim_says_which_reason_it_was():
    """"Could not start" sends someone looking for an outage when the answer is that
    nobody approved it."""
    db = _DB(_Dep(approval_status="pending"), claim_succeeds=False)
    try:
        await gate.claim_for_execution(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT)
    except DeploymentGateError as exc:
        assert exc.code == "not_approved"
        assert "approved" in exc.reason


# -- outcomes ------------------------------------------------------------------


@pytest.mark.unit
async def test_a_failed_deployment_is_still_an_approved_one():
    """Collapsing execution into approval loses the fact that a human said yes."""
    db = _DB(_Dep(approval_status="approved", approved_by="bob",
                  executed_at=datetime.now(timezone.utc)))
    dep = await gate.record_outcome(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT, status="failed",
        outcome={"failing_stage": "Deploy to prod"})
    assert dep.execution_status == "failed"
    assert dep.approval_status == "approved"
    assert dep.outcome["failing_stage"] == "Deploy to prod"


@pytest.mark.unit
async def test_an_invented_outcome_is_refused():
    db = _DB(_Dep(approval_status="approved", approved_by="bob"))
    with pytest.raises(DeploymentGateError, match="not a deployment outcome"):
        await gate.record_outcome(
            db, deployment_id=str(db.dep.id), tenant_id=TENANT, status="probably_fine")


@pytest.mark.unit
async def test_the_run_it_produced_is_recorded_so_it_can_be_followed():
    db = _DB(_Dep(approval_status="approved", approved_by="bob"))
    dep = await gate.record_outcome(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT, status="running",
        external_id="4417", external_url="https://dev.azure.com/acme/_build/results?buildId=4417")
    assert dep.external_id == "4417"
    assert "4417" in dep.external_url


# -- lookups -------------------------------------------------------------------


@pytest.mark.unit
async def test_a_synthesised_id_is_a_clean_not_found_not_a_database_error():
    """Non-UUID ids reaching a UUID column raise a driver error, which surfaces as a
    500 on what is really a 404 — the same bug the artifact routes had."""
    db = _DB(_Dep())
    with pytest.raises(DeploymentGateError, match="No such deployment") as e:
        await gate.approve_deployment(
            db, deployment_id="story-42-deploy", tenant_id=TENANT, approver="bob")
    assert e.value.code == "not_found"


@pytest.mark.unit
async def test_a_missing_deployment_is_not_found():
    db = _DB(None)
    with pytest.raises(DeploymentGateError, match="No such deployment"):
        await gate.approve_deployment(
            db, deployment_id=str(uuid.uuid4()), tenant_id=TENANT, approver="bob")


# -- the audit record ----------------------------------------------------------


@pytest.mark.unit
async def test_the_audit_record_carries_what_was_approved():
    """The evidence of WHAT was approved must not depend on the row being unedited.

    An audit entry holding only an id proves a click happened, not what it agreed to.
    """
    db = _DB(_Dep(requested_by="alice", request={"pipeline_id": 9, "branch": "release"}))
    await gate.approve_deployment(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="bob")
    entry = [a for a in db.added if getattr(a, "event_type", "") == "deployment_approve"][0]
    assert entry.payload["request"] == {"pipeline_id": 9, "branch": "release"}
    assert entry.payload["environment"] == "prod"
    assert entry.payload["requested_by"] == "alice"
    assert entry.actor_id == "bob"


@pytest.mark.unit
async def test_a_rejection_records_its_reason():
    db = _DB(_Dep())
    await gate.reject_deployment(
        db, deployment_id=str(db.dep.id), tenant_id=TENANT, approver="bob",
        reason="failing security gate")
    entry = [a for a in db.added if getattr(a, "event_type", "") == "deployment_reject"][0]
    assert entry.payload["reason"] == "failing security gate"
