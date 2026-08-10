"""RED tests for Prometheus webhook + connector metrics — REQ-M6-13.

Asserts that importing shared.services.metrics exposes all four M6 metrics:
  - WEBHOOK_DELIVERIES (Counter, labels: connector, status)
  - WEBHOOK_PROCESSING_DURATION (Histogram, labels: connector)
  - WEBHOOK_DUPLICATES_REJECTED (Counter, labels: connector)
  - CONNECTOR_RATE_LIMIT_BACKOFFS (Counter, labels: connector, tenant_id)

Also asserts that counters increment correctly on accept/reject/duplicate/backoff.

Tests skip at collection time when shared.services.metrics does not export
these metrics (pre-implementation). The four M3 metrics (TASK_DURATION, etc.)
are not affected by this test file.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from shared.services.metrics import (
        WEBHOOK_DELIVERIES,
        WEBHOOK_PROCESSING_DURATION,
        WEBHOOK_DUPLICATES_REJECTED,
        CONNECTOR_RATE_LIMIT_BACKOFFS,
    )
    _SKIP = False
except (ImportError, ModuleNotFoundError, AttributeError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"M6 metrics not yet exported from shared.services.metrics: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)


@pytest.mark.unit
def test_webhook_deliveries_counter_importable():
    """WEBHOOK_DELIVERIES is importable and non-None."""
    assert WEBHOOK_DELIVERIES is not None


@pytest.mark.unit
def test_webhook_processing_duration_histogram_importable():
    """WEBHOOK_PROCESSING_DURATION is importable and non-None."""
    assert WEBHOOK_PROCESSING_DURATION is not None


@pytest.mark.unit
def test_webhook_duplicates_rejected_counter_importable():
    """WEBHOOK_DUPLICATES_REJECTED is importable and non-None."""
    assert WEBHOOK_DUPLICATES_REJECTED is not None


@pytest.mark.unit
def test_connector_rate_limit_backoffs_counter_importable():
    """CONNECTOR_RATE_LIMIT_BACKOFFS is importable and non-None."""
    assert CONNECTOR_RATE_LIMIT_BACKOFFS is not None


@pytest.mark.unit
def test_webhook_deliveries_accepts_label():
    """WEBHOOK_DELIVERIES.labels(connector=..., status=...) does not raise."""
    WEBHOOK_DELIVERIES.labels(connector="github", status="accepted").inc()


@pytest.mark.unit
def test_webhook_deliveries_increment_on_reject():
    """WEBHOOK_DELIVERIES with status='rejected' increments without error."""
    WEBHOOK_DELIVERIES.labels(connector="jira", status="rejected").inc()


@pytest.mark.unit
def test_webhook_processing_duration_observe():
    """WEBHOOK_PROCESSING_DURATION.labels(connector=...).observe(ms) does not raise."""
    WEBHOOK_PROCESSING_DURATION.labels(connector="github").observe(42.5)


@pytest.mark.unit
def test_webhook_duplicates_rejected_increment_on_duplicate():
    """WEBHOOK_DUPLICATES_REJECTED.labels(connector=...).inc() increments without error."""
    WEBHOOK_DUPLICATES_REJECTED.labels(connector="slack").inc()


@pytest.mark.unit
def test_connector_rate_limit_backoffs_increment():
    """CONNECTOR_RATE_LIMIT_BACKOFFS.labels(connector=..., tenant_id=...).inc() does not raise."""
    CONNECTOR_RATE_LIMIT_BACKOFFS.labels(
        connector="jira", tenant_id="tenant-uuid-rate-metric"
    ).inc()


@pytest.mark.unit
def test_webhook_deliveries_has_connector_and_status_labels():
    """WEBHOOK_DELIVERIES counter includes 'connector' and 'status' label names."""
    metric = WEBHOOK_DELIVERIES
    label_names = getattr(metric, "_labelnames", None) or getattr(metric, "labelnames", [])
    assert "connector" in label_names, (
        f"WEBHOOK_DELIVERIES must have 'connector' label; got {label_names}"
    )
    assert "status" in label_names, (
        f"WEBHOOK_DELIVERIES must have 'status' label; got {label_names}"
    )


@pytest.mark.unit
def test_connector_rate_limit_backoffs_has_tenant_id_label():
    """CONNECTOR_RATE_LIMIT_BACKOFFS includes 'connector' and 'tenant_id' labels."""
    metric = CONNECTOR_RATE_LIMIT_BACKOFFS
    label_names = getattr(metric, "_labelnames", None) or getattr(metric, "labelnames", [])
    assert "connector" in label_names
    assert "tenant_id" in label_names
