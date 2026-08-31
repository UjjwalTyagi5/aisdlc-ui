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

import hashlib
import hmac
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
_KNOWN_CONNECTORS = frozenset({"github", "jira", "azure_repos", "slack", "github_actions"})

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
    if connector in ("github", "github_actions"):
        # Actions deliveries carry the same X-GitHub-Delivery GUID as any other
        # GitHub webhook.
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


# ── Webhook secrets are PER-TENANT ───────────────────────────────────────────
#
# They used to be one process-wide value each, loaded into app.state at startup from
# an untenanted `load_secret("github-webhook-secret")` with an env var behind it. The
# comment in config/env.py defended that as unavoidable, on the grounds that a
# signature is checked "before a request is attributed to any tenant".
#
# That was not true of this router. The tenant is a PATH PARAMETER —
# POST /webhooks/{connector}/{tenant_id} — so it is known before the body is even
# read, let alone verified. What the shared secret actually bought was cross-tenant
# forgery: one secret verifies a delivery to EVERY tenant's URL, so anyone holding it
# (any tenant who configured a webhook, plus every operator) could inject work items
# into any other tenant's pipeline, correctly signed and indistinguishable from real.
#
# Each tenant now registers the webhook in its own GitHub org / Jira site / Slack app
# with its own secret, pastes it into Integrations, and it is resolved here through
# the same two tenant-scoped rungs every connector credential uses.
_WEBHOOK_SECRET_REFS: dict[str, tuple[str, ...]] = {
    "github": ("github-webhook-secret",),
    "github_actions": ("gha-webhook-secret",),
    "slack": ("slack-signing-secret",),
    "jira": ("jira-webhook-secret",),
    # ADO service hooks have no HMAC — they authenticate with HTTP Basic, so this
    # pair is a username and a password rather than a signing key.
    "azure_repos": ("ado-webhook-user", "ado-webhook-password"),
    "msgraph": ("msgraph-webhook-client-state",),
}


async def _tenant_webhook_secret(tenant_id: str, ref: str) -> str:
    """Resolve one webhook secret for THIS tenant. Returns "" when unset.

    Two rungs, both tenant-scoped: the tenant secret store, then Key Vault
    "{tenant}-{ref}". There is deliberately no global-vault rung and no env var —
    see the note above. A "" return means this tenant has not configured the
    webhook, and every caller treats that as a rejection, never as a pass.
    """
    if not tenant_id or not ref:
        return ""
    try:
        from shared.services import secret_store  # lazy: avoid an import cycle
        value = await secret_store.get_secret(tenant_id, ref)
        if value:
            return value
    except Exception as exc:  # noqa: BLE001 — fall through to Key Vault
        logger.warning("secret store unavailable for %s: %s", ref, type(exc).__name__)
    try:
        import shared.keyvault as _keyvault
        return await _keyvault.load_secret(ref, tenant_id=tenant_id) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("key vault unavailable for %s: %s", ref, type(exc).__name__)
        return ""


# ── Microsoft Graph change notifications ─────────────────────────────────────
#
# DECLARED BEFORE the generic /webhooks/{connector}/{tenant_id} route ON PURPOSE.
# FastAPI matches in declaration order, so the generic route would otherwise swallow
# this path and reject it as an unknown connector at step 2 — before the validation
# handshake could ever answer, which would make the subscription impossible to create.
#
# Graph does not fit the generic verify → parse → dedup → normalize → publish pipeline
# for four reasons:
#   1. The validation handshake must answer with text/plain, not JSON, and before the
#      body is read at all.
#   2. One delivery carries N notifications; the normalizer contract is one-to-one.
#   3. There is no delivery-id header, so the dedup key must be synthesised per
#      notification rather than per request.
#   4. There is no signature. `clientState` is the ONLY authentication Graph offers.


