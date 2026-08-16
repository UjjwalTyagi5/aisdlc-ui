"""REQ-M9-01 — `GET /metrics` scrape contract test.

Asserts the ASGI app exposes every custom Prometheus metric singleton from
`shared/services/metrics.py` (pre-existing M3/M5/M6/M7/M8 metrics plus the M9
additions: agent_request_latency_seconds, agent_errors_total, agent_tokens_total,
connector_call_outcome_total, connector_call_duration_seconds).

`process_api.py` already wires `Instrumentator().instrument(app).expose(app)`,
which scrapes the default prometheus_client REGISTRY — the same registry every
`shared/services/metrics.py` singleton lives on. This test proves that wiring
holds and pins it against regression (T-9.1-08).

Counters/Histograms with no observed series omit their `# HELP`/`# TYPE` lines
until first touched, so this test touches each singleton with a representative
bounded label tuple before scraping — this both proves scrapeability and
documents the canonical label tuple for each metric (REQ-M9-04 cross-reference).

Pure ASGI in-process scrape — no DB, no live server.
"""
from __future__ import annotations

import httpx
import pytest

from shared.services import metrics as m


def _touch_all_singletons() -> None:
    """Observe a representative sample for every custom metric so its series
    renders in the /metrics scrape (prometheus_client omits untouched series).
    """
    m.QUEUE_DEPTH.labels(stream="agent-tasks").set(0)
    m.TASK_DURATION.labels(agent_type="requirements").observe(0)
    m.CONNECTOR_CALL_DURATION.labels(connector="azure_devops", method="list_projects").observe(0)
    m.DEAD_LETTER_DEPTH.labels(stream="agent-tasks").set(0)

    m.WEBHOOK_DELIVERIES.labels(connector="github", status="success").inc(0)
    m.WEBHOOK_PROCESSING_DURATION.labels(connector="github").observe(0)
    m.WEBHOOK_DUPLICATES_REJECTED.labels(connector="github").inc(0)
    m.CONNECTOR_RATE_LIMIT_BACKOFFS.labels(
        connector="azure_devops", tenant_id="00000000-0000-0000-0000-000000000001"
    ).inc(0)

    m.RBAC_DENIALS.labels(permission="run:create", role="developer").inc(0)

    m.OIDC_AUTH_ATTEMPTS.labels(provider="entra", outcome="success").inc(0)

    m.SCIM_PROVISION_DURATION.labels(operation="provision").observe(0)

    m.AUDIT_WRITE_DURATION.labels(agent_type="requirements").observe(0)
    m.AUDIT_DEAD_LETTER_DEPTH.set(0)
    m.EVIDENCE_EXPORT_DURATION.observe(0)

    # M9 additions
    m.AGENT_REQUEST_LATENCY.labels(agent_type="requirements").observe(0)
    m.AGENT_ERRORS.labels(agent_type="requirements", outcome="error").inc(0)
    m.AGENT_TOKENS.labels(agent_type="design", model="claude-sonnet-4-6", kind="input").inc(0)
    m.CONNECTOR_CALL_OUTCOME.labels(connector="azure_devops", status="success").inc(0)


# Exposition names — Counter base names render WITH the `_total` suffix;
# Histograms render as `_bucket`/`_sum`/`_count` families (we assert the
# `_count` family name, which is always present once touched).
EXPECTED_METRIC_NAMES = [
    # M3 worker pool / connector hub
    "redis_stream_pending_messages",
    "worker_task_duration_seconds_count",
    "connector_call_duration_seconds_count",
    "dead_letter_queue_depth",
    # M6 webhooks
    "webhook_deliveries_total",
    "webhook_processing_duration_ms_count",
    "webhook_duplicates_rejected_total",
    "connector_rate_limit_backoffs_total",
    # M7 RBAC / OIDC / SCIM
    "rbac_denials_total",
    "oidc_auth_attempts_total",
    "scim_provision_duration_seconds_count",
    # M8 audit governance
    "audit_event_write_duration_ms_count",
    "audit_dead_letter_depth",
    "evidence_export_duration_seconds_count",
    # M9 additions (REQ-M9-02 / REQ-M9-03)
    "agent_request_latency_seconds_count",
    "agent_errors_total",
    "agent_tokens_total",
    "connector_call_outcome_total",
]


@pytest.mark.unit
async def test_metrics_endpoint_returns_200_prometheus_exposition():
    from process_api import app

    _touch_all_singletons()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/plain" in content_type, f"Unexpected content-type: {content_type}"


@pytest.mark.unit
async def test_metrics_scrape_contains_every_custom_singleton():
    from process_api import app

    _touch_all_singletons()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/metrics")

    body = response.text

    missing = [name for name in EXPECTED_METRIC_NAMES if name not in body]
    assert not missing, (
        f"Custom metric(s) missing from /metrics scrape (registry wiring gap, "
        f"T-9.1-08): {missing}"
    )
