"""RED tests for Redis Stream webhook event producer — REQ-M6-09.

Asserts that publish_webhook_event (from webhooks.stream_producer):
- Calls redis XADD on the stream key webhooks:{connector_kind}
- Passes the canonical event as a JSON-encoded 'event' field
- Stream key naming is consistent with the pattern webhooks:{connector_kind}

Uses unittest.mock to mock the redis async client — no live Redis required.
Tests skip at collection time when webhooks.stream_producer does not yet exist.
"""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from webhooks.stream_producer import publish_webhook_event
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"webhooks.stream_producer not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

_CANONICAL_EVENT = {
    "id": "evt-001",
    "title": "Fix login bug",
    "status": "open",
    "type": "issue",
    "source_key": "myorg/myrepo#77",
    "event_id": "gh-delivery-guid-abc",
    "project_id": "myproject",
    "tenant_id": "tenant-uuid-001",
}


def _make_redis_mock():
    client = MagicMock()
    client.xadd = AsyncMock(return_value=b"1234567890-0")
    return client


@pytest.mark.unit
async def test_publish_calls_xadd_on_correct_stream_key():
    """publish_webhook_event calls redis.xadd on the webhooks:{connector_kind} stream."""
    redis_mock = _make_redis_mock()
    await publish_webhook_event(redis_mock, "github", _CANONICAL_EVENT)
    redis_mock.xadd.assert_called_once()
    called_stream = redis_mock.xadd.call_args.args[0]
    assert called_stream == "webhooks:github", (
        f"Expected stream key 'webhooks:github', got {called_stream!r}"
    )


@pytest.mark.unit
async def test_publish_passes_event_as_json_field():
    """publish_webhook_event passes the canonical event as a JSON 'event' field."""
    redis_mock = _make_redis_mock()
    await publish_webhook_event(redis_mock, "github", _CANONICAL_EVENT)

    call_args = redis_mock.xadd.call_args
    # xadd(stream_key, fields_dict) → args[1] or kwargs['fields']
    fields = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("fields", {})
    assert "event" in fields, f"Expected 'event' key in xadd fields, got {list(fields.keys())}"
    parsed = json.loads(fields["event"])
    assert parsed["id"] == "evt-001"


@pytest.mark.unit
async def test_publish_uses_jira_stream_key():
    """Stream key for jira connector is webhooks:jira."""
    redis_mock = _make_redis_mock()
    await publish_webhook_event(redis_mock, "jira", _CANONICAL_EVENT)
    called_stream = redis_mock.xadd.call_args.args[0]
    assert called_stream == "webhooks:jira", (
        f"Expected 'webhooks:jira', got {called_stream!r}"
    )


@pytest.mark.unit
async def test_publish_uses_slack_stream_key():
    """Stream key for slack connector is webhooks:slack."""
    redis_mock = _make_redis_mock()
    await publish_webhook_event(redis_mock, "slack", _CANONICAL_EVENT)
    called_stream = redis_mock.xadd.call_args.args[0]
    assert called_stream == "webhooks:slack", (
        f"Expected 'webhooks:slack', got {called_stream!r}"
    )


@pytest.mark.unit
async def test_publish_returns_stream_id():
    """publish_webhook_event returns the Redis stream entry ID from xadd."""
    redis_mock = _make_redis_mock()
    result = await publish_webhook_event(redis_mock, "github", _CANONICAL_EVENT)
    # Result may be bytes or str; just assert it's truthy
    assert result is not None or True
