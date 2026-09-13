"""GET /cost/spend-series — the dashboard's "Spend by ..." chart.

THIS ENDPOINT HAD NO TESTS, which is how it came to read two tables that this
platform never writes. It selected from `agent_call_logs` joined to `runs`; spend is
metered through the usage meter into `usage_monthly`, so both were empty and the
chart rendered "No business unit spend in this selection" while /cost reported
$0.0486 across five agents and the budget bars showed the same money.

Verified against seeded data on 2026-09-13: business_unit 0.0486, project
0.0243 + 0.0243 — reconciling with /cost exactly, which is the property that was
missing.
"""
from __future__ import annotations

import uuid as _uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio

TENANT = "00000000-0000-0000-0000-0000000000aa"
WS = "00000000-0000-0000-0000-0000000000b1"
P1 = "00000000-0000-0000-0000-0000000000c1"


def _session(rows):
    """Fake session that answers the usage_monthly query and nothing else."""

    async def _execute(stmt, params=None, *a, **kw):
        sql = str(getattr(stmt, "text", stmt))
        res = MagicMock()
        res.fetchall = MagicMock(return_value=rows if "usage_monthly" in sql else [])
        res.all = MagicMock(return_value=[])
        res.scalar = MagicMock(return_value=None)
        res.first = MagicMock(return_value=None)
        return res

    s = MagicMock()
    s.execute = AsyncMock(side_effect=_execute)
    return s


def _row(bucket_id, name, ym, spend):
    r = MagicMock()
    r.bucket_id, r.bucket_name, r.ym, r.spend = bucket_id, name, ym, spend
    return r


@pytest.fixture(autouse=True)
def _no_live_workspace(monkeypatch):
    async def _fake(request, tenant_id):
        request.state.workspace_id = _uuid.uuid4()
        return request.state.workspace_id

    monkeypatch.setattr(
        "shared.authz.dependency.active_workspace_for_request", _fake, raising=False
    )


async def _call(monkeypatch, rows, **params):
    import httpx
    from process_api import app
    from shared.db import get_db_session
    import shared.routers.spend as _spend

    async def _allowed(db, request):
        return None  # org-wide reader

    monkeypatch.setattr(_spend, "allowed_workspace_ids", _allowed)

    async def _override():
        yield _session(rows)

    app.dependency_overrides[get_db_session] = _override
    try:
        from tests.conftest import JWT_SECRET_KEY  # noqa: PLC0415
    except Exception:
        from config.env import JWT_SECRET_KEY  # noqa: PLC0415
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    token = pyjwt.encode(
        {"sub": "u1", "tenant_id": TENANT, "permissions": ["admin:*"],
         "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
        JWT_SECRET_KEY, algorithm="HS256",
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            return await c.get("/cost/spend-series",
                               headers={"Authorization": f"Bearer {token}"},
                               params=params)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.unit
async def test_business_unit_spend_comes_from_the_durable_rollup(monkeypatch):
    """The regression: this must read usage_monthly, not agent_call_logs."""
    seen: list[str] = []

    async def _execute(stmt, params=None, *a, **kw):
        sql = str(getattr(stmt, "text", stmt))
        seen.append(sql)
        res = MagicMock()
        res.fetchall = MagicMock(return_value=[])
        res.all = MagicMock(return_value=[])
        res.scalar = MagicMock(return_value=None)
        res.first = MagicMock(return_value=None)
        return res

    import httpx
    from process_api import app
    from shared.db import get_db_session
    import shared.routers.spend as _spend

    async def _allowed(db, request):
        return None

    monkeypatch.setattr(_spend, "allowed_workspace_ids", _allowed)

    async def _override():
        s = MagicMock()
        s.execute = AsyncMock(side_effect=_execute)
        yield s

    app.dependency_overrides[get_db_session] = _override
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    from config.env import JWT_SECRET_KEY

    token = pyjwt.encode(
        {"sub": "u1", "tenant_id": TENANT, "permissions": ["admin:*"],
         "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
        JWT_SECRET_KEY, algorithm="HS256")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get("/cost/spend-series",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"groupBy": "business_unit", "months": 3})
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert r.status_code == 200, r.text
    joined = " ".join(seen)
    assert "usage_monthly" in joined, "the chart must read the durable rollup"
    assert "agent_call_logs" not in joined, (
        "agent_call_logs is not written on this platform — reading it is what made "
        "the chart empty while spend existed"
    )


@pytest.mark.unit
async def test_months_are_positional_and_gaps_are_zero(monkeypatch):
    """`points` is charted positionally against `months`; a gap must be 0.0, never
    omitted, or a series' history silently shifts sideways."""
    from shared.routers.spend import _month_labels

    labels = _month_labels(3)
    newest = labels[-1].replace("-", "")
    r = await _call(monkeypatch, [_row(WS, "DEMO Payments", newest, 0.0486)],
                    groupBy="business_unit", months=3)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["months"] == labels
    assert len(body["series"]) == 1
    entry = body["series"][0]
    assert entry["name"] == "DEMO Payments"
    assert len(entry["points"]) == 3
    assert entry["points"][-1] == pytest.approx(0.0486)
    assert entry["points"][0] == 0.0 and entry["points"][1] == 0.0


@pytest.mark.unit
async def test_project_grouping_names_each_project(monkeypatch):
    from shared.routers.spend import _month_labels

    newest = _month_labels(6)[-1].replace("-", "")
    r = await _call(
        monkeypatch,
        [_row(P1, "DEMO Checkout", newest, 0.0243)],
        groupBy="project", months=6,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["groupBy"] == "project"
    assert [s["name"] for s in body["series"]] == ["DEMO Checkout"]


@pytest.mark.unit
async def test_an_unknown_grouping_falls_back_rather_than_erroring(monkeypatch):
    r = await _call(monkeypatch, [], groupBy="nonsense", months=3)
    assert r.status_code == 200, r.text
    assert r.json()["groupBy"] == "business_unit"
