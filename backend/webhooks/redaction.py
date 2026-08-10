"""Webhook payload redaction helper — REQ-M6-15 (T-m6-06-ID).

Redacts sensitive headers from webhook log records to prevent credential
leakage in log output. Safe for use in exception handlers and audit trails.
"""
from __future__ import annotations

import copy

# Headers that must always be redacted from logs (contain auth or HMAC secrets)
_SENSITIVE_HEADERS = frozenset({
    "authorization",
    "x-hub-signature",
    "x-hub-signature-256",
    "x-slack-signature",
    "x-slack-request-timestamp",  # not secret itself but combined with sig
})

_REDACTED = "[REDACTED]"


def redact_webhook_payload(payload: dict) -> dict:
    """Return a copy of the webhook payload with sensitive headers redacted.

    Only headers listed in _SENSITIVE_HEADERS are replaced with '[REDACTED]'.
    Non-sensitive headers and the body are preserved unchanged.
    """
    redacted = copy.deepcopy(payload)
    headers = redacted.get("headers", {})
    if isinstance(headers, dict):
        for key in list(headers.keys()):
            if key.lower() in _SENSITIVE_HEADERS:
                headers[key] = _REDACTED
    return redacted
