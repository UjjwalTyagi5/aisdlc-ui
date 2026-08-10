"""RED tests for webhook deduplication — REQ-M6-07.

Asserts that is_duplicate_event (from webhooks.dedup):
- Returns False on first call for a new event_id
- Returns True on second call for the same event_id
- Under 100 concurrent calls for the same event_id, exactly one Redis SET NX EX
  is accepted (returns truthy) and 99 are rejected (return None → duplicate)
- Redis key format is: webhooks:dedup:{provider}:{event_id}
- TTL is 86400 seconds (24h)

Uses unittest.mock to mock the redis async client — no live Redis required.
Tests skip at collection time when webhooks.dedup does not yet exist.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from webhooks.dedup import is_duplicate_event
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"webhooks.dedup not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)


def _make_redis_mock(set_return_values):
    """Build a minimal async redis mock whose set() returns values from the list sequentially."""
    client = MagicMock()
    client.set = AsyncMock(side_effect=set_return_values)
    return client


@pytest.mark.unit
async def test_first_call_returns_false_not_duplicate():
    """First occurrence of an event_id returns False (not a duplicate)."""
    # SET NX EX returns "OK" (truthy) on first set
    redis_mock = _make_redis_mock(["OK"])
    result = await is_duplicate_event(redis_mock, "github", "evt-001")
    assert result is False, f"Expected False (not duplicate) for first call, got {result}"


@pytest.mark.unit
async def test_second_call_returns_true_duplicate():
    """Second occurrence of the same event_id returns True (duplicate)."""
    # First call: key did not exist → SET succeeds → "OK"
    # Second call: key exists → SET NX returns None (duplicate)
    redis_mock = _make_redis_mock(["OK", None])
    _ = await is_duplicate_event(redis_mock, "github", "evt-001")
    result = await is_duplicate_event(redis_mock, "github", "evt-001")
    assert result is True, f"Expected True (duplicate) for second call, got {result}"


@pytest.mark.unit
async def test_redis_set_called_with_nx_and_ex_86400():
    """is_duplicate_event calls redis SET with nx=True and ex=86400 (24h TTL)."""
    redis_mock = _make_redis_mock(["OK"])
    await is_duplicate_event(redis_mock, "github", "evt-nx-check")
    redis_mock.set.assert_called_once_with(
        "webhooks:dedup:github:evt-nx-check",
        "1",
        nx=True,
        ex=86400,
    )


@pytest.mark.unit
async def test_key_format_includes_provider_and_event_id():
    """Redis key has the format webhooks:dedup:{provider}:{event_id}."""
    redis_mock = _make_redis_mock(["OK"])
    await is_duplicate_event(redis_mock, "jira", "jira-evt-XYZ")
    called_key = redis_mock.set.call_args.args[0]
    assert called_key == "webhooks:dedup:jira:jira-evt-XYZ", (
        f"Key format wrong: {called_key!r}"
    )


@pytest.mark.unit
async def test_100_calls_same_event_id_exactly_one_accepted():
    """Under 100 calls for the same event_id, exactly one SET NX EX returns truthy (the rest return None).

    Simulates the worst-case concurrent scenario: even sequentially the mock
    honours the nx=True contract — first returns "OK", remaining return None.
    """
    return_values = ["OK"] + [None] * 99
    redis_mock = _make_redis_mock(return_values)

    results = []
    for _ in range(100):
        r = await is_duplicate_event(redis_mock, "slack", "slack-evt-stress")
        results.append(r)

    not_duplicate_count = results.count(False)
    duplicate_count = results.count(True)
    assert not_duplicate_count == 1, (
        f"Expected exactly 1 non-duplicate, got {not_duplicate_count}"
    )
    assert duplicate_count == 99, (
        f"Expected exactly 99 duplicates, got {duplicate_count}"
    )
    assert redis_mock.set.call_count == 100


@pytest.mark.unit
async def test_different_providers_use_different_keys():
    """Same event_id with different providers produces different Redis keys."""
    redis_mock = _make_redis_mock(["OK", "OK"])
    await is_duplicate_event(redis_mock, "github", "shared-evt-id")
    await is_duplicate_event(redis_mock, "jira", "shared-evt-id")

    calls = redis_mock.set.call_args_list
    keys = [c.args[0] for c in calls]
    assert "webhooks:dedup:github:shared-evt-id" in keys
    assert "webhooks:dedup:jira:shared-evt-id" in keys
    assert keys[0] != keys[1]
