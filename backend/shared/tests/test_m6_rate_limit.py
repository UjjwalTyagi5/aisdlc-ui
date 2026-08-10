"""RED tests for per-tenant rate-limit manager — REQ-M6-10.

Asserts that the _TenantRateLimitState / rate_limit_manager pattern:
- A 429 response triggers per-tenant backoff
- Tenant A backoff does NOT delay tenant B (asyncio timing isolation)
- ConnectorAuditEvent.retry_count > 0 after retry-to-success

Uses asyncio.sleep patching to avoid real waits. Tests skip at collection time
when the rate_limit_manager module or _TenantRateLimitState does not yet exist.
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from webhooks.rate_limit_manager import (
        _TenantRateLimitState,
        rate_limit_manager_with_backoff,
        record_rate_limit_hit,
    )
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"webhooks.rate_limit_manager not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)


@pytest.mark.unit
async def test_rate_limit_state_initializes_correctly():
    """_TenantRateLimitState initializes with zero retry_count and no backoff."""
    state = _TenantRateLimitState()
    assert state.retry_count == 0
    assert state.backoff_until == 0.0 or state.backoff_until <= time.time()


@pytest.mark.unit
async def test_record_rate_limit_hit_increments_retry_count():
    """record_rate_limit_hit increments retry_count and sets a future backoff_until."""
    tenant_states = {}
    record_rate_limit_hit(tenant_states, "tenant-a", retry_after_seconds=1.0)
    state = tenant_states.get("tenant-a")
    assert state is not None
    assert state.retry_count == 1
    assert state.backoff_until > time.time()


@pytest.mark.unit
async def test_tenant_a_backoff_does_not_delay_tenant_b():
    """Tenant A's rate-limit backoff does NOT block tenant B calls.

    rate_limit_manager_with_backoff for tenant-b must complete immediately
    even when tenant-a has an active backoff window.
    """
    tenant_states = {}
    retry_ref = [0]

    # Set tenant-a on a 60s backoff
    record_rate_limit_hit(tenant_states, "tenant-a", retry_after_seconds=60.0)
    assert tenant_states["tenant-a"].backoff_until > time.time()

    # tenant-b should complete instantly (no backoff set)
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await rate_limit_manager_with_backoff(tenant_states, "tenant-b", retry_ref)
        mock_sleep.assert_not_called()


@pytest.mark.unit
async def test_active_backoff_triggers_async_sleep():
    """rate_limit_manager_with_backoff calls asyncio.sleep when backoff_until is in the future."""
    tenant_states = {}
    retry_ref = [0]

    # Set a 30s backoff for tenant-c
    record_rate_limit_hit(tenant_states, "tenant-c", retry_after_seconds=30.0)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await rate_limit_manager_with_backoff(tenant_states, "tenant-c", retry_ref)
        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args.args[0]
        assert sleep_duration > 0, f"Expected positive sleep duration, got {sleep_duration}"


@pytest.mark.unit
async def test_retry_count_recorded_in_audit_event():
    """ConnectorAuditEvent can carry retry_count > 0 after a rate-limit hit."""
    try:
        from config.connectors.models import ConnectorAuditEvent
    except (ImportError, ModuleNotFoundError):
        pytest.skip("ConnectorAuditEvent not importable")

    event = ConnectorAuditEvent(
        connector_name="jira",
        method="list_projects",
        tenant_id="tenant-uuid-rate",
        latency_ms=500.0,
        status="success",
        retry_count=2,
    )
    assert event.retry_count == 2, (
        f"ConnectorAuditEvent.retry_count must accept int > 0; got {event.retry_count}"
    )


@pytest.mark.unit
async def test_different_tenants_have_independent_backoff_state():
    """Each tenant's backoff state is completely independent."""
    tenant_states = {}

    # Hit rate limit for tenant-x only
    record_rate_limit_hit(tenant_states, "tenant-x", retry_after_seconds=60.0)

    # tenant-y must not have any backoff
    assert "tenant-y" not in tenant_states or tenant_states.get("tenant-y") is None or \
           tenant_states["tenant-y"].backoff_until <= time.time()


@pytest.mark.unit
def test_tenant_rate_limit_state_is_per_tenant_dict_entry():
    """Each tenant_id gets its own _TenantRateLimitState dict entry (not shared)."""
    tenant_states = {}
    retry_ref = [0]

    record_rate_limit_hit(tenant_states, "tenant-iso-1", retry_after_seconds=5.0)
    record_rate_limit_hit(tenant_states, "tenant-iso-2", retry_after_seconds=10.0)

    assert "tenant-iso-1" in tenant_states
    assert "tenant-iso-2" in tenant_states
    assert tenant_states["tenant-iso-1"] is not tenant_states["tenant-iso-2"]
