"""AuditEventPayload — Pydantic schema for all audit events emitted through AuditEventService.

All audit writes flow through AuditEventService which accepts this schema (D-02).
model_dump_json() output is used for Redis dead-letter stream serialization (D-M8-05).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AuditEventPayload(BaseModel):
    """Validated payload schema for a single audit event.

    run_id and agent_type are folded into the payload JSONB column at write time —
    the AuditEvent ORM has no dedicated run_id/agent_type columns (Pitfall 6 resolution:
    resource_id stores run_id; run_id + agent_type live inside payload JSONB).
    """

    tenant_id: str
    run_id: Optional[str] = None
    event_type: str
    agent_type: Optional[str] = None
    actor_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
