"""Per-tenant SCIM bearer credential verification (REQ-M7-16, D-02).

verify_scim_credential:
  Reads the Authorization: Bearer {token} from the request,
  loads the per-tenant SCIM credential from Key Vault, and compares
  them with secrets.compare_digest (timing-safe, T-7.4-05).

  KV secret name convention: load_secret("scim-bearer-token", tenant_id=tenant_id)
  which resolves to KV secret '{tenant_id}-scim-bearer-token' (PATTERNS.md §Tenant-scoped
  KV naming convention; mirrors M6 connector secret isolation, D-02).

  Returns False (never raises) on:
    - absent/malformed Authorization header
    - no stored credential for the tenant (fail-closed)
    - token mismatch
  Returns True only on an exact timing-safe match.
  The caller (SCIM router) raises the HTTP 401 on False.
"""
from __future__ import annotations

import secrets

from fastapi import Request

from shared.keyvault import load_secret


async def verify_scim_credential(request: Request, tenant_id: str) -> bool:
    """Verify the per-tenant SCIM bearer credential.

    Fail-closed: returns False on absent/malformed header, missing stored
    credential, or any mismatch.  Only returns True on exact compare_digest match.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False

    provided_token = auth_header[len("Bearer "):]
    if not provided_token:
        return False

    stored_token = await load_secret("scim-bearer-token", tenant_id=tenant_id)
    if not stored_token:
        return False

    return secrets.compare_digest(provided_token, stored_token)
