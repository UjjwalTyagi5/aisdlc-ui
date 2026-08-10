"""Slack webhook signature verifier — REQ-M6-06.

Slack uses v0:{timestamp}:{body} HMAC-SHA256 with a 5-minute replay window.
Uses slack_sdk.signature.SignatureVerifier — the official library handles the
prefix format and replay window correctly (see RESEARCH.md Pattern 2).
"""
from __future__ import annotations

import hmac
import hashlib
import time


async def verify_slack_signature(
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    signing_secret: str,
) -> None:
    """Verify Slack's X-Slack-Signature header with replay-window enforcement.

    Raises ValueError if the signature is invalid or the timestamp is stale
    (older than 5 minutes — T-m6-06-R replay attack mitigation).
    """
    if not timestamp or not signature:
        raise ValueError("Slack signature or timestamp header missing")

    # Reject stale timestamps (replay attack prevention — 5-min window)
    try:
        ts_int = int(timestamp)
    except (ValueError, TypeError):
        raise ValueError("Slack X-Slack-Request-Timestamp is not a valid integer")

    if abs(time.time() - ts_int) > 300:
        raise ValueError("Slack signature timestamp is stale (replay attack protection)")

    # Compute expected signature: v0:{timestamp}:{body}
    base_string = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    mac = hmac.new(
        signing_secret.encode("utf-8"),
        msg=base_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    expected = "v0=" + mac.hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise ValueError("Slack signature mismatch")
