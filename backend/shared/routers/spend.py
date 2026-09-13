"""Monthly spend split by one dimension — GET /cost/spend-series.

Backs the dashboard chart and its filters. Kept separate from /org/overview on
purpose: the overview changes only when the organization does, while this changes
on every filter click, and folding them together would refetch the connector and
people counts each time someone regroups a chart.

WHERE THE FIGURES COME FROM, and why it is two places:

  business_unit / project   `usage_monthly` -- the durable rollup, already keyed by
                            (scope, scope_id, month), which is exactly this chart's
                            shape. It is also what `budget_guard` blocks on and what
                            /cost/summary reports, so the chart and the budget bars
                            can no longer disagree.
  model / provider          Langfuse, per binding, with a monthly time dimension.
                            `usage_monthly` has no model column and never will --
                            it rolls up spend per SCOPE. Langfuse is the only source
                            that knows which model produced the cost.

This all read `agent_call_logs` joined to `runs`, and both tables are empty on a
platform whose spend is metered through the usage meter: the chart rendered "No
business unit spend in this selection" while /cost showed $0.0486 over five agents
and the budget bars showed the same money. Verified 2026-09-13 with seeded data --
ten traces, two projects, spend in usage_monthly, and a chart with nothing in it.

Months with no spend are emitted as 0.0 rather than omitted: the frontend charts
`points` positionally against `months`, so a gap would silently shift a series'
history sideways.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.read_scope import allowed_workspace_ids
from shared.db import get_db_session

logger = logging.getLogger(__name__)

spend_router = APIRouter(prefix="/cost")

_GROUP_BY = {"business_unit", "project", "model", "provider"}

class SpendSeriesEntryOut(BaseModel):
    id: str
    name: str
    points: list[float]


class SpendSeriesOut(BaseModel):
    months: list[str]
    groupBy: str
    series: list[SpendSeriesEntryOut]


def _month_labels(months: int) -> list[str]:
    """`YYYY-MM` labels, oldest first, ending with the current month."""
    now = datetime.now(tz=timezone.utc)
    out: list[str] = []
    y, m = now.year, now.month
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


async def _langfuse_month_series(
    db, request, labels, group_by, bucket_fn, *, allowed, scoped,
    workspace_id, project_id,
) -> None:
    """Fill month buckets for the `model` / `provider` groupings from Langfuse.

    Fans out one metrics query per binding, exactly as /cost does, and asks Langfuse
    to bucket by month (`timeDimension.granularity = "month"`), so a six-month chart
    is six numbers rather than six round trips per project.

    Fails soft per binding: `_lf_get` returns None on any error and the affected
    project simply contributes nothing, which is the same degradation the Traces and
    Cost pages already have. It is NOT silent about spend the way the old
    agent_call_logs query was -- that returned an empty chart on a healthy system.
    """
    from config.env import ENABLE_LANGFUSE  # noqa: PLC0415

    if not ENABLE_LANGFUSE:
        return

    import json as _json  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415

    from shared.observability.bindings import load_bindings  # noqa: PLC0415
    from shared.routers.traces import _lf_get  # noqa: PLC0415

    tenant_id = str(request.state.tenant_id)
    rows = (await db.execute(
        text(
            "select project_id, workspace_id from langfuse_bindings "
            "where tenant_id = cast(:t as uuid) and is_active = true"
        ),
        {"t": tenant_id},
    )).all()
    pids = [
        str(r[0]) for r in rows
        if (workspace_id in (None, "all") or str(r[1]) == str(workspace_id))
        and (not scoped or str(r[1]) in (allowed or []))
        and (project_id is None or str(r[0]) == str(project_id))
    ]
    if not pids:
        return

    # One extra month of slack on the lower bound: the oldest label is a whole month
    # and `date_trunc` on the first of it would clip anything before midnight UTC.
    oldest = datetime.now(timezone.utc) - timedelta(days=31 * len(labels))
    query = {
        "view": "observations",
        "metrics": [{"measure": "totalCost", "aggregation": "sum"}],
        "dimensions": [{"field": "providedModelName"}],
        "timeDimension": {"granularity": "month"},
        "fromTimestamp": oldest.isoformat(),
        "toTimestamp": datetime.now(timezone.utc).isoformat(),
    }

    for b in await load_bindings(db, tenant_id, pids):
        data = await _lf_get(
            "/api/public/metrics", {"query": _json.dumps(query)},
            host=b.langfuse_host, public_key=b.public_key, secret_key=b.secret_key,
        )
        for row in (data or {}).get("data") or []:
            model = row.get("providedModelName")
            if not model:  # non-LLM spans carry no model and no cost
                continue
            # "2026-09-01" -> "2026-09"
            ym = str(row.get("time_dimension") or "")[:7]
            if ym not in labels:
                continue
            # A provider is the part before the first "/" in a LiteLLM-style id;
            # a bare model name is its own provider, which is the honest answer.
            key = model if group_by == "model" else model.split("/", 1)[0]
            entry = bucket_fn(key, key)
            entry["points"][ym] += float(row.get("sum_totalCost") or 0.0)


@spend_router.get(
    "/spend-series",
    response_model=SpendSeriesOut,
    # Same spend data as GET /cost, which requires cost:view — this route asked only
    # for the view floor, so the money figures had two different gates depending on
    # which endpoint you reached them through. Its one consumer (SpendPanel) already
    # renders behind a governance check, so nothing user-visible changes.
    dependencies=[Depends(require_permission("cost:view"))],
)
async def spend_series(
    request: Request,
    groupBy: str = "business_unit",
    months: int = 6,
    workspaceId: Optional[str] = None,
    projectId: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> SpendSeriesOut:
    group_by = groupBy if groupBy in _GROUP_BY else "business_unit"
    months = max(1, min(24, months))
    labels = _month_labels(months)

    allowed = await allowed_workspace_ids(db, request)
    scoped = allowed is not None

    # A unit the caller cannot read is REFUSED, not ignored. Silently widening to
    # "all of mine" would answer a question about someone else's unit with the
    # viewer's own totals — a wrong answer presented as a right one.
    if workspaceId and workspaceId != "all":
        if scoped and workspaceId not in (allowed or []):
            raise HTTPException(status_code=404, detail="not found")

    by_bucket: dict[str, dict] = {}

    def _bucket(bid: str, name: str) -> dict:
        return by_bucket.setdefault(
            bid, {"name": name, "points": {label: 0.0 for label in labels}}
        )

    if group_by in ("business_unit", "project"):
        # THE DURABLE ROLLUP, not agent_call_logs. usage_monthly is already keyed by
        # (scope, scope_id, month) -- this chart's exact shape -- and is the same
        # figure the budget bars and /cost/summary show.
        scope = "workspace" if group_by == "business_unit" else "project"
        # 'YYYYMM' in storage, 'YYYY-MM' on the wire.
        keys = [label.replace("-", "") for label in labels]
        params: dict = {"t": str(request.state.tenant_id), "scope": scope, "keys": keys}

        if scope == "workspace":
            sql = (
                "SELECT u.scope_id::text AS bucket_id, w.display_name AS bucket_name, "
                "       u.month AS ym, COALESCE(SUM(u.cost_usd), 0) AS spend "
                "FROM usage_monthly u "
                "JOIN workspaces w ON w.id = u.scope_id "
                "WHERE u.tenant_id = CAST(:t AS uuid) AND u.scope = :scope "
                "  AND u.month = ANY(:keys) "
            )
            if scoped:
                sql += "  AND u.scope_id = ANY(CAST(:ws AS uuid[])) "
                params["ws"] = allowed or []
            if workspaceId and workspaceId != "all":
                sql += "  AND u.scope_id = CAST(:wid AS uuid) "
                params["wid"] = workspaceId
        else:
            sql = (
                "SELECT u.scope_id::text AS bucket_id, p.display_name AS bucket_name, "
                "       u.month AS ym, COALESCE(SUM(u.cost_usd), 0) AS spend "
                "FROM usage_monthly u "
                "JOIN projects p ON p.id = u.scope_id "
                "WHERE u.tenant_id = CAST(:t AS uuid) AND u.scope = :scope "
                "  AND u.month = ANY(:keys) "
            )
            if scoped:
                sql += "  AND p.workspace_id = ANY(CAST(:ws AS uuid[])) "
                params["ws"] = allowed or []
            if workspaceId and workspaceId != "all":
                sql += "  AND p.workspace_id = CAST(:wid AS uuid) "
                params["wid"] = workspaceId
            if projectId:
                sql += "  AND p.id = CAST(:pid AS uuid) "
                params["pid"] = projectId

        sql += "GROUP BY 1, 2, 3 ORDER BY 2"
        for r in (await db.execute(text(sql), params)).fetchall():
            ym = f"{r.ym[:4]}-{r.ym[4:]}"
            entry = _bucket(r.bucket_id, r.bucket_name or r.bucket_id)
            if ym in entry["points"]:
                entry["points"][ym] = float(r.spend or 0)

    else:
        # MODEL AND PROVIDER COME FROM LANGFUSE. usage_monthly rolls spend up per
        # scope and has no model column, so it cannot answer these at all; Langfuse
        # is the only source that knows which model produced the cost. One query per
        # binding, bucketed by month, mirroring how /cost fans out.
        await _langfuse_month_series(
            db, request, labels, group_by, _bucket,
            allowed=allowed, scoped=scoped,
            workspace_id=workspaceId, project_id=projectId,
        )

    return SpendSeriesOut(
        months=labels,
        groupBy=group_by,
        series=[
            SpendSeriesEntryOut(
                id=bucket_id,
                name=data["name"],
                points=[data["points"][label] for label in labels],
            )
            for bucket_id, data in by_bucket.items()
        ],
    )
