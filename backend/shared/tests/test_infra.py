"""M1 infrastructure test suite — covers TM1-001 through TM1-008.

Unit tests (no live infra): marked @pytest.mark.unit — always run.
Integration tests (require docker-compose services): marked @pytest.mark.integration
— skipped automatically when relevant env vars are unset.
"""
import os
import subprocess
import pathlib
import uuid

import pytest
from sqlalchemy import text

from config.env import (
    POSTGRES_CONN_STRING,
    POSTGRES_MIGRATIONS_CONN_STRING,
    REDIS_URL,
    AZURE_BLOB_ACCOUNT_URL,
    AZURE_KEY_VAULT_URL,
)

# ---------------------------------------------------------------------------
# UNIT TESTS — no live infrastructure required
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_docker_compose_valid():
    """TM1-002: docker-compose.yml exists and declares postgres and redis."""
    import yaml

    compose_path = pathlib.Path(__file__).parents[3] / "docker-compose.yml"
    assert compose_path.exists(), f"docker-compose.yml not found at {compose_path}"
    with compose_path.open() as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {})
    assert "postgres" in services, "postgres service missing from docker-compose.yml"
    assert "redis" in services, "redis service missing from docker-compose.yml"


@pytest.mark.unit
def test_env_vars_defined():
    """TM1-001: all 5 new env var constants are importable from config.env."""
    # Imports at top of file already validate importability; assert types here
    assert isinstance(POSTGRES_CONN_STRING, str)
    assert isinstance(POSTGRES_MIGRATIONS_CONN_STRING, str)
    assert isinstance(REDIS_URL, str)
    assert isinstance(AZURE_BLOB_ACCOUNT_URL, str)
    assert isinstance(AZURE_KEY_VAULT_URL, str)
    # POSTGRES_MIGRATIONS_CONN_STRING defaults to POSTGRES_CONN_STRING when that is set
    if POSTGRES_CONN_STRING:
        assert POSTGRES_MIGRATIONS_CONN_STRING == POSTGRES_CONN_STRING or POSTGRES_MIGRATIONS_CONN_STRING != ""


@pytest.mark.unit
def test_orm_models_defined():
    """TM1-004: all 7 ORM models are defined with correct table names and tenant_id columns."""
    from shared.models.orm import Base, Organization, Workspace, Project, Run, Artifact, AuditEvent, AgentCallLog

    assert Base.metadata.tables.keys() == {
        "organizations",
        "workspaces",
        "projects",
        "runs",
        "artifacts",
        "audit_events",
        "agent_call_logs",
    }
    # tenant_id present on tenant-scoped tables
    assert "tenant_id" in Project.__table__.c
    assert "tenant_id" in Run.__table__.c
    assert "tenant_id" in Artifact.__table__.c
    assert "tenant_id" in AuditEvent.__table__.c
    assert "tenant_id" in AgentCallLog.__table__.c
    # tenant_id absent on org-level tables
    assert "tenant_id" not in Organization.__table__.c
    assert "tenant_id" not in Workspace.__table__.c
    # append-only tables have no updated_at
    assert "updated_at" not in AuditEvent.__table__.c
    assert "updated_at" not in AgentCallLog.__table__.c
    # Artifact is immutable after creation
    assert "updated_at" not in Artifact.__table__.c


@pytest.mark.unit
def test_blob_client_importable():
    """TM1-003 (import side): BlobStorageClient is importable with expected async methods."""
    from shared.storage.azure_blob import BlobStorageClient

    assert hasattr(BlobStorageClient, "upload_bytes")
    assert hasattr(BlobStorageClient, "download_bytes")
    assert hasattr(BlobStorageClient, "get_url")
    assert hasattr(BlobStorageClient, "close")


@pytest.mark.unit
def test_bicep_file_exists():
    """TM1-007: infra/main.bicep and infra/modules/postgres.bicep exist in the repo."""
    repo_root = pathlib.Path(__file__).parents[3]
    assert (repo_root / "infra" / "main.bicep").exists(), "infra/main.bicep not found"
    assert (repo_root / "infra" / "modules" / "postgres.bicep").exists(), \
        "infra/modules/postgres.bicep not found"


# ---------------------------------------------------------------------------
# INTEGRATION TESTS — require docker-compose services
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_postgres_connection(db_session):
    """TM1-001 + TM1-006: live Postgres connection via async SQLAlchemy."""
    result = await db_session.execute(text("SELECT 1"))
    scalar = result.scalar()
    assert scalar == 1


