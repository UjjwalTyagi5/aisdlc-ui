"""Langfuse observability — factory disabled-path guarantee + read-API mapping.

Runs without a live Langfuse server (Langfuse is disabled via env in the test .env,
and the read-API's Langfuse calls are monkeypatched). Validates:
  - build_agent_callbacks / langfuse_langchain_extras are zero-behavior-change no-ops
    when disabled (only the AuditCallbackHandler is returned).
  - The traces_router maps representative Langfuse JSON onto the exact frontend
    contract, honouring the AgentType alias (code_review -> review), level lowering,
    and enum safety.
  - Tenant isolation: get_trace refuses a trace not tagged for the caller's tenant.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from shared.observability import build_agent_callbacks, langfuse_langchain_extras


# ── Factory disabled-path (ENABLE_LANGFUSE is false in the test .env) ──────────

def test_build_agent_callbacks_disabled_returns_audit_only(monkeypatch):
    import contextlib

    # Force the disabled state regardless of the live .env (ENABLE_LANGFUSE may be
    # true locally). The contract: only the AuditCallbackHandler + a nullcontext.
    monkeypatch.setattr("shared.observability.callbacks.get_langfuse_client", lambda: None)

    callbacks, trace_cm = build_agent_callbacks(
        run_id="run-1", tenant_id="t1", agent_type="design"
    )
    names = [type(c).__name__ for c in callbacks]
    # Audit + the Redis usage meter are always present; Langfuse handler is not (disabled).
    assert names == ["AuditCallbackHandler", "UsageMeterCallbackHandler"]
    assert isinstance(trace_cm, contextlib.nullcontext)
    with trace_cm:  # must be a usable no-op
        pass


def test_langfuse_extras_disabled_returns_meter_only(monkeypatch):
    monkeypatch.setattr("shared.observability.callbacks.get_langfuse_client", lambda: None)
    cbs, meta = langfuse_langchain_extras(session_id="s1", tenant_id="t1", agent_type="requirements")
    # The usage meter is always attached (enforcement infra); no Langfuse handler when disabled.
    assert [type(c).__name__ for c in cbs] == ["UsageMeterCallbackHandler"]
    assert meta == {}


# ── Read-API mapping (pure) ───────────────────────────────────────────────────

def _sample_trace(trace_id="tr1", tenant="t1", agent="code_review"):
    return {
        "id": trace_id,
        "name": f"sdlc:{agent}",
        "timestamp": "2026-07-05T10:00:00.000Z",
        "tags": [f"tenant:{tenant}"],
        "metadata": {"agent_type": agent, "model": "claude-sonnet-4-6", "project_id": "p9"},
        "sessionId": "run-9",
        "userId": "u1",
        "latency": 2.5,
        "totalCost": 0.012,
        "environment": "default",
        "release": "v1",
        "htmlPath": "/project/lf-proj/traces/tr1",
        "observations": [
            {
                "id": "o1", "traceId": trace_id, "parentObservationId": None,
                "name": "planning", "type": "GENERATION", "level": "DEFAULT",
                "startTime": "2026-07-05T10:00:00.000Z", "endTime": "2026-07-05T10:00:01.500Z",
                "calculatedTotalCost": 0.008, "input": "prompt text", "output": "answer text",
            },
            {
                "id": "o2", "traceId": trace_id, "parentObservationId": "o1",
                "name": "semgrep", "type": "SPAN", "level": "ERROR",
                "startTime": "2026-07-05T10:00:01.500Z", "endTime": "2026-07-05T10:00:02.500Z",
                "statusMessage": "scanner failed",
            },
        ],
        "scores": [{"name": "quality", "value": 0.9, "comment": "good"}],
    }


def test_map_list_item_contract_and_agent_alias():
    from shared.routers.traces import _map_list_item

    row = _map_list_item(_sample_trace())
    # code_review -> review (frontend AgentType enum)
    assert row.agentType == "review"
    assert row.latencyMs == 2500  # 2.5s -> 2500ms
    assert row.cost.usd == pytest.approx(0.012)
    assert row.spanCount == 2
    assert row.status == "approved"  # list projection default
    assert row.worstLevel == "default"
    assert row.startedAt.startswith("2026-07-05")


def test_map_span_levels_offsets_and_status():
    from shared.routers.traces import _map_span, _parse_dt

    t = _sample_trace()
    start = _parse_dt(t["timestamp"])
    spans = [_map_span(o, start) for o in t["observations"]]
    gen, span = spans
    assert gen.type == "generation" and gen.level == "default"
    assert gen.startOffsetMs == 0 and gen.latencyMs == 1500
    assert gen.cost is not None and gen.cost.usd == pytest.approx(0.008)
    assert gen.inputPreview == "prompt text"
    # error span drives failed status + non-zero offset
    assert span.type == "span" and span.level == "error"
    assert span.status == "failed"
    assert span.startOffsetMs == 1500 and span.latencyMs == 1000


def test_unknown_agent_and_level_fall_back_to_valid_enums():
    from shared.routers.traces import _agent_type, _level

    assert _agent_type({}, "not-a-known-name") == "orchestrator"
    assert _agent_type({"agent_type": "design"}, "") == "design"
    assert _level("SOMETHING_WEIRD") == "default"
    assert _level("WARNING") == "warning"


# ── Endpoint behavior with monkeypatched Langfuse calls ───────────────────────

def _req(tenant_id="t1", permissions=("admin:*",)):
    """A fake request for the direct-call mapping tests.

    Org-wide by default. These tests exercise the Langfuse mapping and aggregation, not
    the scope filter, and a tenant-wide total is a thing only an org-wide caller gets —
    `_visible_projects` short-circuits on `is_org_wide` before it needs a DB session,
    which is why these can still be called with `db=None`.
    """
    return SimpleNamespace(
        state=SimpleNamespace(tenant_id=tenant_id, permissions=list(permissions), user_id="u")
    )


def test_get_trace_tenant_guard(monkeypatch):
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)

    async def _fake_get(path, params):
        return _sample_trace(tenant="OTHER_TENANT")

    monkeypatch.setattr(tr, "_lf_get", _fake_get)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(tr.get_trace(_req(tenant_id="t1"), "tr1", db=None))
    assert ei.value.status_code == 404  # cross-tenant trace is invisible


def test_get_trace_maps_detail_and_worst_level(monkeypatch):
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)

    async def _fake_get(path, params):
        return _sample_trace(tenant="t1")

    monkeypatch.setattr(tr, "_lf_get", _fake_get)

    out = asyncio.run(tr.get_trace(_req("t1"), "tr1", db=None))
    assert out.agentType == "review"
    assert len(out.spans) == 2
    assert out.worstLevel == "error"
    assert out.status == "failed"
    assert out.langfuseUrl and out.langfuseUrl.endswith("/project/lf-proj/traces/tr1")
    assert [s.name for s in out.scores] == ["quality"]


def test_list_and_metrics_disabled_return_empty(monkeypatch):
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: False)
    rows = asyncio.run(tr.list_traces(_req("t1"), db=None))
    assert rows == []
    m = asyncio.run(tr.trace_metrics(_req("t1"), window_days=30, db=None))
    assert m.totalTraces == 0 and m.byAgent == []


def test_metrics_aggregate(monkeypatch):
    import shared.routers.traces as tr

    monkeypatch.setattr(tr, "_enabled", lambda: True)
    page = {
        "data": [_sample_trace(trace_id="a"), _sample_trace(trace_id="b")],
        "meta": {"page": 1, "totalPages": 1},
    }

    async def _fake_get(path, params):
        return page

    monkeypatch.setattr(tr, "_lf_get", _fake_get)
    m = asyncio.run(tr.trace_metrics(_req("t1"), window_days=7, db=None))
    assert m.totalTraces == 2
    assert m.totalCostUsd == pytest.approx(0.024)
    assert m.byAgent and m.byAgent[0].agentType == "review"
    assert m.byAgent[0].traceCount == 2
