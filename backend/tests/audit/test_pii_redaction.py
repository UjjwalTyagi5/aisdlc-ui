"""REQ-M8-04 — PII redaction unit tests.

Asserts that AuditEventService._redact_pii() replaces detected email addresses
with [REDACTED] before any write to the audit_events table. Raw PII is never stored.

D-M8-03 locked decision: Regex-based scan of payload fields before write; redact
detected patterns (emails). GDPR erasure = payload-field redaction, not row deletion.

Wave-2 turns these GREEN when shared/audit/service.py is implemented.
"""
from __future__ import annotations

import pytest

# Wave-2 implements shared/audit/service.py.
# Mark all tests xfail until that wave lands.
pytestmark = pytest.mark.xfail(
    reason="Wave 2 implements shared/audit/service.py with _redact_pii()",
    strict=False,
)


@pytest.mark.unit
def test_email_in_string_value_is_redacted():
    """An email address embedded in a string payload value is replaced with [REDACTED]."""
    from shared.audit.service import AuditEventService

    svc = AuditEventService()
    result = svc._redact_pii({"message": "User john.doe@example.com triggered the action"})

    assert "[REDACTED]" in result["message"]
    assert "john.doe@example.com" not in result["message"]


@pytest.mark.unit
def test_email_at_key_boundary_is_redacted():
    """Email in a dedicated 'email' key field is redacted."""
    from shared.audit.service import AuditEventService

    svc = AuditEventService()
    result = svc._redact_pii({"user_email": "alice@corp.io", "action": "login"})

    assert result["user_email"] == "[REDACTED]"
    assert result["action"] == "login"


@pytest.mark.unit
def test_no_email_payload_unchanged():
    """A payload with no email patterns passes through unmodified."""
    from shared.audit.service import AuditEventService

    svc = AuditEventService()
    payload = {"tool_name": "fetch_work_items", "run_id": "abc123", "count": 5}
    result = svc._redact_pii(payload)

    assert result == {"tool_name": "fetch_work_items", "run_id": "abc123", "count": 5}


@pytest.mark.unit
def test_nested_dict_email_is_redacted():
    """Email addresses in nested dict values are also redacted."""
    from shared.audit.service import AuditEventService

    svc = AuditEventService()
    result = svc._redact_pii({
        "actor": {"id": "u1", "email": "actor@tenant.com"},
        "event": "tool_call",
    })

    assert result["actor"]["email"] == "[REDACTED]"
    assert result["actor"]["id"] == "u1"
    assert result["event"] == "tool_call"


@pytest.mark.unit
def test_email_in_list_element_is_redacted():
    """Email addresses embedded in list string elements are redacted."""
    from shared.audit.service import AuditEventService

    svc = AuditEventService()
    result = svc._redact_pii({"mentions": ["cc: bob@example.com", "no email here"]})

    assert "[REDACTED]" in result["mentions"][0]
    assert "bob@example.com" not in result["mentions"][0]
    assert result["mentions"][1] == "no email here"


@pytest.mark.unit
def test_multiple_emails_in_one_field_all_redacted():
    """Multiple email addresses in a single string field are all redacted."""
    from shared.audit.service import AuditEventService

    svc = AuditEventService()
    result = svc._redact_pii({
        "log": "Sent to alice@a.com and bob@b.org for review"
    })

    assert "alice@a.com" not in result["log"]
    assert "bob@b.org" not in result["log"]
    assert result["log"].count("[REDACTED]") == 2


@pytest.mark.unit
async def test_emit_calls_redact_before_write(mock_audit_service):
    """emit() must call _redact_pii before writing to DB (integration of service methods).

    Wave-2 wires emit() → _redact_pii() → _write(). This test verifies the call order.
    """
    from shared.audit.service import AuditEventService
    from shared.audit.models import AuditEventPayload

    svc = AuditEventService()
    svc._write = mock_audit_service._write
    svc._send_to_dead_letter = mock_audit_service._send_to_dead_letter

    payload = AuditEventPayload(
        tenant_id="00000000-0000-0000-0000-000000000001",
        run_id="run-001",
        event_type="tool_call_start",
        agent_type="requirements",
        actor_id="system:requirements",
        payload={"message": "Invoked by user@example.com"},
    )

    await svc.emit(payload)

    mock_audit_service._write.assert_called_once()
    written_payload = mock_audit_service._write.call_args[0][0]
    assert "user@example.com" not in str(written_payload.payload)
