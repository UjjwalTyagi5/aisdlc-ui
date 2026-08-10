"""REQ-M9-09 — tenant_llm_budget_utilization gauge + budget-breach signal unit tests.

Asserts:
  - tenant_llm_budget_utilization gauge exists with ONLY a tenant_id label
    (D-M9-02 cardinality discipline — no run_id/model/PII labels)
  - compute_budget_utilization(tenant_id, spend_usd, budget_usd) sets the gauge
    and returns {utilization, breached_80, budget_usd, spend_usd}
  - breached_80 flags at >=80% of budget
  - budget_usd <= 0 yields utilization 0 and breached_80 False (no divide-by-zero)
  - resolve_tenant_budget falls back to LLM_TENANT_BUDGET_USD_DEFAULT when no
    per-tenant override is configured, and honors LLM_TENANT_BUDGET_OVERRIDES
  - no alert-delivery / notification call is present in cost.py (REQ-M9-09 scope)
"""
from __future__ import annotations

import inspect

import pytest

from shared.services.metrics import TENANT_LLM_BUDGET_UTILIZATION


@pytest.mark.unit
def test_gauge_has_only_tenant_id_label():
    """tenant_llm_budget_utilization carries ONLY a tenant_id label (D-M9-02)."""
    assert TENANT_LLM_BUDGET_UTILIZATION._labelnames == ("tenant_id",)


@pytest.mark.unit
def test_compute_budget_utilization_sets_gauge_and_flags_breach():
    from shared.routers.cost import compute_budget_utilization

    tenant_id = "00000000-0000-0000-0000-000000000099"

    # 80% of budget exactly -> breached
    result = compute_budget_utilization(tenant_id, spend_usd=80.0, budget_usd=100.0)
    assert result["utilization"] == pytest.approx(0.8)
    assert result["breached_80"] is True
    assert result["budget_usd"] == 100.0
    assert result["spend_usd"] == 80.0

    gauge_value = TENANT_LLM_BUDGET_UTILIZATION.labels(tenant_id=tenant_id)._value.get()
    assert gauge_value == pytest.approx(0.8)


@pytest.mark.unit
def test_compute_budget_utilization_below_breach_threshold():
    from shared.routers.cost import compute_budget_utilization

    result = compute_budget_utilization(
        "00000000-0000-0000-0000-000000000098", spend_usd=10.0, budget_usd=100.0
    )
    assert result["utilization"] == pytest.approx(0.1)
    assert result["breached_80"] is False


@pytest.mark.unit
def test_compute_budget_utilization_zero_budget_no_divide_by_zero():
    from shared.routers.cost import compute_budget_utilization

    result = compute_budget_utilization(
        "00000000-0000-0000-0000-000000000097", spend_usd=50.0, budget_usd=0.0
    )
    assert result["utilization"] == 0.0
    assert result["breached_80"] is False


@pytest.mark.unit
def test_compute_budget_utilization_negative_budget_no_divide_by_zero():
    from shared.routers.cost import compute_budget_utilization

    result = compute_budget_utilization(
        "00000000-0000-0000-0000-000000000096", spend_usd=50.0, budget_usd=-5.0
    )
    assert result["utilization"] == 0.0
    assert result["breached_80"] is False


@pytest.mark.unit
def test_resolve_tenant_budget_falls_back_to_default(monkeypatch):
    from shared.routers import cost as cost_module

    monkeypatch.setattr(cost_module, "LLM_TENANT_BUDGET_USD_DEFAULT", 250.0)
    monkeypatch.setattr(cost_module, "LLM_TENANT_BUDGET_OVERRIDES_JSON", "")

    assert cost_module.resolve_tenant_budget("00000000-0000-0000-0000-000000000001") == 250.0


@pytest.mark.unit
def test_resolve_tenant_budget_honors_per_tenant_override(monkeypatch):
    from shared.routers import cost as cost_module

    tenant_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(cost_module, "LLM_TENANT_BUDGET_USD_DEFAULT", 250.0)
    monkeypatch.setattr(
        cost_module, "LLM_TENANT_BUDGET_OVERRIDES_JSON", '{"%s": 500.0}' % tenant_id
    )

    assert cost_module.resolve_tenant_budget(tenant_id) == 500.0
    # Other tenants still get the default
    assert (
        cost_module.resolve_tenant_budget("00000000-0000-0000-0000-000000000002") == 250.0
    )


@pytest.mark.unit
def test_resolve_tenant_budget_invalid_overrides_json_falls_back(monkeypatch):
    """Malformed LLM_TENANT_BUDGET_OVERRIDES must not raise — default-only fallback."""
    from shared.routers import cost as cost_module

    monkeypatch.setattr(cost_module, "LLM_TENANT_BUDGET_USD_DEFAULT", 100.0)
    monkeypatch.setattr(cost_module, "LLM_TENANT_BUDGET_OVERRIDES_JSON", "{not valid json")

    assert cost_module.resolve_tenant_budget("00000000-0000-0000-0000-000000000001") == 100.0


@pytest.mark.unit
def test_no_alert_delivery_code_in_cost_module():
    """REQ-M9-09: alert DELIVERY is deferred to DLT-9 — assert no notification call ships."""
    from shared.routers import cost as cost_module

    source = inspect.getsource(cost_module)
    forbidden_terms = ["send_alert", "notify(", "send_notification", "smtp", "webhook_send"]
    for term in forbidden_terms:
        assert term.lower() not in source.lower(), (
            f"Found forbidden alert-delivery reference '{term}' in cost.py — "
            "REQ-M9-09 covers the computed signal only; delivery is DLT-9"
        )
