import uuid

import pytest

from shared.services.eval_gate import run_evaluation, latest_passing_evaluation


@pytest.mark.asyncio
async def test_run_evaluation_persists_and_returns_the_row():
    tenant = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    row = await run_evaluation(
        tenant_id=tenant, target_type="profile", target_id=target_id,
        agent_id="requirements", scope="org",
        body="acceptance criteria, stakeholder, scope, user stories all covered",
        evaluator_id="user-1", evaluator_role="developer",
    )
    assert row["result"] == "pass"
    assert row["evaluator_id"] == "user-1"


@pytest.mark.asyncio
async def test_run_evaluation_fail_result_for_thin_body():
    tenant = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    row = await run_evaluation(
        tenant_id=tenant, target_type="profile", target_id=target_id,
        agent_id="requirements", scope="org", body="short",
        evaluator_id="user-1", evaluator_role="developer",
    )
    assert row["result"] == "fail"


@pytest.mark.asyncio
async def test_latest_passing_evaluation_none_when_no_pass_exists():
    tenant = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    await run_evaluation(
        tenant_id=tenant, target_type="profile", target_id=target_id,
        agent_id="requirements", scope="org", body="short",
        evaluator_id="user-1", evaluator_role="developer",
    )
    result = await latest_passing_evaluation(tenant, "profile", target_id)
    assert result is None


@pytest.mark.asyncio
async def test_latest_passing_evaluation_scoped_to_exact_target_id():
    tenant = str(uuid.uuid4())
    target_a = str(uuid.uuid4())
    target_b = str(uuid.uuid4())
    body = "acceptance criteria, stakeholder, scope, user stories all covered"
    await run_evaluation(
        tenant_id=tenant, target_type="profile", target_id=target_a,
        agent_id="requirements", scope="org", body=body,
        evaluator_id="user-1", evaluator_role="developer",
    )
    result = await latest_passing_evaluation(tenant, "profile", target_b)
    assert result is None  # a PASS on target_a must not satisfy a check for target_b
