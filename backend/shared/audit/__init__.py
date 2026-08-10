"""shared.audit — Central audit infrastructure package.

Exports:
  AuditEventPayload    — Pydantic schema for all audit events
  AuditEventService    — Single write path (D-02); emit, emit_blocking, PII redaction
  audit_service        — Module-level singleton imported by callback_handler, connectors,
                         signals.py HITL path
  AuditCallbackHandler — LangGraph BaseCallbackHandler that intercepts tool/LLM events
                         (Plan 03 — both sync + async variants for astream coverage)
"""
from __future__ import annotations

from shared.audit.models import AuditEventPayload
from shared.audit.service import AuditEventService, audit_service
from shared.audit.callback_handler import AuditCallbackHandler

__all__ = [
    "AuditEventPayload",
    "AuditEventService",
    "audit_service",
    "AuditCallbackHandler",
]
