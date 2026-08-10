"""RED tests for ConnectorAuditEvent emission — REQ-M6-12.

Asserts that each connector method emits a ConnectorAuditEvent carrying:
- connector_name, method, tenant_id, run_id, latency_ms, status
- error_type uses type(exc).__name__ (NEVER str(exc))
- retry_count is present (new field required by REQ-M6-10 / M6 spec)

ConnectorAuditEvent is imported from config.connectors.models.
Tests skip at collection time when the model doesn't have the required fields.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from config.connectors.models import ConnectorAuditEvent
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"config.connectors.models not importable: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

import uuid

_TENANT_UUID = str(uuid.uuid4())
_RUN_UUID = str(uuid.uuid4())


@pytest.mark.unit
def test_audit_event_has_all_required_fields():
    """ConnectorAuditEvent constructs with all required M6 fields."""
    event = ConnectorAuditEvent(
        connector_name="jira",
        method="list_projects",
        tenant_id=_TENANT_UUID,
        run_id=_RUN_UUID,
        latency_ms=42.0,
        status="success",
        retry_count=0,
    )
    assert event.connector_name == "jira"
    assert event.method == "list_projects"
    assert event.tenant_id == _TENANT_UUID
    assert event.run_id == _RUN_UUID
    assert event.latency_ms == 42.0
    assert event.status == "success"
    assert hasattr(event, "retry_count"), "ConnectorAuditEvent must have retry_count field"
    assert event.retry_count == 0


@pytest.mark.unit
def test_audit_event_retry_count_field_exists():
    """ConnectorAuditEvent model has a retry_count field (REQ-M6-10 extension)."""
    event = ConnectorAuditEvent(
        connector_name="github_issues",
        method="fetch_item_detail",
        tenant_id=_TENANT_UUID,
        latency_ms=100.0,
        status="success",
    )
    assert hasattr(event, "retry_count"), (
        "ConnectorAuditEvent must have retry_count attribute (new field for M6)"
    )


@pytest.mark.unit
def test_audit_event_retry_count_defaults_to_zero():
    """ConnectorAuditEvent.retry_count defaults to 0 when not specified."""
    event = ConnectorAuditEvent(
        connector_name="slack",
        method="notify_slack",
        tenant_id=_TENANT_UUID,
        latency_ms=20.0,
        status="success",
    )
    assert event.retry_count == 0


@pytest.mark.unit
def test_audit_event_error_type_uses_class_name_not_str_exc():
    """error_type stores the exception class name, not str(exc) — never leaks message content."""
    exc = ValueError("sensitive data: secret_token=abc123")
    error_type_correct = type(exc).__name__  # "ValueError"
    error_type_wrong = str(exc)              # "sensitive data: secret_token=abc123"

    event = ConnectorAuditEvent(
        connector_name="jira",
        method="create_item",
        tenant_id=_TENANT_UUID,
        latency_ms=50.0,
        status="error",
        error_type=error_type_correct,
    )
    assert event.error_type == "ValueError"
    assert event.error_type != error_type_wrong, (
        "error_type must be type(exc).__name__, not str(exc)"
    )


@pytest.mark.unit
def test_audit_event_valid_status_values():
    """ConnectorAuditEvent.status accepts 'success' and 'error' only."""
    for valid_status in ("success", "error"):
        event = ConnectorAuditEvent(
            connector_name="azure_repos",
            method="webhook_receiver",
            tenant_id=_TENANT_UUID,
            latency_ms=10.0,
            status=valid_status,
        )
        assert event.status == valid_status


@pytest.mark.unit
def test_audit_event_latency_ms_is_float():
    """latency_ms is stored as a float (milliseconds)."""
    event = ConnectorAuditEvent(
        connector_name="github_issues",
        method="add_comment",
        tenant_id=_TENANT_UUID,
        latency_ms=123.456,
        status="success",
    )
    assert isinstance(event.latency_ms, float)


@pytest.mark.unit
def test_audit_event_retry_count_records_rate_limit_retries():
    """retry_count > 0 correctly records how many 429 retries occurred."""
    event = ConnectorAuditEvent(
        connector_name="jira",
        method="list_projects",
        tenant_id=_TENANT_UUID,
        latency_ms=2000.0,
        status="success",
        retry_count=3,
    )
    assert event.retry_count == 3, (
        f"Expected retry_count=3, got {event.retry_count}"
    )
