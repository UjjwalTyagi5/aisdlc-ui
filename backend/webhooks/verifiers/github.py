"""GitHub webhook signature verifier — REQ-M6-06.

GitHub uses HMAC-SHA256 over the raw request body; header value is ``sha256=<hex>``.
Always uses constant-time comparison to prevent timing oracle attacks (T-m6-06-ID).
"""
from __future__ import annotations

import hashlib
import hmac


async def verify_github_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> None:
    """Verify the X-Hub-Signature-256 header against the raw request body.

    Raises ValueError on missing or invalid signature; caller must return 400
    without leaking the reason (T-m6-06-S / T-m6-06-ID).
    """
    if not signature_header:
        raise ValueError("X-Hub-Signature-256 header missing")
    if not signature_header.startswith("sha256="):
        raise ValueError("X-Hub-Signature-256 must have sha256= prefix")
    mac = hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise ValueError("GitHub signature mismatch")
