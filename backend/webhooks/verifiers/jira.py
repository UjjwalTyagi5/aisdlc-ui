"""Jira webhook signature verifier — REQ-M6-06.

Jira uses X-Hub-Signature with sha256= prefix, same WebSub standard as GitHub.
See RESEARCH.md Pattern 3 / RESEARCH.md Pitfall 4 (not ADO, which uses Basic Auth).
"""
from __future__ import annotations

import hashlib
import hmac


async def verify_jira_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> None:
    """Verify the X-Hub-Signature header against the raw request body.

    Raises ValueError on missing or invalid signature; caller must return 400
    without revealing auth semantics (T-m6-06-S / T-m6-06-ID).
    """
    if not signature_header:
        raise ValueError("X-Hub-Signature header missing")
    mac = hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise ValueError("Jira signature mismatch")
