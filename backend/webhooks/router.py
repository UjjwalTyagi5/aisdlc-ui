"""Tenant-scoped webhook router — REQ-M6-05 / REQ-M6-06 / REQ-M6-07 / REQ-M6-08 / REQ-M6-09.

Security-first pipeline: verify → dedup → normalize → XADD.

POST /webhooks/{connector}/{tenant_id}

CRITICAL security requirements:
- Raw body read BEFORE any JSON parsing (Pitfall 1 avoidance)
- No Pydantic body parameter in route signature (would exhaust body stream)
- Signature verification BEFORE dedup (timing oracle prevention)
- Return 400 (never 401/403) for all auth failures (T-m6-06-ID — no auth semantics leak)
- Content-Length cap ~5MB before body read (T-m6-06-D DoS mitigation)
- /webhooks/ is NOT in _EXEMPT_PATHS for JWT middleware; signature IS the auth
  mechanism. Plan 07 adds the _EXEMPT_PATHS entry for process_api.py.
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from webhooks.dedup import is_duplicate_event
from webhooks.stream_producer import publish_webhook_event
from webhooks.verifiers import get_verifier
from webhooks.normalizers import get_normalizer
from shared.services.metrics import (
    WEBHOOK_DELIVERIES,
    WEBHOOK_PROCESSING_DURATION,
    WEBHOOK_DUPLICATES_REJECTED,
)

logger = logging.getLogger(__name__)

webhooks_router = APIRouter()

# Connectors that have registered verifiers and normalizers
_KNOWN_CONNECTORS = frozenset({"github", "jira", "azure_repos", "slack"})

# Content-Length hard cap: ~5 MB (T-m6-06-D DoS mitigation)
_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MiB


def _extract_event_id(connector: str, payload: dict, headers) -> str:
    """Extract the provider-specific delivery ID for deduplication.

    event_id sources per provider:
    - github:      X-GitHub-Delivery header
    - jira:        X-Atlassian-Webhook-Identifier header
    - slack:       event_id field in JSON body [A1 ASSUMED]
    - azure_repos: top-level 'id' field in JSON body [A2 ASSUMED]
    """
    if connector == "github":
        return headers.get("x-github-delivery") or headers.get("X-GitHub-Delivery") or ""
    if connector == "jira":
        return (
            headers.get("x-atlassian-webhook-identifier")
            or headers.get("X-Atlassian-Webhook-Identifier")
            or ""
        )
    if connector == "slack":
        # A1 ASSUMED: Slack Events API includes event_id in JSON body
        return payload.get("event_id") or payload.get("event", {}).get("event_ts", "") or ""
    if connector == "azure_repos":
        # A2 ASSUMED: ADO service hook top-level 'id' is the delivery GUID
        return payload.get("id", "")
    return ""


def _get_verifier_args(connector: str, raw_body: bytes, headers) -> tuple:
    """Build verifier call args from the per-provider credential state in app.state.

    Returns a tuple of positional args for the verifier callable.
    The secrets are read from app.state at call time (set by lifespan).
    This function builds the credential arguments from headers only;
    the app.state secret injection happens in the route handler.
    """
    # Return just raw_body and headers; secret injection happens in handler
    return (raw_body, headers)


@webhooks_router.post("/webhooks/{connector}/{tenant_id}")
async def receive_webhook(
    connector: str,
    tenant_id: str,
    request: Request,
) -> dict:
    """Process an inbound webhook delivery.

    Security-first pipeline:
    1. Content-Length guard (DoS mitigation)
    2. Validate connector name (reject unknowns before any crypto)
    3. Read raw body FIRST (stream consumed on first read — Pitfall 1)
    4. Signature verification (reject invalid before any processing)
    5. Parse JSON
    6. Extract event_id
    7. Dedup check (atomic SET NX EX — after sig verify, never before — Pitfall 5)
    8. Normalize to CanonicalWorkItem
    9. XADD to Redis Stream
    10. Increment metrics
    """
    start_ms = time.monotonic() * 1000

    # Step 1: Content-Length cap before reading body (T-m6-06-D)
    content_length_hdr = request.headers.get("content-length")
    if content_length_hdr is not None:
        try:
            cl = int(content_length_hdr)
        except (ValueError, TypeError):
            cl = 0
        if cl > _MAX_BODY_BYTES:
            raise HTTPException(
                status_code=400,
                detail="Bad Request",  # generic — no size info leaked
            )

    # Step 2: Validate connector name (reject unknown before any crypto)
    if connector not in _KNOWN_CONNECTORS:
        raise HTTPException(status_code=400, detail="Unknown connector")

    # Step 3: Read raw body FIRST — must happen before any JSON parsing
    # No Pydantic body parameter in route signature (Pitfall 1 avoidance)
    raw_body = await request.body()

    # Enforce body size after read (handles Transfer-Encoding: chunked with no Content-Length)
    if len(raw_body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=400, detail="Bad Request")

    # Step 4: Signature verification BEFORE any other processing (security-first)
    # Returns 400 with generic "Bad Request" — never 401/403 (T-m6-06-ID)
    try:
        verifier = get_verifier(connector)
        await _invoke_verifier(verifier, connector, raw_body, request)
    except (ValueError, KeyError, Exception):
        WEBHOOK_DELIVERIES.labels(connector=connector, status="rejected_sig").inc()
        raise HTTPException(status_code=400, detail="Bad Request")

    # Step 5: Parse JSON body
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Bad Request")

    # Step 6: Extract event_id for dedup
    event_id = _extract_event_id(connector, payload, request.headers)
    if not event_id:
        # Fallback: use a hash of the body as event_id so we can still dedup
        import hashlib
        event_id = hashlib.sha256(raw_body).hexdigest()[:32]

    # Step 7: Dedup AFTER signature verification (never before — timing oracle)
    redis_client = _get_redis(request)
    if redis_client is not None:
        if await is_duplicate_event(redis_client, connector, event_id):
            WEBHOOK_DUPLICATES_REJECTED.labels(connector=connector).inc()
            return {"status": "duplicate", "event_id": event_id}

    # Step 8: Normalize to CanonicalWorkItem
    try:
        normalizer = get_normalizer(connector)
        canonical = normalizer(payload, tenant_id, event_id)
    except (KeyError, Exception) as exc:
        logger.warning("Normalization failed for %s: %s", connector, type(exc).__name__)
        raise HTTPException(status_code=400, detail="Bad Request")

    # Step 9: XADD to Redis Stream
    if redis_client is not None:
        await publish_webhook_event(redis_client, connector, canonical)

    # Step 10: Metrics
    WEBHOOK_DELIVERIES.labels(connector=connector, status="accepted").inc()
    elapsed_ms = time.monotonic() * 1000 - start_ms
    WEBHOOK_PROCESSING_DURATION.labels(connector=connector).observe(elapsed_ms)

    return {"status": "accepted", "event_id": event_id}


def _get_redis(request: Request):
    """Return an async Redis client bound to the shared pool, or None if unconfigured.

    app.state.redis_pool is a ConnectionPool (created for the worker pool); the
    dedup/stream helpers need a Redis client, so wrap the pool here. Tests inject a
    ready Redis client via app.state.redis_pool and rely on duck-typing — a client
    has no effect when re-wrapped, but to preserve that path we only wrap a pool.
    """
    pool = getattr(request.app.state, "redis_pool", None)
    if pool is None:
        return None
    import redis.asyncio as aioredis

    if isinstance(pool, aioredis.ConnectionPool):
        return aioredis.Redis(connection_pool=pool)
    return pool  # already a client (e.g. injected in unit tests)


async def _invoke_verifier(verifier, connector: str, raw_body: bytes, request: Request) -> None:
    """Call the per-provider verifier with the correct arguments from request context.

    Each provider uses different header names and secret sources:
    - github:      X-Hub-Signature-256 header + webhook secret from app.state
    - slack:       X-Slack-Request-Timestamp + X-Slack-Signature + signing secret
    - jira:        X-Hub-Signature header + webhook secret from app.state
    - azure_repos: Authorization: Basic header + stored user/pass from app.state
    """
    headers = request.headers
    state = request.app.state if hasattr(request.app, "state") else None

    if connector == "github":
        sig = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        secret = _get_state_attr(state, "github_webhook_secret", "")
        await verifier(raw_body, sig, secret)

    elif connector == "slack":
        ts = headers.get("x-slack-request-timestamp") or headers.get("X-Slack-Request-Timestamp")
        sig = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        secret = _get_state_attr(state, "slack_signing_secret", "")
        await verifier(raw_body, ts, sig, secret)

    elif connector == "jira":
        sig = headers.get("x-hub-signature") or headers.get("X-Hub-Signature")
        secret = _get_state_attr(state, "jira_webhook_secret", "")
        await verifier(raw_body, sig, secret)

    elif connector == "azure_repos":
        auth = headers.get("authorization") or headers.get("Authorization")
        user = _get_state_attr(state, "ado_webhook_user", "")
        pwd = _get_state_attr(state, "ado_webhook_password", "")
        await verifier(raw_body, auth, user, pwd)


def _get_state_attr(state, attr: str, default):
    """Safely read an attribute from app.state; return default if absent."""
    if state is None:
        return default
    return getattr(state, attr, default)
