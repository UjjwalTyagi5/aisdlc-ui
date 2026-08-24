"""Artifact persistence tests for M2-02 / M2-06 (D-11, D-13).

Verifies that RequirementsArtifact written via SQLAlchemy to runs.requirements_payload
is readable by a second session (no HANDOFF:: sentinel needed), and that the
artifact_service emits a Redis pub/sub event on write.

Both tests require Postgres + Redis and skip cleanly when either is absent.
"""
import asyncio
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from config.env import POSTGRES_CONN_STRING, REDIS_URL

_SKIP_NO_INFRA = pytest.mark.skipif(
    not (POSTGRES_CONN_STRING and REDIS_URL),
    reason="POSTGRES_CONN_STRING or REDIS_URL not set — skipping artifact persistence tests",
)


@pytest.mark.integration
@_SKIP_NO_INFRA
@pytest.mark.asyncio
async def test_artifact_write_read_roundtrip():
    """RequirementsArtifact written in one session is readable from a fresh session.

    Uses two completely separate SQLAlchemy sessions (first is closed before
    second opens) to confirm the data was committed to Postgres, not just held
    in memory.
    """
    from sqlalchemy import delete as sa_delete
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run
    from shared.models.artifacts import RequirementsArtifact

    run_id = uuid.uuid4()
    # Run.tenant_id carries no FK (unlike project_id) — a fresh UUID needs no
    # organization/workspace seeding, just a real UUID so RLS's WITH CHECK on the
    # runs table is satisfied by the tenant-scoped session below.
    tenant_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    artifact = RequirementsArtifact(
        agent_session_id=session_id,
        brd_content="Test BRD content for roundtrip test",
        user_stories=[{"id": "US-001", "title": "Sample story"}],
        acceptance_criteria=["AC-001: System responds within 2s"],
        version=1,
    )

    try:
        # --- Write session --- get_db_session_for_tenant SETs LOCAL
        # app.current_tenant_id, which RLS's WITH CHECK on the runs table requires
        # to admit the INSERT at all.
        async with get_db_session_for_tenant(tenant_id) as write_session:
            run = Run(
                id=run_id,
                tenant_id=uuid.UUID(tenant_id),
                current_stage="requirements",
                requirements_payload=artifact.model_dump(),
            )
            write_session.add(run)

        # --- Read session (separate connection, same tenant GUC) ---
        async with get_db_session_for_tenant(tenant_id) as read_session:
            result = await read_session.execute(select(Run).where(Run.id == run_id))
            retrieved_run = result.scalar_one_or_none()

        assert retrieved_run is not None, f"Run {run_id} not found after commit"
        assert retrieved_run.requirements_payload is not None, "requirements_payload is None"

        read_artifact = RequirementsArtifact(**retrieved_run.requirements_payload)
        assert read_artifact.agent_session_id == session_id, (
            f"agent_session_id mismatch: expected '{session_id}', got '{read_artifact.agent_session_id}'"
        )
        assert read_artifact.brd_content == artifact.brd_content, (
            f"brd_content mismatch: expected '{artifact.brd_content}', got '{read_artifact.brd_content}'"
        )
        assert read_artifact.version == 1
    finally:
        async with get_db_session_for_tenant(tenant_id) as cleanup_session:
            await cleanup_session.execute(sa_delete(Run).where(Run.id == run_id))


@pytest.mark.integration
@_SKIP_NO_INFRA
@pytest.mark.asyncio
async def test_artifact_ready_redis_event():
    """artifact_service.write_artifact() emits artifact_ready event on Redis pub/sub.

    Subscribes to the artifact_events channel before triggering the write so no
    events are missed. Uses asyncio.wait_for with a 5-second timeout.
    """
    import redis.asyncio as aioredis
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run
    from shared.services.artifact_service import write_and_notify, _ARTIFACT_CHANNEL
    from shared.models.artifacts import RequirementsArtifact

    session_id = str(uuid.uuid4())
    artifact = RequirementsArtifact(
        agent_session_id=session_id,
        brd_content="Redis event test BRD",
        version=1,
    )
    tenant_id = str(uuid.uuid4())
    run_id = uuid.uuid4()

    # persist_artifact (which write_and_notify calls) UPDATES an existing run row —
    # it does not create one — so a Run must be seeded first, same tenant-scoped
    # session RLS requires for the earlier roundtrip test.
    async with get_db_session_for_tenant(tenant_id) as seed_session:
        seed_session.add(Run(id=run_id, tenant_id=uuid.UUID(tenant_id), current_stage="requirements"))

    received_event: dict | None = None

    async def _subscribe_and_capture():
        nonlocal received_event
        client = aioredis.from_url(REDIS_URL)
        pubsub = client.pubsub()
        await pubsub.subscribe(_ARTIFACT_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    received_event = json.loads(data)
                    return
        finally:
            await pubsub.unsubscribe(_ARTIFACT_CHANNEL)
            await client.aclose()

    # Start subscriber first, then trigger the write
    subscriber_task = asyncio.create_task(_subscribe_and_capture())
    # Brief yield to let subscriber register before the write fires
    await asyncio.sleep(0.1)

    try:
        await write_and_notify(
            run_id=str(run_id),
            artifact_type="requirements",
            artifact_data=artifact.model_dump(),
            tenant_id=tenant_id,
        )

        try:
            await asyncio.wait_for(subscriber_task, timeout=5.0)
        except asyncio.TimeoutError:
            subscriber_task.cancel()
            pytest.fail(
                "Timed out waiting for artifact_ready Redis event after 5 seconds. "
                "Check that artifact_service.write_and_notify publishes to the correct channel."
            )

        assert received_event is not None, "No event received on Redis channel"
        assert received_event.get("event") == "artifact_ready", (
            f"Expected event='artifact_ready', got: {received_event}"
        )
        assert received_event.get("artifact_type") == "requirements", (
            f"Expected artifact_type='requirements', got: {received_event}"
        )
    finally:
        from sqlalchemy import delete as sa_delete
        async with get_db_session_for_tenant(tenant_id) as cleanup_session:
            await cleanup_session.execute(sa_delete(Run).where(Run.id == run_id))
