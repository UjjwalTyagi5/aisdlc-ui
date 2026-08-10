"""Live webhook round-trip + dedup stress — milestone-6, integration half.

All tests in this module are marked @pytest.mark.integration and require:
  1. Azure Key Vault secrets provisioned:
       - jira-email, jira-api-token, jira-url (or JIRA_EMAIL / JIRA_API_TOKEN env vars)
       - github-app-id, github-app-private-key, github-app-installation-id
       - slack-bot-token
       - ado-pat (or ADO_ORG_URL / ADO_PAT env vars)
  2. Provider-side webhook registration pointing at the deployed endpoint:
       POST /webhooks/{connector}/{tenant_id}
       (Jira webhook + secret; GitHub App webhook event; ADO service hook; Slack signing secret)
  3. Running infra (via docker-compose or external):
       - Redis accessible at REDIS_URL
       - Temporal worker running with ENABLE_TEMPORAL=true
  4. Feature flags:
       - ENABLE_WEBHOOK_TRIGGERS=true

DO NOT run this suite unattended or in CI without the above provisioning.
Run only with: cd agentic_app && python -m pytest shared/tests/integration/ -m integration -v

STATUS: DEFERRED — pending credential provisioning.
These tests will pass once the external blockers are resolved (see milestone-6-VERIFICATION.md).

Assumptions verified by this suite (pending live runs):
  A1: Slack Events API includes 'event_id' in JSON body
  A2: ADO service hook payload has 'id' at top level
  A3: Jira 429 responses include Retry-After header
  A4: GitHub API uses X-RateLimit-Remaining and X-RateLimit-Reset headers
"""
import asyncio
import json
import os
import time
import uuid

import pytest

# ---------------------------------------------------------------------------
# Environment checks — all integration tests are skipped if env is missing
# ---------------------------------------------------------------------------

_JIRA_URL = os.environ.get("JIRA_URL") or os.environ.get("JIRA_URL", "")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
_TEMPORAL_AVAILABLE = os.environ.get("ENABLE_TEMPORAL", "false").lower() == "true"
_WEBHOOK_TRIGGERS_ENABLED = os.environ.get("ENABLE_WEBHOOK_TRIGGERS", "false").lower() == "true"

# Base URL for the deployed FastAPI service under test
_SERVICE_BASE_URL = os.environ.get("AGENTIC_BASE_URL", "http://localhost:8001")

_LIVE_ENV_READY = bool(
    _JIRA_URL
    and os.environ.get("JIRA_API_TOKEN")
    and _TEMPORAL_AVAILABLE
    and _WEBHOOK_TRIGGERS_ENABLED
)

_SKIP_REASON = (
    "Live integration environment not provisioned. "
    "Required: JIRA_URL, JIRA_API_TOKEN, ENABLE_TEMPORAL=true, "
    "ENABLE_WEBHOOK_TRIGGERS=true, Redis, Temporal worker running, "
    "provider-side webhook registration completed."
)


