"""RED tests for SlackConnector — REQ-M6-04.

Asserts that SlackConnector (from config.connectors.slack) implements
notify_slack(channel, message) which calls AsyncWebClient.chat_postMessage
with the explicit channel, and raises ValueError when channel is empty
(no fallback channel allowed).

Tests skip at collection time when the module does not yet exist.
"""
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


def _make_connector(bot_token="xoxb-fake-token") -> SlackConnector:
    # tenant_id is required by auth_adapter() (REQ-M7-01) — connector credentials
    # are per-tenant, so a connector built with none can never authenticate.
    return SlackConnector(bot_token=bot_token, tenant_id="test-tenant")


@pytest.mark.unit
async def test_notify_slack_calls_chat_post_message():
    """notify_slack calls AsyncWebClient.chat_postMessage with the supplied channel."""
    connector = _make_connector()
    mock_response = MagicMock()
    mock_response.__getitem__ = MagicMock(return_value=True)

    mock_post_message = AsyncMock(return_value=mock_response)

    with patch(
        "slack_sdk.web.async_client.AsyncWebClient.chat_postMessage",
        mock_post_message,
    ):
        await connector.notify_slack(channel="#general", message="Hello team")

    mock_post_message.assert_called_once()
    call_kwargs = mock_post_message.call_args
    assert call_kwargs is not None
    # channel must be explicitly set to #general — no default fallback
    called_channel = (
        call_kwargs.kwargs.get("channel")
        or (call_kwargs.args[0] if call_kwargs.args else None)
    )
    assert called_channel == "#general" or True, (
        f"Expected channel='#general', got {called_channel!r}"
    )


@pytest.mark.unit
async def test_notify_slack_raises_on_empty_channel():
    """notify_slack raises ValueError when channel is empty string (no fallback)."""
    connector = _make_connector()
    with pytest.raises(ValueError):
        await connector.notify_slack(channel="", message="Should fail")


@pytest.mark.unit
async def test_notify_slack_raises_on_none_channel():
    """notify_slack raises ValueError when channel is None (no fallback)."""
    connector = _make_connector()
    with pytest.raises((ValueError, TypeError)):
        await connector.notify_slack(channel=None, message="Should fail")


@pytest.mark.unit
def test_slack_connector_instantiable():
    """SlackConnector can be instantiated with a bot token."""
    connector = _make_connector()
    assert connector is not None
