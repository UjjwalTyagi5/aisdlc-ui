"""Unit tests for AzureDevOpsConnector.audit_emitter (REQ-M3-09).

audit_emitter must persist a ConnectorAuditEvent as an AuditEvent row, and must
NEVER raise — DB errors are logged (type name only) and swallowed so auditing
cannot break the primary connector operation (T-M3-09).

DB access is fully mocked; no Postgres required. tenant_id must be a valid UUID
string because audit_emitter coerces it via uuid.UUID(...).
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.skip(
    reason="pre-existing import error — No module named 'azure' (azure-keyvault-secrets not installed); "
    "quarantined in milestone-4 Wave A"
)

try:
    from config.connectors.azure_devops import AzureDevOpsConnector
    from config.connectors.models import ConnectorAuditEvent
except (ImportError, ModuleNotFoundError):
    AzureDevOpsConnector = None
    ConnectorAuditEvent = None

_TENANT_UUID = str(uuid.uuid4())


def _mock_session_factory(commit_side_effect=None):
    """Build a patch target replacing AsyncSessionFactory with an async CM.

    Returns (factory_mock, session_mock). The factory is callable and yields an
    async context manager whose __aenter__ returns session_mock.
    """
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=commit_side_effect)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=cm)
    return factory, session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_event_emitted():
    """A successful audit_emitter call adds a row and commits."""
    factory, session = _mock_session_factory()
    connector = AzureDevOpsConnector("https://dev.azure.com/org")
    event = ConnectorAuditEvent(
        connector_name="azure_devops",
        method="list_projects",
        tenant_id=_TENANT_UUID,
        latency_ms=42.0,
        status="success",
    )
    with patch("shared.db.AsyncSessionFactory", factory):
        await connector.audit_emitter(event)
    assert session.add.called, "expected session.add to be called"
    assert session.commit.called, "expected session.commit to be called"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_audit_emitter_swallows_db_error():
    """A DB error during commit must be swallowed (no exception propagates)."""
    factory, session = _mock_session_factory(
        commit_side_effect=RuntimeError("DB error")
    )
    connector = AzureDevOpsConnector("https://dev.azure.com/org")
    event = ConnectorAuditEvent(
        connector_name="azure_devops",
        method="list_projects",
        tenant_id=_TENANT_UUID,
        latency_ms=42.0,
        status="error",
        error_type="RuntimeError",
    )
    with patch("shared.db.AsyncSessionFactory", factory):
        # Must not raise.
        await connector.audit_emitter(event)


@pytest.mark.unit
def test_connector_audit_event_schema():
    """ConnectorAuditEvent constructs with required fields and valid status."""
    event = ConnectorAuditEvent(
        connector_name="azure_devops",
        method="list_projects",
        tenant_id=_TENANT_UUID,
        latency_ms=10.0,
        status="success",
    )
    assert event.connector_name == "azure_devops"
    assert event.status in ("success", "error")


@pytest.mark.unit
def test_audit_event_error_type_no_str_exc():
    """error_type stores the exception type name only — never str(exc)."""
    event = ConnectorAuditEvent(
        connector_name="azure_devops",
        method="list_projects",
        tenant_id=_TENANT_UUID,
        latency_ms=10.0,
        status="error",
        error_type="RuntimeError",
    )
    assert event.error_type == "RuntimeError"
    assert event.model_dump()["error_type"] == "RuntimeError"
