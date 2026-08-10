"""Upstream mirror field-widening tests (Task 3).

test_documentation_artifacts_is_patchable: pure assertion, no DB needed — always runs.
test_mirror_round_trip: requires a live Postgres DB session fixture; marked integration
so CI doesn't fail when infra is absent. Run it in the live-verification task (Task 17).
"""
import pytest
from shared.services.agent_session_store import (
    upsert_agent_session,
    fetch_session_artifacts,
    PATCHABLE_ARTIFACT_FIELDS,
)


def test_documentation_artifacts_is_patchable():
    assert "documentation_artifacts" in PATCHABLE_ARTIFACT_FIELDS


@pytest.mark.integration
@pytest.mark.skip(reason="Requires 'db' fixture with live Postgres — run in Task 17 live-verification")
@pytest.mark.asyncio
async def test_mirror_round_trip(db):  # `db` fixture provides a clean test DB session
    run_id = "11111111-1111-1111-1111-111111111111"
    await upsert_agent_session(
        run_id, agent_type="design", tenant_id=None,
        requirements_payload={"stories": [{"id": "S1"}]},
    )
    got = await fetch_session_artifacts(run_id)
    assert got and got["requirements_payload"] == {"stories": [{"id": "S1"}]}
