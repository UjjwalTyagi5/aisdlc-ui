"""Per-organization settings — SSO, MFA policy, session lifetime.

THE CLIENT SECRET NEVER TRANSITS THIS MODULE'S RETURN VALUES
------------------------------------------------------------
`update_sso` accepts a plaintext secret, hands it straight to the secret store, and
records only the reference. `get_settings` returns `hasClientSecret: bool` — not the
reference and certainly not the value. A settings page needs to know whether a secret
is configured; it never needs to read one back, and an endpoint that can return it is
an endpoint that can leak it.

That also means there is no "show current secret" affordance to build later. Rotating
is setting a new one.

DEFAULTS ARE RETURNED, NOT WRITTEN
----------------------------------
An organization with no row gets the defaults rather than a row created on first read.
Reads stay reads: a GET that writes turns every settings page load into a row insert,
and makes "has this org ever been configured?" unanswerable.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.secret_store import put_secret

logger = logging.getLogger(__name__)

# The reference under which an org's Entra client secret is stored. One per tenant,
# and derived rather than caller-supplied so a request cannot point the row at another
# tenant's secret.
ENTRA_SECRET_REF = "entra-client-secret"

DEFAULT_SESSION_TIMEOUT_MINUTES = 480
MIN_SESSION_TIMEOUT_MINUTES = 5
MAX_SESSION_TIMEOUT_MINUTES = 10080  # one week


class OrgSettingsError(Exception):
    """Invalid settings input. Carries the HTTP status the router should use."""

    http_status = 422


async def get_settings(session: AsyncSession, tenant_id: str) -> dict[str, Any]:
    """Current settings for this org, or the defaults when none have been saved."""
    row = (await session.execute(
        text(
            "SELECT entra_tenant_id, entra_client_id, entra_client_secret_ref, "
            "       mfa_required, session_timeout_minutes, updated_by, updated_at "
            "FROM org_settings WHERE tenant_id = CAST(:t AS uuid)"
        ),
        {"t": tenant_id},
    )).first()

    if row is None:
        return {
            "entraTenantId": None,
            "entraClientId": None,
            "hasClientSecret": False,
            "mfaRequired": False,
            "sessionTimeoutMinutes": DEFAULT_SESSION_TIMEOUT_MINUTES,
            "ssoConfigured": False,
            "updatedBy": None,
            "updatedAt": None,
        }

    return {
        "entraTenantId": row.entra_tenant_id,
        "entraClientId": row.entra_client_id,
        # Whether a secret exists — never the reference, never the value.
        "hasClientSecret": bool(row.entra_client_secret_ref),
        "mfaRequired": bool(row.mfa_required),
        "sessionTimeoutMinutes": int(row.session_timeout_minutes),
        # SSO is usable only with all three parts. Reported as one flag so a caller
        # cannot conclude "configured" from a tenant id alone and then fail at login.
        "ssoConfigured": bool(
            row.entra_tenant_id and row.entra_client_id and row.entra_client_secret_ref
        ),
        "updatedBy": row.updated_by,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


async def update_sso(
    session: AsyncSession,
    *,
    tenant_id: str,
    updated_by: Optional[str],
    entra_tenant_id: Optional[str] = None,
    entra_client_id: Optional[str] = None,
    entra_client_secret: Optional[str] = None,
    mfa_required: Optional[bool] = None,
    session_timeout_minutes: Optional[int] = None,
) -> dict[str, Any]:
    """Partial update. Only the fields provided are changed.

    `None` means "leave alone", which is what makes this a PATCH. Clearing a value is
    therefore not expressible here, and deliberately so: the destructive edits
    (removing SSO, dropping MFA) should be explicit acts rather than a side effect of
    omitting a field from a form payload.
    """
    if session_timeout_minutes is not None and not (
        MIN_SESSION_TIMEOUT_MINUTES <= session_timeout_minutes <= MAX_SESSION_TIMEOUT_MINUTES
    ):
        raise OrgSettingsError(
            f"session_timeout_minutes must be between {MIN_SESSION_TIMEOUT_MINUTES} "
            f"and {MAX_SESSION_TIMEOUT_MINUTES}"
        )

    secret_ref: Optional[str] = None
    if entra_client_secret:
        # Straight to the secret store; only the reference reaches the row below. Done
        # BEFORE the row write so a failure to persist the secret cannot leave settings
        # claiming a credential that was never stored.
        await put_secret(tenant_id, ENTRA_SECRET_REF, entra_client_secret)
        secret_ref = ENTRA_SECRET_REF

    await session.execute(
        text(
            "INSERT INTO org_settings "
            "  (tenant_id, entra_tenant_id, entra_client_id, entra_client_secret_ref, "
            "   mfa_required, session_timeout_minutes, updated_by, updated_at) "
            # Every nullable parameter is CAST explicitly. asyncpg infers a parameter's
            # type from its use, and COALESCE($1, $2) with a NULL first argument gives
            # it nothing to infer from — it falls back to text and Postgres rejects the
            # insert against an integer column.
            "VALUES (CAST(:t AS uuid), :etid, :ecid, :ref, "
            "        COALESCE(CAST(:mfa AS boolean), false), "
            "        COALESCE(CAST(:timeout AS integer), CAST(:default_timeout AS integer)), "
            "        :by, now()) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            # COALESCE keeps the stored value when the field was not supplied — this is
            # what makes the update partial rather than a full replace that silently
            # blanks everything the form did not send.
            "  entra_tenant_id = COALESCE(EXCLUDED.entra_tenant_id, org_settings.entra_tenant_id), "
            "  entra_client_id = COALESCE(EXCLUDED.entra_client_id, org_settings.entra_client_id), "
            "  entra_client_secret_ref = COALESCE(EXCLUDED.entra_client_secret_ref, "
            "                                     org_settings.entra_client_secret_ref), "
            "  mfa_required = COALESCE(CAST(:mfa AS boolean), org_settings.mfa_required), "
            "  session_timeout_minutes = COALESCE(CAST(:timeout AS integer), "
            "                                     org_settings.session_timeout_minutes), "
            "  updated_by = EXCLUDED.updated_by, "
            "  updated_at = now()"
        ),
        {
            "t": tenant_id,
            "etid": entra_tenant_id,
            "ecid": entra_client_id,
            "ref": secret_ref,
            "mfa": mfa_required,
            "timeout": session_timeout_minutes,
            "default_timeout": DEFAULT_SESSION_TIMEOUT_MINUTES,
            "by": updated_by,
        },
    )

    # Logged without any secret material — the fact of a rotation, not its content.
    logger.info(
        "org settings updated: tenant=%s by=%s sso_fields=%s mfa=%s timeout=%s secret_rotated=%s",
        tenant_id, updated_by,
        bool(entra_tenant_id or entra_client_id), mfa_required,
        session_timeout_minutes, bool(entra_client_secret),
    )
    return await get_settings(session, tenant_id)
