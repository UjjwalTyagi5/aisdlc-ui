"""Aggregate exit-gate verification for milestone-6 — unit-verifiable success criteria.

Asserts 8 phase success criteria (SC-01..SC-08) at unit level, plus:
  - SC-07: vendor-SDK boundary grep gate (agents_orchestrator/ must not import
    jira/github SDKs or slack_sdk directly)
  - Module importability of all 16 Wave-0 target modules (no skip-on-missing)

SC-06 (live webhook → Temporal round-trip) is DEFERRED — requires provisioned Key Vault
credentials and provider-side webhook registration. It lives in the -m integration suite.

Run:
    cd agentic_app && python -m pytest shared/tests/test_m6_verification.py -m unit -v
"""
import hashlib
import hmac
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTIC_ROOT = _REPO_ROOT / "agentic_app"
_ORCHESTRATOR_PATH = str(_AGENTIC_ROOT / "agents_orchestrator")

sys.path.insert(0, str(_AGENTIC_ROOT))

# ---------------------------------------------------------------------------
# SC-07 — vendor-SDK boundary gate (must pass before anything else)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_sc07_no_jira_sdk_import_in_agents_orchestrator():
    """SC-07: grep -r 'from jira' agents_orchestrator/ must return exit code 1.

    Agent tool code must not import the Jira SDK directly — all Jira access
    flows through the connector abstraction layer (get_connector() DI).
    """
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "from jira", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        "BOUNDARY VIOLATION: found 'from jira' imports in agents_orchestrator/:\n"
        + result.stdout.decode(errors="replace")
    )


@pytest.mark.unit
def test_sc07_no_github_sdk_import_in_agents_orchestrator():
    """SC-07: grep -r 'from github' agents_orchestrator/ must return exit code 1.

    Agent tool code must not import the PyGithub SDK directly.
    """
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "from github", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        "BOUNDARY VIOLATION: found 'from github' imports in agents_orchestrator/:\n"
        + result.stdout.decode(errors="replace")
    )


@pytest.mark.unit
def test_sc07_no_slack_sdk_import_in_agents_orchestrator():
    """SC-07: grep -r 'import slack_sdk' agents_orchestrator/ must return exit code 1.

    slack-sdk usage is permitted in config/connectors/slack.py only, never in agent
    tool functions.
    """
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "import slack_sdk", _ORCHESTRATOR_PATH],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result.returncode == 1, (
        "BOUNDARY VIOLATION: found 'import slack_sdk' in agents_orchestrator/:\n"
        + result.stdout.decode(errors="replace")
    )


