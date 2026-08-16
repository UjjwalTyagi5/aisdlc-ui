"""Prometheus metric singletons for the worker pool and connector hub.

Module-level objects so every worker process and the FastAPI app share one
registry per process. Pure prometheus_client — no FastAPI, no redis imports here.
"""
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

QUEUE_DEPTH = Gauge(
    "redis_stream_pending_messages",
    "Number of pending (unacknowledged) messages in stream",
    labelnames=["stream"],
)

TASK_DURATION = Histogram(
    "worker_task_duration_seconds",
    "Time taken to process a single agent task",
    labelnames=["agent_type"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600),
)

CONNECTOR_CALL_DURATION = Histogram(
    "connector_call_duration_seconds",
    "Time taken for a single connector API call",
    labelnames=["connector", "method"],
    buckets=(0.1, 0.5, 1, 2, 5, 15, 30),
)

DEAD_LETTER_DEPTH = Gauge(
    "dead_letter_queue_depth",
    "Number of messages in the dead letter stream",
    labelnames=["stream"],
)

# ── M6: Webhook Pipeline Metrics (REQ-M6-15) ──

WEBHOOK_DELIVERIES = Counter(
    "webhook_deliveries_total",
    "Total webhook deliveries by connector and status",
    labelnames=["connector", "status"],
)

WEBHOOK_PROCESSING_DURATION = Histogram(
    "webhook_processing_duration_ms",
    "Webhook processing latency in milliseconds",
    labelnames=["connector"],
    buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000),
)

WEBHOOK_DUPLICATES_REJECTED = Counter(
    "webhook_duplicates_rejected_total",
    "Webhook deliveries rejected as duplicates",
    labelnames=["connector"],
)

CONNECTOR_RATE_LIMIT_BACKOFFS = Counter(
    "connector_rate_limit_backoffs_total",
    "Rate limit 429 backoff events per connector and tenant",
    labelnames=["connector", "tenant_id"],
)

# ── M7.2: RBAC Enforcement Metrics (REQ-M7-23) ──

RBAC_DENIALS = Counter(
    "rbac_denials_total",
    "Permission denials by required permission and actor role",
    labelnames=["permission", "role"],
)

# ── M7.3: OIDC metrics (REQ-M7-23) ──

OIDC_AUTH_ATTEMPTS = Counter(
    "oidc_auth_attempts_total",
    "OIDC login/validation attempts by provider and outcome",
    labelnames=["provider", "outcome"],
    # outcome enum (bounded): success | tenant_not_found | bad_audience | invalid_token
    # provider enum (bounded): entra | okta — drawn from OIDC_PROVIDERS.keys() in providers.py
)

# ── M7.4: SCIM Provisioning Metrics (REQ-M7-23) ──

SCIM_PROVISION_DURATION = Histogram(
    "scim_provision_duration_seconds",
    "Duration of SCIM provision/deprovision operations",
    labelnames=["operation"],
    # operation enum (bounded): provision | deprovision — low-cardinality per 7.2/7.3 discipline
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

# ── M8: Audit Governance Metrics ──

AUDIT_WRITE_DURATION = Histogram(
    "audit_event_write_duration_ms",
    "Audit event DB write latency in milliseconds",
    labelnames=["agent_type"],
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000),
)

AUDIT_DEAD_LETTER_DEPTH = Gauge(
    "audit_dead_letter_depth",
    "Number of failed audit emits pending retry in the dead-letter stream",
)

EVIDENCE_EXPORT_DURATION = Histogram(
    "evidence_export_duration_seconds",
    "Evidence ZIP generation and upload latency",
    buckets=(1, 5, 15, 30, 60, 120, 300),
)

# ── M9: Observability — Agent + Connector Metrics (REQ-M9-02, REQ-M9-03) ──

AGENT_REQUEST_LATENCY = Histogram(
    "agent_request_latency_seconds",
    "Latency of an agent tool/LLM invocation, by agent type",
    labelnames=["agent_type"],
    buckets=(0.5, 1, 2, 5, 15, 30, 60, 120, 300),
)

AGENT_ERRORS = Counter(
    "agent_errors_total",
    "Agent errors by agent type and outcome",
    labelnames=["agent_type", "outcome"],
    # outcome enum (bounded): "error" only — success paths do not increment this counter
)

AGENT_TOKENS = Counter(
    "agent_tokens_total",
    "LLM tokens consumed by agent type, model, and kind",
    labelnames=["agent_type", "model", "kind"],
    # kind enum (bounded): "input" | "output"
)

CONNECTOR_CALL_OUTCOME = Counter(
    "connector_call_outcome_total",
    "Connector call outcomes by connector and status",
    labelnames=["connector", "status"],
    # status enum (bounded): ConnectorAuditEvent.status Literal["success", "error"]
)


# ── M9.2: Cost Attribution & Budget Metrics (REQ-M9-09) ──

TENANT_LLM_BUDGET_UTILIZATION = Gauge(
    "tenant_llm_budget_utilization",
    "Per-tenant LLM spend as a fraction of configured budget (>=0.8 = breach)",
    labelnames=["tenant_id"],
    # Bounded by active-tenant count. D-M9-02 cardinality discipline: tenant_id
    # ONLY — NEVER add run_id or model labels to this gauge. Alert DELIVERY on
    # breach is deferred to DLT-9 (settingup); this gauge is the computed
    # signal only.
)


def observe_connector_call(event: Any) -> None:
    """Record a connector call's duration + outcome (REQ-M9-03).

    Wires the previously-dead CONNECTOR_CALL_DURATION histogram and increments
    CONNECTOR_CALL_OUTCOME. Accepts a duck-typed ConnectorAuditEvent (no
    module-level import to avoid a circular import). Must NEVER raise — metrics
    must not break a connector call.
    """
    try:
        CONNECTOR_CALL_DURATION.labels(
            connector=event.connector_name, method=event.method
        ).observe(event.latency_ms / 1000.0)
        CONNECTOR_CALL_OUTCOME.labels(
            connector=event.connector_name, status=event.status
        ).inc()
    except Exception:
        pass
