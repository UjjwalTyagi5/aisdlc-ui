"""CSRF-safe OAuth state parameter signing for Wave C connector onboarding.

Uses stdlib hmac + secrets only — no itsdangerous dependency (avoids external
package install; security is equivalent when hmac.compare_digest is used).

State envelope: base64url({p: <json_payload>, s: <hmac_sha256_hex>})
Payload: {nonce, tid, kind, exp}
HMAC key: JWT_SECRET_KEY (config.env — never os.getenv).

T-7.4-16: HMAC-SHA256 + compare_digest prevents state forgery.
T-7.4-17: tid bound in state + verify_oauth_state asserts tid == expected_tenant_id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from config.env import JWT_SECRET_KEY

_OAUTH_STATE_WINDOW_SECONDS = 600  # 10-minute OAuth flow window


def _sign_payload(payload_str: str) -> str:
    """Return HMAC-SHA256 hex digest of payload_str using JWT_SECRET_KEY."""
    return hmac.new(
        JWT_SECRET_KEY.encode(),
        payload_str.encode(),
        hashlib.sha256,
    ).hexdigest()


def mint_oauth_state(tenant_id: str, connector_kind: str) -> str:
    """Generate a CSRF-safe OAuth state parameter bound to tenant_id.

    Returns a base64url-encoded string containing a signed {nonce, tid, kind, exp}
    payload. The signature is HMAC-SHA256 over the JSON payload using JWT_SECRET_KEY.
    State window is 10 minutes (600 seconds).
    """
    nonce = secrets.token_hex(16)
    exp = int(time.time()) + _OAUTH_STATE_WINDOW_SECONDS
    payload_dict = {
        "nonce": nonce,
        "tid": tenant_id,
        "kind": connector_kind,
        "exp": exp,
    }
    payload_str = json.dumps(payload_dict, separators=(",", ":"))
    sig = _sign_payload(payload_str)
    raw = json.dumps({"p": payload_str, "s": sig}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def verify_oauth_state(state: str, expected_tenant_id: str) -> dict:
    """Verify state parameter; return payload dict on success.

    Raises ValueError:
    - on tampered signature (T-7.4-16, T-7.4-17)
    - on expired state
    - when payload.tid != expected_tenant_id (cross-tenant CSRF, T-7.4-17)
    - on malformed state
    """
    # Restore base64 padding stripped at mint time
    padded = state + "==" * ((4 - len(state) % 4) % 4)
    try:
        raw = json.loads(base64.urlsafe_b64decode(padded.encode()))
        payload_str: str = raw["p"]
        sig: str = raw["s"]
    except (KeyError, json.JSONDecodeError, Exception) as exc:
        raise ValueError(f"Malformed OAuth state: {exc}") from exc

    expected_sig = _sign_payload(payload_str)
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("OAuth state signature invalid — possible CSRF (T-7.4-16)")

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed OAuth state payload: {exc}") from exc

    if payload.get("exp", 0) < time.time():
        raise ValueError("OAuth state expired")

    if payload.get("tid") != expected_tenant_id:
        raise ValueError(
            f"OAuth state tenant_id mismatch — possible CSRF (T-7.4-17): "
            f"state.tid={payload.get('tid')!r} != expected={expected_tenant_id!r}"
        )

    return payload
