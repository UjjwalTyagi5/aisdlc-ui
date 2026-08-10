"""Azure Repos / ADO webhook verifier — REQ-M6-06.

ADO service hooks do NOT include a cryptographic HMAC signature header.
Authentication is via HTTP Basic Auth (username:password) set at webhook registration
time. See RESEARCH.md Pattern 4 and Pitfall 4.

A2 ASSUMED: ADO Basic-Auth credential format is username:password encoded as base64;
the expected_user and expected_password are stored per-tenant in Key Vault.
Must verify against a real ADO service hook payload before production use.
"""
from __future__ import annotations

import base64
import hmac
import logging

logger = logging.getLogger(__name__)


async def verify_ado_signature(
    raw_body: bytes,
    auth_header: str | None,
    expected_user: str,
    expected_password: str,
) -> None:
    """Verify ADO service hook via Basic-Auth Authorization header.

    ADO uses Basic Auth (not HMAC) for webhook authentication (Pitfall 4).
    Raises ValueError on missing or mismatched credentials.

    If both expected_user and expected_password are empty strings, logs a warning
    and accepts permissively (testing mode — document in CapabilityManifest).
    """
    if not expected_user and not expected_password:
        logger.warning(
            "ADO webhook credentials not configured — accepting without auth (testing mode)"
        )
        return

    if not auth_header or not auth_header.startswith("Basic "):
        raise ValueError("Authorization: Basic header missing for ADO webhook")

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
    except Exception:
        raise ValueError("ADO webhook Authorization header is not valid base64")

    expected = f"{expected_user}:{expected_password}"
    # Constant-time comparison to prevent timing oracle attacks (T-m6-06-ID)
    if not hmac.compare_digest(decoded, expected):
        raise ValueError("ADO webhook Basic Auth mismatch")