@pytest.mark.integration
async def test_alembic_migration_cycle():
    """TM1-005: full Alembic upgrade → downgrade → upgrade cycle returns exit 0 each time."""
    if not POSTGRES_MIGRATIONS_CONN_STRING:
        pytest.skip("POSTGRES_MIGRATIONS_CONN_STRING not set")

    agentic_app_dir = pathlib.Path(__file__).parents[2]
    env = {**os.environ, "POSTGRES_MIGRATIONS_CONN_STRING": POSTGRES_MIGRATIONS_CONN_STRING}

    def run(cmd):
        result = subprocess.run(cmd, cwd=str(agentic_app_dir), env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, \
            f"alembic {' '.join(cmd[1:])} failed:\n{result.stdout}\n{result.stderr}"

    run(["alembic", "downgrade", "base"])
    run(["alembic", "upgrade", "head"])
    run(["alembic", "downgrade", "-1"])
    run(["alembic", "upgrade", "head"])


@pytest.mark.integration
async def test_redis_streams_produce_consume(redis_client):
    """TM1-002: Redis Streams produce/consume round-trip."""
    stream_key = f"tasks:test-stream-{uuid.uuid4().hex[:8]}"
    group_name = "test-group"

    # Produce
    msg_id = await redis_client.xadd(stream_key, {"key": "value1"})

    # Create consumer group
    try:
        await redis_client.xgroup_create(stream_key, group_name, "$", mkstream=True)
    except Exception:
        pass  # BUSYGROUP — group already exists

    # Reset group to start of stream so we can read the message we just added
    await redis_client.xgroup_setid(stream_key, group_name, "0")

    # Consume
    messages = await redis_client.xreadgroup(
        group_name, "consumer-1", {stream_key: ">"}, count=1
    )
    assert messages and len(messages[0][1]) >= 1, "No messages received from stream"

    # Ack + cleanup
    await redis_client.xack(stream_key, group_name, msg_id)
    await redis_client.delete(stream_key)


@pytest.mark.integration
def test_health_endpoint():
    """TM1-006: /health endpoint returns 200 with all required keys."""
    from fastapi.testclient import TestClient
    from process_api import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert "postgres" in data
    assert "redis" in data
    assert "blob" in data
    assert "status" in data
    assert "probed_at" in data
    assert "runtime_mode" in data
    assert data["status"] in ("healthy", "degraded")


@pytest.mark.integration
async def test_blob_upload_download(blob_client_fixture):
    """TM1-003: blob upload → download round-trip against live Azure Blob Storage."""
    blob_name = f"test/{uuid.uuid4()}/test-artifact.json"
    test_data = b'{"test": "m1-infra", "verified": true}'
    try:
        url = await blob_client_fixture.upload_bytes(test_data, blob_name,
                                                      content_type="application/json")
        assert url.startswith("https://"), f"Expected HTTPS URL, got: {url}"
        downloaded = await blob_client_fixture.download_bytes(blob_name)
        assert downloaded == test_data
    finally:
        # Best-effort cleanup — don't let cleanup failure fail the test
        try:
            await blob_client_fixture._client.delete_blob(
                blob_client_fixture._container, blob_name
            )
        except Exception:
            pass


@pytest.mark.integration
async def test_key_vault_read():
    """TM1-008: load_secret returns without raising when AZURE_KEY_VAULT_URL is set."""
    if not AZURE_KEY_VAULT_URL:
        pytest.skip("AZURE_KEY_VAULT_URL not set")
    from shared.keyvault import load_secret

    # Result may be None (secret not yet created) or a string — either is acceptable
    result = await load_secret("sdlc-dev-postgres-conn-string")
    assert result is None or isinstance(result, str)


@pytest.mark.integration
async def test_tenant_rls_isolation(db_session):
    """ADR-009 RLS: a row with tenant_id=A is not visible when current_tenant=B."""
    from shared.models.orm import Base

    # Skip gracefully if tables don't exist (no migration applied yet)
    tables = {t for t in Base.metadata.tables}
    try:
        await db_session.execute(text("SELECT 1 FROM organizations LIMIT 1"))
    except Exception:
        pytest.skip("Schema not initialised — run alembic upgrade head first")

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    # Insert minimal org + workspace + project for tenant_a
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    project_id = uuid.uuid4()

    await db_session.execute(text(
        "INSERT INTO organizations (id, slug, display_name) "
        "VALUES (:id, :slug, :name)"
    ), {"id": str(org_id), "slug": f"test-org-{org_id.hex[:8]}", "name": "Test Org"})

    await db_session.execute(text(
        "INSERT INTO workspaces (id, organization_id, slug, display_name) "
        "VALUES (:id, :org_id, :slug, :name)"
    ), {"id": str(ws_id), "org_id": str(org_id),
        "slug": f"test-ws-{ws_id.hex[:8]}", "name": "Test Workspace"})

    await db_session.execute(text(
        "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
        "VALUES (:id, :ws_id, :tenant_id, :name, 'azure_devops')"
    ), {"id": str(project_id), "ws_id": str(ws_id),
        "tenant_id": str(tenant_a), "name": "Test Project"})

    await db_session.flush()

    # With tenant_a context — project should be visible
    await db_session.execute(text(f"SET app.current_tenant_id = '{tenant_a}'"))
    rows_a = (await db_session.execute(
        text("SELECT id FROM projects WHERE tenant_id = :tid"),
        {"tid": str(tenant_a)}
    )).fetchall()
    assert len(rows_a) >= 1, "Project not visible under its own tenant context"

    # With tenant_b context — project must NOT be visible
    await db_session.execute(text(f"SET app.current_tenant_id = '{tenant_b}'"))
    rows_b = (await db_session.execute(
        text("SELECT id FROM projects WHERE tenant_id = :tid"),
        {"tid": str(tenant_a)}
    )).fetchall()
    assert len(rows_b) == 0, "RLS isolation failure: project visible under wrong tenant"

    # Rollback so test data doesn't persist
    await db_session.rollback()