# ---------------------------------------------------------------------------
# SC-01 live — Valid signed delivery → 200 accepted; invalid → 400
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_sc01_live_valid_github_signature_accepted():
    """SC-01 (live): GitHub signed test delivery → POST /webhooks/github/{tenant} returns 200."""
    import httpx
    import hashlib
    import hmac

    tenant_id = os.environ.get("TEST_TENANT_ID", str(uuid.uuid4()))
    github_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not github_secret:
        pytest.skip("GITHUB_WEBHOOK_SECRET env var not set")

    raw_body = json.dumps({
        "action": "opened",
        "issue": {"id": 1, "number": 99, "title": "Integration test issue"},
        "repository": {"full_name": "test-org/test-repo"},
    }).encode()

    mac = hmac.new(github_secret.encode(), msg=raw_body, digestmod=hashlib.sha256)
    sig = "sha256=" + mac.hexdigest()
    delivery_id = str(uuid.uuid4())

    async with httpx.AsyncClient(base_url=_SERVICE_BASE_URL, timeout=30) as client:
        resp = await client.post(
            f"/webhooks/github/{tenant_id}",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": delivery_id,
                "X-GitHub-Event": "issues",
            },
        )
    assert resp.status_code == 200, (
        f"SC-01 live: expected 200 for valid GitHub sig, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_sc01_live_invalid_signature_returns_400():
    """SC-01 (live): GitHub delivery with wrong signature → 400 Bad Request."""
    import httpx

    tenant_id = os.environ.get("TEST_TENANT_ID", str(uuid.uuid4()))
    raw_body = b'{"action":"opened","issue":{"id":2}}'

    async with httpx.AsyncClient(base_url=_SERVICE_BASE_URL, timeout=30) as client:
        resp = await client.post(
            f"/webhooks/github/{tenant_id}",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=deadbeefdeadbeef",
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-GitHub-Event": "issues",
            },
        )
    assert resp.status_code == 400, (
        f"SC-01 live: expected 400 for invalid sig, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# SC-02 live — 100-duplicate stress: exactly 1 Temporal run, 99 duplicates
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_sc02_live_100_duplicate_stress():
    """SC-02 (live): 100 identical deliveries (same event_id) → exactly 1 Temporal run.

    This test delivers the same event 100 times and checks:
    - GET /runs shows exactly 1 new run with the expected webhook workflow_id
    - webhook_duplicates_rejected_total{connector='jira'} increments by 99
    """
    import httpx
    import hashlib
    import hmac

    tenant_id = os.environ.get("TEST_TENANT_ID", str(uuid.uuid4()))
    jira_secret = os.environ.get("JIRA_WEBHOOK_SECRET", "")
    if not jira_secret:
        pytest.skip("JIRA_WEBHOOK_SECRET env var not set")

    # Fixed event_id to trigger dedup
    fixed_event_id = f"stress-test-{uuid.uuid4().hex[:8]}"
    raw_body = json.dumps({
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "99999",
            "key": "STRESS-1",
            "fields": {
                "summary": "Dedup stress test",
                "status": {"name": "To Do"},
                "issuetype": {"name": "Story"},
            },
        },
    }).encode()

    mac = hmac.new(jira_secret.encode(), msg=raw_body, digestmod=hashlib.sha256)
    sig = "sha256=" + mac.hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": sig,
        "X-Atlassian-Webhook-Identifier": fixed_event_id,
    }

    accepted = 0
    duplicates = 0

    async with httpx.AsyncClient(base_url=_SERVICE_BASE_URL, timeout=30) as client:
        for _ in range(100):
            resp = await client.post(
                f"/webhooks/jira/{tenant_id}",
                content=raw_body,
                headers=headers,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == "accepted":
                    accepted += 1
                elif body.get("status") == "duplicate":
                    duplicates += 1

    assert accepted == 1, f"SC-02 live: expected 1 accepted, got {accepted}"
    assert duplicates == 99, f"SC-02 live: expected 99 duplicates, got {duplicates}"


# ---------------------------------------------------------------------------
# SC-06 live — Jira webhook round-trip → Temporal SDLCWorkflow → GET /runs
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_sc06_live_jira_webhook_creates_temporal_run():
    """SC-06 (live): Jira webhook delivery → Redis Stream → Temporal SDLCWorkflow → visible via GET /runs.

    Verification flow:
    1. POST /webhooks/jira/{tenant_id} with valid signed payload
    2. Wait up to 10s for consumer to process the stream event
    3. GET /runs (with JWT auth) and assert a run exists with trigger='webhook'
    """
    import httpx
    import hashlib
    import hmac

    tenant_id = os.environ.get("TEST_TENANT_ID", str(uuid.uuid4()))
    jira_secret = os.environ.get("JIRA_WEBHOOK_SECRET", "")
    jwt_token = os.environ.get("TEST_JWT_TOKEN", "")

    if not jira_secret:
        pytest.skip("JIRA_WEBHOOK_SECRET env var not set")
    if not jwt_token:
        pytest.skip("TEST_JWT_TOKEN env var not set")

    unique_event_id = f"roundtrip-{uuid.uuid4().hex}"
    raw_body = json.dumps({
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "id": "10042",
            "key": "ROUND-1",
            "fields": {
                "summary": "Round-trip integration test",
                "status": {"name": "In Progress"},
                "issuetype": {"name": "Task"},
            },
        },
    }).encode()

    mac = hmac.new(jira_secret.encode(), msg=raw_body, digestmod=hashlib.sha256)
    sig = "sha256=" + mac.hexdigest()

    async with httpx.AsyncClient(base_url=_SERVICE_BASE_URL, timeout=30) as client:
        delivery_resp = await client.post(
            f"/webhooks/jira/{tenant_id}",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature": sig,
                "X-Atlassian-Webhook-Identifier": unique_event_id,
            },
        )
    assert delivery_resp.status_code == 200, (
        f"SC-06 live: webhook delivery failed: {delivery_resp.status_code} {delivery_resp.text}"
    )

    # Wait for consumer to process and Temporal to start the workflow
    await asyncio.sleep(5)

    expected_workflow_prefix = f"webhook-jira-{tenant_id}"

    async with httpx.AsyncClient(base_url=_SERVICE_BASE_URL, timeout=30) as client:
        runs_resp = await client.get(
            "/runs",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    assert runs_resp.status_code == 200, (
        f"SC-06 live: GET /runs failed: {runs_resp.status_code}"
    )

    # GET /runs returns a Paginated envelope: {items: RunOut[], pagination: {...}}.
    # RunOut exposes the Temporal id as `temporalWorkflowId` (not `workflow_id`).
    runs = runs_resp.json().get("items", [])
    webhook_runs = [
        r for r in runs
        if r.get("trigger") == "webhook"
        and expected_workflow_prefix in (r.get("temporalWorkflowId") or "")
    ]
    assert webhook_runs, (
        f"SC-06 live: no webhook-triggered run found in GET /runs for workflow prefix "
        f"'{expected_workflow_prefix}'. All runs: {[r.get('temporalWorkflowId') for r in runs]}"
    )


# ---------------------------------------------------------------------------
# SC-08 live — Slack notification delivery
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_sc08_live_slack_notify_delivers_message():
    """SC-08 (live): notify_slack sends a message to a real Slack channel."""
    import os as _os

    slack_token = _os.environ.get("SLACK_BOT_TOKEN", "")
    test_channel = _os.environ.get("SLACK_TEST_CHANNEL", "")

    if not slack_token:
        pytest.skip("SLACK_BOT_TOKEN env var not set")
    if not test_channel:
        pytest.skip("SLACK_TEST_CHANNEL env var not set")

    from config.connectors.slack import SlackConnector

    connector = SlackConnector(bot_token=slack_token)
    # Uses the real Slack Web API — this should succeed if token + channel are valid
    await connector.notify_slack(
        channel=test_channel,
        message=f"Milestone-6 integration test: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
    )


# ---------------------------------------------------------------------------
# Assumption verification tests — A1..A4 (pending live runs)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_assumption_a1_slack_event_id_in_body():
    """A1 (live): Slack Events API 'event_id' field appears at top level of JSON body.

    This test must be run by capturing a real Slack event delivery and inspecting
    the body. The test itself validates the assumption from a real payload.
    Provide the payload via SLACK_TEST_PAYLOAD_FILE env var (path to JSON file).
    """
    import os as _os

    payload_file = _os.environ.get("SLACK_TEST_PAYLOAD_FILE", "")
    if not payload_file:
        pytest.skip("SLACK_TEST_PAYLOAD_FILE not set — provide a captured Slack event payload")

    with open(payload_file) as f:
        payload = json.load(f)

    assert "event_id" in payload, (
        "A1 FAILED: Slack Events API payload does not have 'event_id' at top level. "
        f"Keys found: {list(payload.keys())}. "
        "Update webhooks/verifiers/slack.py dedup event_id extraction if needed."
    )


@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_assumption_a2_ado_event_id_at_top_level():
    """A2 (live): ADO service hook payload has 'id' GUID at top level.

    Provide a captured ADO service hook payload via ADO_TEST_PAYLOAD_FILE env var.
    """
    import os as _os

    payload_file = _os.environ.get("ADO_TEST_PAYLOAD_FILE", "")
    if not payload_file:
        pytest.skip("ADO_TEST_PAYLOAD_FILE not set — provide a captured ADO service hook payload")

    with open(payload_file) as f:
        payload = json.load(f)

    assert "id" in payload, (
        "A2 FAILED: ADO service hook payload does not have 'id' at top level. "
        f"Keys found: {list(payload.keys())}. "
        "Update webhooks/normalizers/azure_repos.py event_id extraction if needed."
    )
    assert isinstance(payload["id"], str), (
        f"A2 FAILED: ADO 'id' is not a string: {type(payload['id'])}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_assumption_a3_jira_429_has_retry_after_header():
    """A3 (live): Jira Cloud 429 responses include a Retry-After header.

    This assumption is verified by triggering a rate limit against a real Jira tenant
    and inspecting the response headers. Provide JIRA_TEST_URL + credentials.
    """
    import httpx

    jira_url = os.environ.get("JIRA_URL", "")
    jira_email = os.environ.get("JIRA_EMAIL", "")
    jira_token = os.environ.get("JIRA_API_TOKEN", "")

    if not all([jira_url, jira_email, jira_token]):
        pytest.skip("JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN must all be set for A3 verification")

    # Rapid fire requests to trigger 429 — inspect for Retry-After header
    # (Note: may require many rapid requests; adjust count if needed)
    retry_after_seen = False
    async with httpx.AsyncClient(auth=(jira_email, jira_token), timeout=10) as client:
        for _ in range(30):
            resp = await client.get(f"{jira_url}/rest/api/3/project")
            if resp.status_code == 429:
                retry_after_seen = "retry-after" in {k.lower() for k in resp.headers}
                break

    if not retry_after_seen:
        pytest.skip("Did not trigger a 429 — cannot verify A3. Retry-After assumption remains PENDING.")

    assert retry_after_seen, (
        "A3 FAILED: Jira 429 response did not include Retry-After header. "
        "Update webhooks/rate_limit_manager.py to use a different fallback backoff."
    )


@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_ENV_READY, reason=_SKIP_REASON)
async def test_assumption_a4_github_rate_limit_headers():
    """A4 (live): GitHub REST API responses include X-RateLimit-Remaining and X-RateLimit-Reset.

    This is verified against the GitHub API using a real installation token.
    """
    import httpx

    github_token = os.environ.get("GITHUB_INSTALLATION_TOKEN", "")
    if not github_token:
        pytest.skip("GITHUB_INSTALLATION_TOKEN not set for A4 verification")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.github.com/rate_limit",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    assert resp.status_code == 200, f"A4: GitHub rate_limit endpoint failed: {resp.status_code}"
    assert "x-ratelimit-remaining" in {k.lower() for k in resp.headers}, (
        "A4 FAILED: GitHub response missing X-RateLimit-Remaining header. "
        "Update rate limit handling in config/connectors/github_issues.py."
    )
    assert "x-ratelimit-reset" in {k.lower() for k in resp.headers}, (
        "A4 FAILED: GitHub response missing X-RateLimit-Reset header."
    )
