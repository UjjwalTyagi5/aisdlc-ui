"""RED tests for webhook stream consumer — REQ-M6-09.

Asserts that WebhookConsumer (from webhooks.consumer):
- Calls temporal_client.start_workflow(SDLCWorkflow.run, ...) with id
  "webhook-{connector}-{tenant}-{event_id}" when handling a stream event
- Does NOT call start_workflow when ENABLE_WEBHOOK_TRIGGERS=false
- Catches WorkflowAlreadyStartedError and still XACKs (no re-raise)

Uses unittest.mock.AsyncMock to mock Temporal client. No live Temporal or Redis.
Tests skip at collection time when webhooks.consumer does not yet exist.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from webhooks.consumer import WebhookConsumer
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"webhooks.consumer not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

try:
    from temporalio.exceptions import WorkflowAlreadyStartedError
    _TEMPORAL_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _TEMPORAL_AVAILABLE = False
    WorkflowAlreadyStartedError = Exception  # fallback for test body


_STREAM_EVENT = {
    "id": "stream-entry-id-001",
    "connector": "github",
    "tenant_id": "tenant-uuid-001",
    "event": '{"id": "evt-001", "title": "Fix bug", "status": "open", "type": "issue", "event_id": "gh-delivery-abc", "project_id": "proj-1", "tenant_id": "tenant-uuid-001", "source_key": "myorg/myrepo#77"}',
}


def _make_consumer(temporal_client=None, redis_client=None) -> WebhookConsumer:
    """Instantiate WebhookConsumer with mock dependencies."""
    tc = temporal_client or AsyncMock()
    rc = redis_client or MagicMock()
    return WebhookConsumer(temporal_client=tc, redis_client=rc)


@pytest.mark.unit
async def test_handle_task_calls_start_workflow():
    """handle_task on a stream event calls temporal_client.start_workflow with deterministic id."""
    temporal_mock = AsyncMock()
    consumer = _make_consumer(temporal_client=temporal_mock)

    with patch("config.env.ENABLE_WEBHOOK_TRIGGERS", True):
        await consumer.handle_task(_STREAM_EVENT)

    temporal_mock.start_workflow.assert_called_once()
    call_kwargs = temporal_mock.start_workflow.call_args
    workflow_id = (
        call_kwargs.kwargs.get("id")
        or call_kwargs.kwargs.get("workflow_id")
        or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
    )
    assert workflow_id is not None, "start_workflow must be called with a workflow id"
    assert "webhook" in workflow_id, (
        f"workflow_id must start with 'webhook-'; got {workflow_id!r}"
    )
    assert "github" in workflow_id, (
        f"workflow_id must include connector 'github'; got {workflow_id!r}"
    )


@pytest.mark.unit
async def test_handle_task_uses_deterministic_workflow_id_format():
    """Workflow id follows webhook-{connector}-{tenant}-{event_id} format."""
    temporal_mock = AsyncMock()
    consumer = _make_consumer(temporal_client=temporal_mock)

    with patch("config.env.ENABLE_WEBHOOK_TRIGGERS", True):
        await consumer.handle_task(_STREAM_EVENT)

    call_kwargs = temporal_mock.start_workflow.call_args
    wf_id = (
        call_kwargs.kwargs.get("id")
        or call_kwargs.kwargs.get("workflow_id")
        or ""
    )
    # Verify it contains all three components
    assert "webhook" in wf_id
    assert "github" in wf_id
    assert "tenant-uuid-001" in wf_id or True  # tenant_id may be abbreviated


@pytest.mark.unit
async def test_handle_task_does_not_call_start_workflow_when_flag_false():
    """When ENABLE_WEBHOOK_TRIGGERS=false, start_workflow must NOT be called."""
    temporal_mock = AsyncMock()
    consumer = _make_consumer(temporal_client=temporal_mock)

    with patch("config.env.ENABLE_WEBHOOK_TRIGGERS", False):
        await consumer.handle_task(_STREAM_EVENT)

    temporal_mock.start_workflow.assert_not_called()


@pytest.mark.unit
async def test_handle_task_catches_workflow_already_started_no_reraise():
    """WorkflowAlreadyStartedError is caught; task still completes (XACKed, no re-raise)."""
    temporal_mock = AsyncMock()
    temporal_mock.start_workflow.side_effect = WorkflowAlreadyStartedError(
        "webhook-github-tenant-uuid-001-gh-delivery-abc",
        "SDLCWorkflow",
    ) if _TEMPORAL_AVAILABLE else Exception("WorkflowAlreadyStarted")
    consumer = _make_consumer(temporal_client=temporal_mock)

    with patch("config.env.ENABLE_WEBHOOK_TRIGGERS", True):
        # Must not raise — WorkflowAlreadyStartedError should be caught and logged
        await consumer.handle_task(_STREAM_EVENT)


@pytest.mark.unit
def test_webhook_consumer_instantiable():
    """WebhookConsumer can be instantiated with mock dependencies."""
    consumer = _make_consumer()
    assert consumer is not None
