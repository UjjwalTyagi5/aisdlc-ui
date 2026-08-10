"""REQ-M9-04 — Cardinality discipline test.

Enumerates every Counter/Gauge/Histogram singleton exported from
`shared/services/metrics.py` and asserts:

1. No metric carries a `run_id` (or other unbounded per-subject identifier)
   label — the FORBIDDEN set.
2. Every metric's label set is a subset of a curated, bounded ALLOWLIST —
   so any future out-of-vocabulary label fails CI loudly, forcing a
   conscious review (T-9.1-07, D-M9-02).
3. The four M9 singletons (AGENT_REQUEST_LATENCY, AGENT_ERRORS, AGENT_TOKENS,
   CONNECTOR_CALL_OUTCOME) carry exactly their intended bounded labels.

Pure introspection over `_labelnames` — no DB, no app, no event loop.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from shared.services import metrics as m

# Per-subject / unbounded identifiers that must never become a label value
# (information disclosure + cardinality-explosion DoS — T-9.1-06, T-9.1-07).
FORBIDDEN = {"run_id", "request_id", "user_id", "session_id", "item_id", "trace_id", "email", "ip"}

# Bounded label vocabulary. tenant_id is allowed ONLY on
# CONNECTOR_RATE_LIMIT_BACKOFFS (pre-existing M6 metric); the M9 additions
# must NOT use tenant_id.
ALLOWLIST = {
    "agent_type",
    "model",
    "kind",
    "outcome",
    "status",
    "connector",
    "method",
    "permission",
    "role",
    "provider",
    "operation",
    "phase",
    "stream",
    "activity",
    "tenant_id",
}

# tenant_id is reviewed-and-allowed ONLY on these metrics.
# Note: prometheus_client Counter._name strips the trailing "_total" suffix
# (the exposition name "connector_rate_limit_backoffs_total" renders from the
# base name "connector_rate_limit_backoffs").
# tenant_llm_budget_utilization (M9.2) is tenant_id-scoped by design per the
# locked cardinality decision D-M9-02 (per-tenant budget breach signal; tenant_id
# is the only label and is bounded by the tenant count).
TENANT_ID_ALLOWED_METRICS = {
    "connector_rate_limit_backoffs",
    "tenant_llm_budget_utilization",
}


def _all_metric_singletons() -> dict[str, Counter | Gauge | Histogram]:
    """Reflect over shared.services.metrics, collecting every prometheus_client
    metric object (Counter, Gauge, Histogram) defined at module level."""
    found = {}
    for name in dir(m):
        if name.startswith("_"):
            continue
        obj = getattr(m, name)
        if isinstance(obj, (Counter, Gauge, Histogram)):
            found[name] = obj
    return found


def test_enumerates_at_least_the_known_m9_singletons():
    singletons = _all_metric_singletons()
    expected_names = {
        "AGENT_REQUEST_LATENCY",
        "AGENT_ERRORS",
        "AGENT_TOKENS",
        "CONNECTOR_CALL_OUTCOME",
        "TASK_DURATION",
        "CONNECTOR_CALL_DURATION",
        "WEBHOOK_DELIVERIES",
        "RBAC_DENIALS",
        "OIDC_AUTH_ATTEMPTS",
        "SCIM_PROVISION_DURATION",
        "AUDIT_WRITE_DURATION",
    }
    missing = expected_names - set(singletons)
    assert not missing, f"Expected metric singletons not found in shared.services.metrics: {missing}"


def test_no_metric_carries_a_forbidden_label():
    singletons = _all_metric_singletons()
    violations = {}
    for name, metric in singletons.items():
        labelnames = set(metric._labelnames)
        forbidden_used = labelnames & FORBIDDEN
        if forbidden_used:
            violations[name] = forbidden_used

    assert not violations, (
        f"Metric(s) carry forbidden per-subject/unbounded labels (REQ-M9-04, "
        f"T-9.1-06): {violations}"
    )


def test_every_metric_label_is_in_bounded_allowlist():
    singletons = _all_metric_singletons()
    violations = {}
    for name, metric in singletons.items():
        labelnames = set(metric._labelnames)

        # tenant_id is only permitted on a single pre-existing metric.
        if "tenant_id" in labelnames and metric._name not in TENANT_ID_ALLOWED_METRICS:
            violations[name] = {"tenant_id (not in TENANT_ID_ALLOWED_METRICS)"}
            continue

        out_of_vocab = labelnames - ALLOWLIST
        if out_of_vocab:
            violations[name] = out_of_vocab

    assert not violations, (
        f"Metric(s) introduce out-of-allowlist label(s) — a conscious "
        f"ALLOWLIST update is required (REQ-M9-04, T-9.1-07): {violations}"
    )


def test_m9_singletons_carry_exactly_their_intended_labels():
    assert set(m.AGENT_REQUEST_LATENCY._labelnames) == {"agent_type"}
    assert set(m.AGENT_ERRORS._labelnames) == {"agent_type", "outcome"}
    assert set(m.AGENT_TOKENS._labelnames) == {"agent_type", "model", "kind"}
    assert set(m.CONNECTOR_CALL_OUTCOME._labelnames) == {"connector", "status"}

    # M9 additions never use tenant_id.
    for metric in (
        m.AGENT_REQUEST_LATENCY,
        m.AGENT_ERRORS,
        m.AGENT_TOKENS,
        m.CONNECTOR_CALL_OUTCOME,
    ):
        assert "tenant_id" not in metric._labelnames
        assert "run_id" not in metric._labelnames
