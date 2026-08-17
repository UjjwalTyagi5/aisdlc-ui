"""Monthly spend split by one dimension — GET /cost/spend-series.

Backs the dashboard chart and its filters. Kept separate from /org/overview on
purpose: the overview changes only when the organization does, while this changes
on every filter click, and folding them together would refetch the connector and
people counts each time someone regroups a chart.

Every figure comes from agent_call_logs, reached per dimension through
runs -> projects -> workspaces. Months with no calls are emitted as 0.0 rather
than omitted: the frontend charts `points` positionally against `months`, so a
gap would silently shift a series' history sideways.
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

# How each grouping names its bucket. The join to workspaces is always present so
# the caller's unit scope can be applied uniformly, whatever the grouping.
_GROUP_SQL = {
    "business_unit": ("w.id::text", "w.display_name"),
    "project":       ("p.id::text", "p.display_name"),
    "model":         ("acl.model",  "acl.model"),
    # A provider is the part before the first '/' in a LiteLLM-style model id
    # (anthropic/claude-... -> anthropic). Models with no prefix are their own
    # provider, which is the honest answer for a bare custom model name.
    "provider":      ("split_part(acl.model, '/', 1)", "split_part(acl.model, '/', 1)"),
}


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


@spend_router.get(
    "/spend-series",
    response_model=SpendSeriesOut,
    dependencies=[Depends(require_permission("artifact:view"))],
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

    id_expr, name_expr = _GROUP_SQL[group_by]
    # make_interval() with an integer rather than CAST('N months' AS interval):
    # the driver binds the parameter by the type the cast declares, so a string
    # bound as an interval fails to encode before the query ever runs.
    where = [
        "acl.created_at >= date_trunc('month', now()) "
        "- make_interval(months => CAST(:span AS int))"
    ]
    params: dict = {"span": months - 1, "ws": allowed or []}
    if scoped:
        where.append("p.workspace_id = ANY(CAST(:ws AS uuid[]))")
    if workspaceId and workspaceId != "all":
        where.append("p.workspace_id = CAST(:wid AS uuid)")
        params["wid"] = workspaceId
    if projectId:
        where.append("p.id = CAST(:pid AS uuid)")
        params["pid"] = projectId

    rows = (await db.execute(
        text(
            f"SELECT {id_expr} AS bucket_id, {name_expr} AS bucket_name, "
            "       to_char(date_trunc('month', acl.created_at), 'YYYY-MM') AS ym, "
            "       COALESCE(SUM(acl.cost_usd), 0) AS spend "
            "FROM agent_call_logs acl "
            # r.id::text = acl.run_id rather than casting run_id to uuid: run_id is
            # a free string column and webhook runs can hold a provider key there,
            # which would make a uuid cast throw for the entire query.
            "JOIN runs r ON r.id::text = acl.run_id "
            "JOIN projects p ON p.id = r.project_id "
            "JOIN workspaces w ON w.id = p.workspace_id "
            "WHERE " + " AND ".join(where) +
            " GROUP BY 1, 2, 3 ORDER BY 2"
        ),
        params,
    )).fetchall()

    by_bucket: dict[str, dict] = {}
    for r in rows:
        entry = by_bucket.setdefault(
            r.bucket_id,
            {"name": r.bucket_name, "points": {label: 0.0 for label in labels}},
        )
        # A month outside the requested window can come back when a row sits exactly
        # on a boundary; drop it rather than widening the series past its labels.
        if r.ym in entry["points"]:
            entry["points"][r.ym] = float(r.spend or 0)

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
