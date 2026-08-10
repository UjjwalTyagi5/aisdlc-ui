"""Unit tests for the Prometheus metric singletons (REQ-M3-04).

Pure unit tests — no Redis, no HTTP. Verifies the four metrics are importable
and that observe/set/inc on labelled children do not raise on valid input.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="pre-existing import error — No module named 'prometheus_client' not installed; "
    "quarantined in milestone-4 Wave A"
)

try:
    from shared.services.metrics import (
        CONNECTOR_CALL_DURATION,
        DEAD_LETTER_DEPTH,
        QUEUE_DEPTH,
        TASK_DURATION,
    )
except (ImportError, ModuleNotFoundError):
    CONNECTOR_CALL_DURATION = None
    DEAD_LETTER_DEPTH = None
    QUEUE_DEPTH = None
    TASK_DURATION = None


@pytest.mark.unit
def test_task_duration_histogram_importable():
    """All four metric singletons import and are non-None."""
    assert TASK_DURATION is not None
    assert QUEUE_DEPTH is not None
    assert CONNECTOR_CALL_DURATION is not None
    assert DEAD_LETTER_DEPTH is not None


@pytest.mark.unit
def test_task_duration_histogram_observe():
    """Observing a valid float on the histogram child must not raise."""
    TASK_DURATION.labels(agent_type="requirements").observe(5.0)


@pytest.mark.unit
def test_queue_depth_gauge_set():
    """Setting the queue depth gauge child must not raise."""
    QUEUE_DEPTH.labels(stream="tasks:requirements").set(3)


@pytest.mark.unit
def test_dead_letter_depth_gauge_inc():
    """Incrementing the dead-letter gauge child must not raise."""
    DEAD_LETTER_DEPTH.labels(stream="tasks:requirements").inc()
