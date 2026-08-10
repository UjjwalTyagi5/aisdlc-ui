"""D-05: Activity idempotency — re-running the same activity returns the same artifact.

Tier: integration (ActivityEnvironment + live Postgres — requires DB + LiteLLM)
Criteria: D-05 — upsert by (run_id, agent_type, version) prevents duplicate artifacts

D-05 idempotency relies on Postgres upsert: a Run row must exist in the DB so the
artifact can be persisted and retrieved on the second call. Requires POSTGRES_CONN_STRING
and LITELLM_API_KEY (first call invokes the LLM when no artifact is cached).
"""
from __future__ import annotations

import pytest

from config.env import LITELLM_API_KEY, POSTGRES_CONN_STRING
from tests.temporal.conftest import make_run_id, make_workflow_input

# ---------------------------------------------------------------------------
# Import probe — skip if temporalio absent
# ---------------------------------------------------------------------------
try:
    from temporalio.testing import ActivityEnvironment
    _TEMPORALIO_AVAILABLE = True
except ImportError:
    _TEMPORALIO_AVAILABLE = False

_skip_no_temporal = pytest.mark.skipif(
    not _TEMPORALIO_AVAILABLE,
    reason="temporalio not installed",
)

# DB guard — idempotency upsert requires a Postgres Run row to cache the artifact.
_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — D-05 idempotency requires a live DB",
)

# Live-agent guard — first ActivityEnvironment call invokes the requirements agent via LiteLLM.
_skip_no_litellm = pytest.mark.skipif(
    not LITELLM_API_KEY,
    reason="LITELLM_API_KEY not set — litellm-proxy not configured; skipping live-agent temporal tests",
)


# ---------------------------------------------------------------------------
# D-05 idempotency test
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_temporal
@_skip_no_db
@_skip_no_litellm
async def test_activity_idempotency():
    """Re-executing run_requirements_activity with the same input returns the same artifact.

    Contract (D-05): the activity performs an upsert keyed on
    (run_id, agent_type, version).  Running it twice must not create
    duplicate artifacts and must return equal results.

    Tier: integration — requires a live DB (Postgres) so persist_artifact can
    cache the artifact after the first LLM call; without the Run row the upsert
    silently no-ops and the second call re-invokes the LLM with a different result.
    """
    # Import inside the test body — missing module yields xfail, not error
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]

    env = ActivityEnvironment()
    run_id = make_run_id()
    workflow_input = make_workflow_input(run_id=run_id)

    result_first = await env.run(run_requirements_activity, workflow_input)
    result_second = await env.run(run_requirements_activity, workflow_input)

    # Both executions must return the same artifact (idempotent upsert)
    assert result_first == result_second, (
        "D-05 violation: re-running the same activity returned different artifacts"
    )
    # run_id must be preserved in the artifact
    assert result_first.agent_session_id == run_id, (
        "Artifact must record the run_id it was created for"
    )


@pytest.mark.integration
@_skip_no_temporal
@_skip_no_db
@_skip_no_litellm
async def test_activity_idempotency_different_run_ids_produce_different_artifacts():
    """Two distinct run_ids must produce two distinct artifacts (no cross-contamination)."""
    from workflows.activities.requirements_activity import run_requirements_activity  # type: ignore[import]

    env = ActivityEnvironment()
    input_a = make_workflow_input(run_id=make_run_id())
    input_b = make_workflow_input(run_id=make_run_id())

    result_a = await env.run(run_requirements_activity, input_a)
    result_b = await env.run(run_requirements_activity, input_b)

    assert result_a.agent_session_id != result_b.agent_session_id, (
        "D-05: different run_ids must produce distinct artifact session IDs"
    )