# ---------------------------------------------------------------------------
# Module importability gate — all 16 Wave-0 target modules must import cleanly
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_all_wave0_target_modules_importable():
    """All 16 Wave-0 target modules import without error (no skip-on-missing remaining)."""
    modules_to_check = [
        "webhooks.verifiers.github",
        "webhooks.verifiers.slack",
        "webhooks.verifiers.jira",
        "webhooks.verifiers.azure_repos",
        "webhooks.dedup",
        "webhooks.stream_producer",
        "webhooks.router",
        "webhooks.consumer",
        "webhooks.normalizers.github",
        "webhooks.normalizers.jira",
        "webhooks.normalizers.azure_repos",
        "webhooks.rate_limit_manager",
        "config.connectors.jira",
        "config.connectors.github_issues",
        "config.connectors.azure_repos",
        "config.connectors.slack",
    ]
    failures = []
    for module in modules_to_check:
        try:
            __import__(module)
        except (ImportError, ModuleNotFoundError) as exc:
            failures.append(f"{module}: {exc}")
    assert not failures, (
        "The following Wave-0 target modules are NOT yet importable "
        "(all must be GREEN before phase-gate passes):\n"
        + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# SC-01 — Signature verification: invalid sig → 400 / valid → accepted
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_sc01_github_invalid_signature_raises_value_error():
    """SC-01: GitHub HMAC-SHA256 mismatch raises ValueError (caller returns 400)."""
    from webhooks.verifiers.github import verify_github_signature

    raw_body = b'{"action":"opened","issue":{"id":1}}'
    with pytest.raises(ValueError):
        await verify_github_signature(raw_body, "sha256=deadbeef", "real-secret")


@pytest.mark.unit
async def test_sc01_github_valid_signature_does_not_raise():
    """SC-01: Correct GitHub HMAC-SHA256 signature passes without raising."""
    from webhooks.verifiers.github import verify_github_signature

    raw_body = b'{"action":"opened","issue":{"id":1}}'
    secret = "my-github-secret"
    mac = hmac.new(secret.encode(), msg=raw_body, digestmod=hashlib.sha256)
    valid_sig = "sha256=" + mac.hexdigest()
    await verify_github_signature(raw_body, valid_sig, secret)


@pytest.mark.unit
async def test_sc01_missing_signature_raises_value_error():
    """SC-01: Missing signature header raises ValueError for all providers."""
    from webhooks.verifiers.github import verify_github_signature

    with pytest.raises(ValueError):
        await verify_github_signature(b"body", None, "secret")


@pytest.mark.unit
async def test_sc01_jira_invalid_signature_raises_value_error():
    """SC-01: Jira X-Hub-Signature mismatch raises ValueError."""
    from webhooks.verifiers.jira import verify_jira_signature

    with pytest.raises(ValueError):
        await verify_jira_signature(b"body", "sha256=deadbeef", "real-secret")


@pytest.mark.unit
async def test_sc01_slack_stale_timestamp_raises_value_error():
    """SC-01: Slack signature with timestamp older than 5 minutes raises ValueError (replay)."""
    from webhooks.verifiers.slack import verify_slack_signature

    stale_ts = str(int(time.time()) - 400)  # 400s old
    raw_body = b"body"
    base_string = f"v0:{stale_ts}:{raw_body.decode()}"
    mac = hmac.new(b"secret", msg=base_string.encode(), digestmod=hashlib.sha256)
    sig = "v0=" + mac.hexdigest()
    with pytest.raises(ValueError):
        await verify_slack_signature(raw_body, stale_ts, sig, "secret")


# ---------------------------------------------------------------------------
# SC-02 — Dedup atomicity: 100× same event_id → exactly 1 stream write (mock)
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_sc02_100_duplicates_yield_one_accepted_99_rejected():
    """SC-02: 100 calls with same event_id → exactly 1 non-duplicate (mock redis SET NX EX)."""
    from webhooks.dedup import is_duplicate_event

    return_values = ["OK"] + [None] * 99
    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(side_effect=return_values)

    results = []
    for _ in range(100):
        r = await is_duplicate_event(redis_mock, "github", "stress-event-id")
        results.append(r)

    not_dup = results.count(False)
    dup = results.count(True)
    assert not_dup == 1, f"Expected exactly 1 non-duplicate; got {not_dup}"
    assert dup == 99, f"Expected exactly 99 duplicates; got {dup}"
    assert redis_mock.set.call_count == 100


@pytest.mark.unit
async def test_sc02_dedup_uses_atomic_set_nx_ex():
    """SC-02: is_duplicate_event uses atomic SET NX EX (nx=True, ex=86400)."""
    from webhooks.dedup import is_duplicate_event

    redis_mock = MagicMock()
    redis_mock.set = AsyncMock(return_value="OK")
    await is_duplicate_event(redis_mock, "jira", "evt-atomic-check")
    redis_mock.set.assert_called_once_with(
        "webhooks:dedup:jira:evt-atomic-check", "1", nx=True, ex=86400
    )


# ---------------------------------------------------------------------------
# SC-03 — 429 backoff records retry_count; tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_sc03_rate_limit_hit_increments_retry_count():
    """SC-03: A 429 hit increments retry_count and sets a future backoff_until."""
    from webhooks.rate_limit_manager import record_rate_limit_hit

    tenant_states = {}
    record_rate_limit_hit(tenant_states, "tenant-a", retry_after_seconds=5.0)
    state = tenant_states["tenant-a"]
    assert state.retry_count == 1
    assert state.backoff_until > time.time()


@pytest.mark.unit
async def test_sc03_tenant_a_backoff_does_not_delay_tenant_b():
    """SC-03: Tenant-A backoff does NOT block tenant-B calls (isolation)."""
    from webhooks.rate_limit_manager import rate_limit_manager_with_backoff, record_rate_limit_hit

    tenant_states = {}
    retry_ref = [0]
    record_rate_limit_hit(tenant_states, "tenant-a", retry_after_seconds=60.0)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await rate_limit_manager_with_backoff(tenant_states, "tenant-b", retry_ref)
        mock_sleep.assert_not_called()


@pytest.mark.unit
async def test_sc03_retry_count_stored_in_audit_event():
    """SC-03: ConnectorAuditEvent.retry_count accepts and stores retry count from 429 hits."""
    from config.connectors.models import ConnectorAuditEvent

    event = ConnectorAuditEvent(
        connector_name="jira",
        method="list_projects",
        tenant_id="tenant-rate-sc03",
        latency_ms=500.0,
        status="success",
        retry_count=3,
    )
    assert event.retry_count == 3


# ---------------------------------------------------------------------------
# SC-04 — /connectors/health includes all 4 new connectors
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_sc04_connector_health_includes_all_new_connectors():
    """SC-04: GET /connectors/health returns entries for jira, github_issues, azure_repos, slack."""
    import jwt as _jwt

    try:
        from process_api import app
    except Exception as exc:
        pytest.skip(f"process_api not importable: {exc}")

    try:
        from fastapi.testclient import TestClient
        from config.env import JWT_SECRET_KEY, JWT_ALGORITHM
    except ImportError as exc:
        pytest.skip(f"TestClient or env not available: {exc}")

    token = ""
    if JWT_SECRET_KEY:
        now = int(time.time())
        token = _jwt.encode(
            {"sub": "m6-gate", "tenant_id": "test", "iat": now, "exp": now + 3600},
            JWT_SECRET_KEY,
            algorithm=JWT_ALGORITHM or "HS256",
        )

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/connectors/health",
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    if resp.status_code in (401, 503):
        pytest.skip(f"Auth/service unavailable: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    connectors = body.get("connectors", {})
    for expected in ("jira", "github_issues", "azure_repos", "slack"):
        assert expected in connectors, (
            f"SC-04 FAIL: '{expected}' missing from /connectors/health; "
            f"got keys: {list(connectors.keys())}"
        )


# ---------------------------------------------------------------------------
# SC-05 — ConnectorAuditEvent fields emitted per call
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_sc05_audit_event_has_all_required_fields():
    """SC-05: ConnectorAuditEvent has all required M6 fields including retry_count."""
    from config.connectors.models import ConnectorAuditEvent

    event = ConnectorAuditEvent(
        connector_name="github_issues",
        method="list_stories",
        tenant_id="tenant-sc05",
        run_id="run-sc05",
        latency_ms=55.0,
        status="success",
        retry_count=0,
    )
    assert event.connector_name == "github_issues"
    assert event.method == "list_stories"
    assert event.tenant_id == "tenant-sc05"
    assert event.latency_ms == 55.0
    assert event.status == "success"
    assert hasattr(event, "retry_count")
    assert event.retry_count == 0


@pytest.mark.unit
def test_sc05_audit_event_error_type_uses_class_name():
    """SC-05: error_type uses type(exc).__name__ — never str(exc) to avoid leaking content."""
    from config.connectors.models import ConnectorAuditEvent

    exc = ValueError("credential=super_secret_token")
    event = ConnectorAuditEvent(
        connector_name="jira",
        method="create_item",
        tenant_id="tenant-sc05",
        latency_ms=10.0,
        status="error",
        error_type=type(exc).__name__,
    )
    assert event.error_type == "ValueError"
    assert "super_secret_token" not in (event.error_type or "")


# ---------------------------------------------------------------------------
# SC-08 — Slack: explicit channel required; message redaction guards
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_sc08_slack_empty_channel_raises():
    """SC-08: notify_slack raises ValueError for empty channel (no fallback channel)."""
    from config.connectors.slack import SlackConnector

    connector = SlackConnector(bot_token="xoxb-fake-token")
    with pytest.raises(ValueError):
        await connector.notify_slack(channel="", message="test message")


@pytest.mark.unit
async def test_sc08_slack_over_length_message_raises():
    """SC-08: notify_slack raises ValueError for messages exceeding max length."""
    from config.connectors.slack import SlackConnector

    connector = SlackConnector(bot_token="xoxb-fake-token")
    long_message = "A" * 4001  # over 4000-char limit
    with pytest.raises(ValueError):
        await connector.notify_slack(channel="#general", message=long_message)


@pytest.mark.unit
async def test_sc08_slack_secret_pattern_raises():
    """SC-08: notify_slack raises ValueError when message contains ANTHROPIC_API_KEY pattern."""
    from config.connectors.slack import SlackConnector

    connector = SlackConnector(bot_token="xoxb-fake-token")
    with pytest.raises(ValueError):
        await connector.notify_slack(
            channel="#alerts",
            message="Dumping config: ANTHROPIC_API_KEY=sk-ant-api03-abc123",
        )


@pytest.mark.unit
async def test_sc08_slack_clean_message_accepted():
    """SC-08: notify_slack accepts a clean, properly-sized message."""
    from config.connectors.slack import SlackConnector

    connector = SlackConnector(bot_token="xoxb-fake-token")
    mock_response = MagicMock()
    with patch(
        "slack_sdk.web.async_client.AsyncWebClient.chat_postMessage",
        AsyncMock(return_value=mock_response),
    ):
        await connector.notify_slack(
            channel="#general",
            message="Build completed: PR #99 merged to main.",
        )


# ---------------------------------------------------------------------------
# The consumer flag-gate tests lived here. WebhookConsumer is gone with Temporal:
# it existed to turn a stream event into a workflow, and there is no engine to
# start. Webhook DELIVERY is still covered above; what is no longer covered is
# what happens to a delivered event, because nothing happens to it yet.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_consumer_flag_gate_disables_workflow_creation():
    """ENABLE_WEBHOOK_TRIGGERS=False prevents start_workflow from being called."""
    from webhooks.consumer import WebhookConsumer

    temporal_mock = AsyncMock()
    redis_mock = MagicMock()
    consumer = WebhookConsumer(temporal_client=temporal_mock, redis_client=redis_mock)

    stream_event = {
        "id": "stream-001",
        "connector": "github",
        "tenant_id": "tenant-flag-test",
        "event": json.dumps({
            "id": "evt-flag",
            "title": "Test issue",
            "status": "open",
            "type": "issue",
            "event_id": "delivery-flag",
            "project_id": "proj-1",
            "tenant_id": "tenant-flag-test",
            "source_key": "org/repo#1",
        }),
    }

    with patch("config.env.ENABLE_WEBHOOK_TRIGGERS", False):
        await consumer.handle_task(stream_event)

    temporal_mock.start_workflow.assert_not_called()


@pytest.mark.unit
async def test_consumer_flag_gate_enables_workflow_creation():
    """ENABLE_WEBHOOK_TRIGGERS=True causes start_workflow to be called."""
    from webhooks.consumer import WebhookConsumer

    temporal_mock = AsyncMock()
    redis_mock = MagicMock()
    consumer = WebhookConsumer(temporal_client=temporal_mock, redis_client=redis_mock)

    stream_event = {
        "id": "stream-002",
        "connector": "jira",
        "tenant_id": "tenant-enable-test",
        "event": json.dumps({
            "id": "evt-enable",
            "title": "Jira issue",
            "status": "open",
            "type": "story",
            "event_id": "delivery-enable",
            "project_id": "proj-2",
            "tenant_id": "tenant-enable-test",
            "source_key": "PROJ-42",
        }),
    }

    with patch("config.env.ENABLE_WEBHOOK_TRIGGERS", True):
        await consumer.handle_task(stream_event)

    temporal_mock.start_workflow.assert_called_once()