@webhooks_router.post("/webhooks/msgraph/{tenant_id}")
async def receive_msgraph_notification(tenant_id: str, request: Request):
    """Receive Microsoft Graph change notifications (SharePoint drive items).

    SECURITY: `clientState` is the only authentication available, so it must be high
    entropy and is compared with hmac.compare_digest. A mismatch on ANY notification
    rejects the WHOLE batch with a generic 400 — never 401/403, matching the rest of
    this router (T-m6-06-ID, no auth-semantics leak).
    """
    from fastapi.responses import PlainTextResponse

    # Step 1: validation handshake. MUST come first, MUST NOT read the body, and MUST
    # be text/plain — Graph rejects the subscription otherwise.
    validation_token = request.query_params.get("validationToken")
    if validation_token is not None:
        return PlainTextResponse(validation_token, status_code=200)

    start_ms = time.monotonic() * 1000

    # Step 2: Content-Length cap before reading the body (same guard as the generic route).
    content_length_hdr = request.headers.get("content-length")
    if content_length_hdr is not None:
        try:
            if int(content_length_hdr) > _MAX_BODY_BYTES:
                raise HTTPException(status_code=400, detail="Bad Request")
        except (ValueError, TypeError):
            pass

    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=400, detail="Bad Request")

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Bad Request")

    notifications = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(notifications, list):
        raise HTTPException(status_code=400, detail="Bad Request")

    # Step 3: authenticate EVERY notification before acting on any of them, against
    # THIS TENANT's clientState — Graph offers no signature, so this string is the
    # entire authentication story and a platform-wide one would let any tenant's
    # subscription speak for any other.
    expected = await _tenant_webhook_secret(tenant_id, "msgraph-webhook-client-state")
    if not expected:
        # Fail closed: with no configured clientState there is nothing to authenticate
        # against, so anyone could inject change events.
        logger.warning("msgraph webhook received but no client state is configured")
        WEBHOOK_DELIVERIES.labels(connector="msgraph", status="rejected_sig").inc()
        raise HTTPException(status_code=400, detail="Bad Request")

    for note in notifications:
        supplied = note.get("clientState", "") if isinstance(note, dict) else ""
        if not hmac.compare_digest(str(supplied), str(expected)):
            WEBHOOK_DELIVERIES.labels(connector="msgraph", status="rejected_sig").inc()
            raise HTTPException(status_code=400, detail="Bad Request")

    # Step 4: dedup + normalize + publish, per notification.
    redis_client = _get_redis(request)
    normalizer = get_normalizer("msgraph")
    accepted = duplicates = 0

    for note in notifications:
        if not isinstance(note, dict):
            continue
        resource_data = note.get("resourceData") or {}
        event_id = hashlib.sha256(
            "|".join(
                [
                    str(note.get("subscriptionId", "")),
                    str(note.get("resource", "")),
                    str(resource_data.get("id", "") if isinstance(resource_data, dict) else ""),
                    str(note.get("changeType", "")),
                ]
            ).encode("utf-8")
        ).hexdigest()[:32]

        if redis_client is not None and await is_duplicate_event(
            redis_client, "msgraph", event_id
        ):
            duplicates += 1
            WEBHOOK_DUPLICATES_REJECTED.labels(connector="msgraph").inc()
            continue

        try:
            canonical = normalizer(note, tenant_id, event_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("msgraph normalization failed: %s", type(exc).__name__)
            continue

        # Published to `webhooks:sharepoint`. NOTE: nothing consumes that stream yet —
        # what a changed SharePoint document should DO to a run is a product decision,
        # so the events are observable but inert. Whoever builds that consumer must
        # treat these as untrusted identifiers and re-fetch through the connector.
        if redis_client is not None:
            await publish_webhook_event(redis_client, "sharepoint", canonical)
        accepted += 1

    WEBHOOK_DELIVERIES.labels(connector="msgraph", status="accepted").inc()
    WEBHOOK_PROCESSING_DURATION.labels(connector="msgraph").observe(
        time.monotonic() * 1000 - start_ms
    )
    return {"status": "accepted", "accepted": accepted, "duplicates": duplicates}


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
        await _invoke_verifier(verifier, connector, tenant_id, raw_body, request)
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


async def _invoke_verifier(
    verifier, connector: str, tenant_id: str, raw_body: bytes, request: Request
) -> None:
    """Call the per-provider verifier with THIS TENANT's secret.

    Each provider uses different header names and a different credential shape:
    - github / github_actions: X-Hub-Signature-256 + its own HMAC secret
    - slack:                   X-Slack-Request-Timestamp + X-Slack-Signature + signing secret
    - jira:                    X-Hub-Signature + HMAC secret
    - azure_repos:             Authorization: Basic + a username/password pair

    FAILS CLOSED ON AN UNCONFIGURED SECRET. This is not defensive tidiness — with a
    secret of "" every HMAC verifier here computes hmac.new(b"", body), a value any
    caller can compute for themselves, so an unconfigured tenant would accept ANY
    signed-looking delivery rather than none. The old code passed app.state's ""
    default straight through to the verifier. Raising instead makes "this tenant has
    not set the webhook up" reject, which is what the caller turns into a 400.
    """
    headers = request.headers
    refs = _WEBHOOK_SECRET_REFS.get(connector, ())
    secrets = [await _tenant_webhook_secret(tenant_id, ref) for ref in refs]
    if not secrets or not all(secrets):
        raise ValueError(f"{connector} webhook is not configured for this tenant")

    if connector in ("github", "github_actions"):
        # Identical HMAC-SHA256 algorithm, DIFFERENT secret — a CI webhook can then be
        # rotated without disturbing the issues webhook.
        sig = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        await verifier(raw_body, sig, secrets[0])

    elif connector == "slack":
        ts = headers.get("x-slack-request-timestamp") or headers.get("X-Slack-Request-Timestamp")
        sig = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        await verifier(raw_body, ts, sig, secrets[0])

    elif connector == "jira":
        sig = headers.get("x-hub-signature") or headers.get("X-Hub-Signature")
        await verifier(raw_body, sig, secrets[0])

    elif connector == "azure_repos":
        auth = headers.get("authorization") or headers.get("Authorization")
        await verifier(raw_body, auth, secrets[0], secrets[1])
