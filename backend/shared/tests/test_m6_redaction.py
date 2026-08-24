"""RED tests for webhook payload redaction and notify_slack content guards — REQ-M6-15.

Asserts that:
- Webhook failure logging redacts sensitive fields (no raw credential values in log records)
- notify_slack raises ValueError for over-length messages
- notify_slack raises ValueError when message contains known secret patterns (ANTHROPIC_API_KEY)

Tests skip at collection time when SlackConnector or redaction utilities don't exist yet.
"""
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from config.connectors.slack import SlackConnector
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"config.connectors.slack not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

_MAX_MESSAGE_LENGTH = 4000  # Slack message limit; connector must enforce this


@pytest.mark.unit
async def test_notify_slack_raises_on_over_length_message():
    """notify_slack raises ValueError when message exceeds maximum length."""
    connector = SlackConnector(bot_token="xoxb-test-token", tenant_id="test-tenant")
    long_message = "A" * (_MAX_MESSAGE_LENGTH + 1)
    with pytest.raises(ValueError):
        await connector.notify_slack(channel="#general", message=long_message)


@pytest.mark.unit
async def test_notify_slack_raises_on_anthropic_api_key_in_message():
    """notify_slack raises ValueError when message contains 'ANTHROPIC_API_KEY' pattern."""
    connector = SlackConnector(bot_token="xoxb-test-token", tenant_id="test-tenant")
    secret_message = "Config dump: ANTHROPIC_API_KEY=sk-ant-api03-secretvalue12345"
    with pytest.raises(ValueError):
        await connector.notify_slack(channel="#general", message=secret_message)


@pytest.mark.unit
async def test_notify_slack_raises_on_secret_key_patterns():
    """notify_slack raises ValueError for common secret-like patterns in message."""
    connector = SlackConnector(bot_token="xoxb-test-token", tenant_id="test-tenant")
    # Test multiple secret patterns
    secret_patterns = [
        "API_KEY=some_secret_value",
        "Bearer sk-ant-api03-deadbeefdeadbeef",
        "password=super_secret_password",
    ]
    for pattern in secret_patterns:
        with pytest.raises(ValueError):
            await connector.notify_slack(channel="#alerts", message=pattern)


@pytest.mark.unit
async def test_notify_slack_accepts_normal_message():
    """notify_slack accepts a normal, clean message without raising."""
    connector = SlackConnector(bot_token="xoxb-test-token", tenant_id="test-tenant")
    mock_response = MagicMock()

    with patch(
        "slack_sdk.web.async_client.AsyncWebClient.chat_postMessage",
        AsyncMock(return_value=mock_response),
    ):
        # Should not raise for a clean message
        await connector.notify_slack(
            channel="#general",
            message="Build complete: PR #42 merged to main",
        )


@pytest.mark.unit
def test_webhook_payload_redaction_removes_sensitive_fields():
    """Webhook failure log redacts sensitive headers (Authorization, X-Hub-Signature-256)."""
    try:
        from webhooks.redaction import redact_webhook_payload
    except (ImportError, ModuleNotFoundError):
        pytest.skip("webhooks.redaction not yet implemented")

    sensitive_payload = {
        "headers": {
            "Authorization": "Basic dXNlcjpzZWNyZXRwYXNzd29yZA==",
            "X-Hub-Signature-256": "sha256=deadbeefdeadbeef",
            "Content-Type": "application/json",
            "X-GitHub-Delivery": "abc123",
        },
        "body": {"action": "opened", "issue": {"id": 1}},
    }

    redacted = redact_webhook_payload(sensitive_payload)
    headers = redacted.get("headers", {})
    assert headers.get("Authorization") != sensitive_payload["headers"]["Authorization"], (
        "Authorization header must be redacted"
    )
    assert headers.get("X-Hub-Signature-256") != sensitive_payload["headers"]["X-Hub-Signature-256"], (
        "X-Hub-Signature-256 header must be redacted"
    )
    assert headers.get("Content-Type") == "application/json", (
        "Non-sensitive headers must be preserved"
    )


@pytest.mark.unit
async def test_notify_slack_channel_must_be_explicit():
    """notify_slack requires an explicit channel — no default fallback channel."""
    connector = SlackConnector(bot_token="xoxb-test-token", tenant_id="test-tenant")
    # Empty channel must raise
    with pytest.raises(ValueError):
        await connector.notify_slack(channel="", message="Test message")


@pytest.mark.unit
async def test_normal_message_below_max_length_does_not_raise():
    """Messages at or below max length do not trigger ValueError for length."""
    connector = SlackConnector(bot_token="xoxb-test-token", tenant_id="test-tenant")
    safe_message = "Build succeeded: " + "X" * 100  # Well under limit

    mock_response = MagicMock()
    with patch(
        "slack_sdk.web.async_client.AsyncWebClient.chat_postMessage",
        AsyncMock(return_value=mock_response),
    ):
        # Should not raise for a reasonably sized message
        await connector.notify_slack(channel="#builds", message=safe_message)
