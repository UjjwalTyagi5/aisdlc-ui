"""SCIM 2.0 User request/response Pydantic schemas (REQ-M7-16).

Attributes follow RFC 7643 §4.1 User schema naming conventions:
  userName    — unique display identifier (e.g. email address)
  externalId  — the IdP-assigned stable identifier used for idempotent upserts
  active      — boolean soft-activate/deactivate flag
  emails      — optional list of email objects (RFC 7643 multi-value attribute)

SCIMUserCreate is the inbound payload from the IdP (Azure AD SCIM provisioning).
SCIMUserOut   is the response returned after provision/upsert — maps to RFC 7643 shape.
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


class SCIMUserCreate(BaseModel):
    """Inbound SCIM 2.0 User payload from the IdP.

    externalId is the IdP-stable stable key used for ON CONFLICT (external_id, tenant_id)
    upsert — it is NEVER the platform user.id (Pitfall 7, RESEARCH).
    """

    userName: str
    externalId: str
    active: bool = True
    emails: Optional[List[Any]] = None


class SCIMUserOut(BaseModel):
    """SCIM 2.0 User response — RFC 7643 §4.1 shape.

    id         — platform user.id (UUID str)
    externalId — IdP-assigned stable identifier echoed back
    userName   — display identifier
    active     — current active flag
    """

    id: str
    userName: str
    externalId: str
    active: bool
