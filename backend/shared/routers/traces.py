"""Traces read-API — live proxy to the self-hosted Langfuse Public API.

Exposes GET /traces, /traces/metrics, /traces/{id} in the exact TraceListItem /
Trace / TraceMetrics contract the frontend already ships (apps/web/lib/schemas/
trace.ts). No local mirror table: each request queries Langfuse at
{LANGFUSE_HOST}/api/public/* with the server-side key pair and maps the response.

Tenant scoping (defense note):
  Langfuse is a single shared project; isolation is enforced HERE by always
  injecting tags=tenant:<request.state.tenant_id> into every Langfuse query — a
  caller can never read another tenant's traces. Unlike cost/audit (Postgres RLS),
  this is APP-LEVEL enforcement, so the tenant filter is applied unconditionally and
  is never taken from client input.

Graceful degradation: when ENABLE_LANGFUSE is off or keys are unset, the endpoints
return empty results / zeroed metrics / 404 rather than 5xx, so the /traces page
renders an empty state instead of erroring.

Mapping caveats (documented, best-effort v1):
  - Langfuse observation levels/types are upper-cased → lower-cased to the frontend
    SpanLevel/SpanType enums; unknown values fall back to "default"/"span".
  - agent_type "code_review" → frontend AgentType "review".
  - List rows leave status/worstLevel unset: the list endpoint returns observation
    ids, not observations, so there are no levels to fold into an outcome. The detail
    endpoint computes both from spans. They were hardcoded to "approved"/"default"
    until a failed run showing green in the table proved that a placeholder on an
    evidence surface is read as a finding.
  - errorRate is unset in the metrics aggregate for the same reason, rather than 0.0.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from config.env import (
    ENABLE_LANGFUSE,
    LANGFUSE_HOST,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
)
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.can_perform import visible_project_ids
from shared.authz.dependency import require_permission
from shared.authz.read_scope import is_org_wide
from shared.db import get_db_session
from shared.routers._schemas import (
    CostOut,
    ProjectCostSummaryOut,
    SpanOut,
    TraceListItemOut,
    TraceMetricsByAgentOut,
    TraceMetricsOut,
    TraceOut,
    TraceScoreOut,
)

logger = logging.getLogger(__name__)

traces_router = APIRouter()

# Langfuse observation type → frontend SpanType
_SPAN_TYPE = {"GENERATION": "generation", "SPAN": "span", "EVENT": "event"}
_SPAN_LEVELS = ("debug", "default", "warning", "error")
# agent_type used in our trace metadata → frontend AgentType enum
_AGENT_ALIAS = {"code_review": "review"}
_AGENT_TYPES = {
    "orchestrator", "requirements", "design", "development", "review",
    # "plan" is what pm_agent emits. Its absence here was silent rather than loud:
    # _agent_type() falls back to "orchestrator" for anything unrecognised, so every
    # PM trace was filed under a different agent and the per-agent metrics counted it
    # as one. The frontend AgentType enum has had "plan" all along.
    "plan",
    "security", "testing", "deployment", "documentation",
}


def _enabled() -> bool:
    # Host included deliberately: every read here builds a URL from it, and an empty
    # host would produce a relative request rather than an honest "tracing is off".
    return bool(
        ENABLE_LANGFUSE and LANGFUSE_HOST and LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
    )


def _auth_header() -> dict[str, str]:
    token = base64.b64encode(
        f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


# Short in-memory TTL cache for Langfuse reads. The free "Hobby" tier rate-limits the
# metrics API aggressively, and the cost / project-summary / traces pages each hit it on
# load — collapsing repeats within a window keeps us under the quota. Successful responses
# only; errors/None are never cached so a rate-limited call retries after the reset.
#
# BOUNDED, because the key includes every query parameter. Per-project fan-out and a
# user filter multiply the distinct key space — one key per (project, user, page) a
# caller touches — and an unbounded module-level dict holding Langfuse payloads for the
# life of the process is a slow leak, not a cache.
_LF_CACHE: dict[Any, tuple[float, dict]] = {}
_LF_CACHE_TTL_S = 60.0
_LF_CACHE_MAX = 512


def _lf_cache_store(key: Any, value: dict) -> None:
    """Insert, evicting the oldest entries once the cache is full."""
    import time  # noqa: PLC0415

    if len(_LF_CACHE) >= _LF_CACHE_MAX:
        # Drop expired entries first; only fall back to evicting live ones if that
        # freed nothing, so a burst of distinct queries cannot pin the cache full.
        now = time.monotonic()
        for k in [k for k, (ts, _) in _LF_CACHE.items() if (now - ts) >= _LF_CACHE_TTL_S]:
            _LF_CACHE.pop(k, None)
        while len(_LF_CACHE) >= _LF_CACHE_MAX:
            _LF_CACHE.pop(next(iter(_LF_CACHE)), None)
    _LF_CACHE[key] = (time.monotonic(), value)


def _lf_cache_key(path: str, params: dict[str, Any]):
    return (path, tuple(sorted(
        (k, tuple(v) if isinstance(v, (list, tuple)) else v) for k, v in params.items()
    )))


async def _lf_get(path: str, params: dict[str, Any]) -> Optional[dict]:
    """GET the Langfuse Public API; return parsed JSON or None on any failure (cached)."""
    import time  # noqa: PLC0415
    _key = _lf_cache_key(path, params)
    _hit = _LF_CACHE.get(_key)
    if _hit and (time.monotonic() - _hit[0]) < _LF_CACHE_TTL_S:
        return _hit[1]
    url = f"{LANGFUSE_HOST.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=_auth_header())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        _data = resp.json()
        _lf_cache_store(_key, _data)
        return _data
    except httpx.HTTPError as exc:
        # Langfuse unreachable / misconfigured host is an EXPECTED degraded state (the
        # endpoints still return empty/zeroed results). Log concisely — no traceback spam.
        logger.warning("Langfuse API call failed: %s (%s) — check LANGFUSE_HOST/keys",
                       path, type(exc).__name__)
        return None
    except Exception:
        logger.warning("Langfuse API call failed: %s", path, exc_info=True)
        return None


def _agent_type(meta: dict, name: str) -> str:
    raw = (meta or {}).get("agent_type") or ""
    if not raw and name.startswith("sdlc:"):
        raw = name.split("sdlc:", 1)[1]
    mapped = _AGENT_ALIAS.get(raw, raw)
    return mapped if mapped in _AGENT_TYPES else "orchestrator"


def _level(raw: Optional[str]) -> str:
    lvl = (raw or "").lower()
    return lvl if lvl in _SPAN_LEVELS else "default"


def _ms(seconds: Optional[float]) -> int:
    try:
        return max(0, int(round((seconds or 0) * 1000)))
    except Exception:
        return 0


def _map_list_item(t: dict) -> TraceListItemOut:
    meta = t.get("metadata") or {}
    observations = t.get("observations") or []
    # projectId/projectName MUST be non-empty — the frontend brands them z.string().min(1).
    # Chat/standalone traces have no pipeline project, so fall back to a stable label.
    _project = str(meta.get("project_id") or "").strip()
    return TraceListItemOut(
        id=str(t.get("id") or "unknown"),
        runId=meta.get("run_id") or t.get("sessionId"),
        projectId=_project or "standalone",
        projectName=_project or "Standalone",
        name=str(t.get("name") or "trace"),
        agentType=_agent_type(meta, str(t.get("name") or "")),
        userId=t.get("userId"),
        status=None,
        startedAt=str(t.get("timestamp") or _now_iso()),
        latencyMs=_ms(t.get("latency")),
        cost=CostOut(usd=float(t.get("totalCost") or 0.0), inputTokens=0, outputTokens=0),
        model=str(meta.get("model") or ""),
        spanCount=len(observations),
        environment=str(t.get("environment") or "default"),
        worstLevel=None,
        scores=[],
    )


def _map_span(o: dict, trace_start: Optional[datetime]) -> SpanOut:
    started = _parse_dt(o.get("startTime"))
    ended = _parse_dt(o.get("endTime"))
    offset_ms = 0
    if started and trace_start:
        offset_ms = max(0, int((started - trace_start).total_seconds() * 1000))
    latency_ms = 0
    if started and ended:
        latency_ms = max(0, int((ended - started).total_seconds() * 1000))
    level = _level(o.get("level"))
    cost_val = o.get("calculatedTotalCost")
    in_tok, out_tok = _obs_tokens(o)
    # Show a cost badge when there is either a cost OR token usage (a generation
    # can have tokens with cost 0 if the model isn't in Langfuse's price list).
    span_cost = (
        CostOut(usd=float(cost_val or 0.0), inputTokens=in_tok, outputTokens=out_tok)
        if (cost_val or in_tok or out_tok)
        else None
    )
    return SpanOut(
        id=str(o.get("id", "")),
        traceId=str(o.get("traceId", "")),
        parentId=o.get("parentObservationId"),
        name=str(o.get("name") or o.get("type") or "span"),
        type=_SPAN_TYPE.get(str(o.get("type") or "").upper(), "span"),
        level=level,
        startedAt=str(o.get("startTime") or _now_iso()),
        startOffsetMs=offset_ms,
        latencyMs=latency_ms,
        status="failed" if level == "error" else "approved",
        statusMessage=o.get("statusMessage"),
        cost=span_cost,
        inputPreview=_preview(o.get("input")),
        outputPreview=_preview(o.get("output")),
    )


def _obs_tokens(o: dict) -> tuple[int, int]:
    """Extract (input, output) token counts from a Langfuse observation.

    Langfuse surfaces usage as `usage: {input, output, total}` and/or
    `usageDetails: {input, output}`; older shapes use prompt/completion keys.
    """
    u = o.get("usage") or {}
    ud = o.get("usageDetails") or {}
    in_tok = u.get("input") or u.get("promptTokens") or ud.get("input") or 0
    out_tok = u.get("output") or u.get("completionTokens") or ud.get("output") or 0
    try:
        return int(in_tok or 0), int(out_tok or 0)
    except (TypeError, ValueError):
        return 0, 0


def _preview(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = val if isinstance(val, str) else str(val)
    return s[:280]


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tenant_tag(request: Request) -> str:
    return f"tenant:{request.state.tenant_id}"


async def _visible_projects(db: AsyncSession, request: Request) -> Optional[set[str]]:
    """The project ids this caller may see traces for, or None for the whole tenant.

    `trace:view` is held by project_admin and security_engineer and says they may read
    traces — not whose. Every route here was scoped by the Langfuse `tenant:` tag alone,
    so a project admin saw every project's execution traces, including their prompts and
    costs. See finding 4 in docs/rbac-audit-2026-08-17.md.
    """
    if is_org_wide(request):
        return None
    visible = await visible_project_ids(
        db,
        user_id=getattr(request.state, "user_id", "") or "",
        tenant_id=str(request.state.tenant_id),
    )
    return None if visible is None else set(visible)


# How many per-project Langfuse queries one /traces page may cost. Langfuse ANDs its
# tag filter, so "any of these N projects" has no single-query form and must be N
# queries merged. That is affordable for a project admin with a handful of projects and
# not for a caller who can see fifty; past this we take the short-page fallback instead
# of turning one page load into fifty round-trips.
_MAX_PROJECT_FANOUT = 8


async def _fanout_by_project(
    request: Request, project_ids: list[str], base: dict[str, Any], limit: int
) -> list[dict]:
    """One tagged query per visible project, merged newest-first and truncated to `limit`.

    This is what makes a scoped caller's first page a FULL page. Fetching one
    tenant-wide page and filtering it afterwards returns however many of those rows
    happened to belong to the caller — frequently far fewer than `limit`, sometimes
    zero, with no way to ask for the rest.

    Each sub-query asks for the full `limit` because any single project could supply
    the whole page. Merging on `timestamp` reproduces the newest-first order Langfuse
    would have returned had it been able to express the predicate itself.
    """
    import asyncio  # noqa: PLC0415

    results = await asyncio.gather(*[
        _lf_get(
            "/api/public/traces",
            {**base, "tags": [_tenant_tag(request), f"project:{pid}"]},
        )
        for pid in project_ids
    ])
    merged: list[dict] = []
    for raw in results:
        merged.extend((raw or {}).get("data") or [])
    merged.sort(key=lambda t: str(t.get("timestamp") or ""), reverse=True)
    return merged[:limit]


def _scope_rows(
    rows: list[TraceListItemOut], visible: Optional[set[str]]
) -> list[TraceListItemOut]:
    """Drop traces belonging to projects this caller cannot see.

    Filtering happens BEFORE any aggregate is computed, not after, so the metric cards
    are totals over the allowed set rather than a trimmed view of the organisation's.

    `standalone` traces — chat with no project attached — are dropped for a scoped
    caller. They carry no attribution, so there is no basis on which to decide they are
    this caller's rather than anyone else's, and a prompt is exactly the kind of content
    that should not default to visible.
    """
    if visible is None:
        return rows
    return [r for r in rows if r.projectId in visible]


def _apply_trace_filters(
    rows: list[TraceListItemOut], agent: Optional[str], project: Optional[str]
) -> list[TraceListItemOut]:
    """Client-side agent/project filtering shared by the list + metrics endpoints.

    Applied identically in both so the summary cards always match the table below
    them. `agent` arrives as the frontend AgentType (aliased back through
    _AGENT_ALIAS); `project` is the trace's project_id (or "standalone").
    """
    if agent:
        want = _AGENT_ALIAS.get(agent, agent)
        rows = [r for r in rows if r.agentType == want]
    if project:
        rows = [r for r in rows if r.projectId == project]
    return rows


async def _resolve_project_names(project_ids: set[str], tenant_id: str) -> dict[str, str]:
    """Map real project UUIDs -> Project.display_name (tenant-scoped, RLS-enforced).

    Trace metadata carries the project_id; the human name lives in Postgres. Best-effort:
    any failure returns {} so the traces list still renders (falls back to the id).
    """
    import uuid as _uuid  # noqa: PLC0415

    uuids = []
    for p in project_ids:
        if not p or p == "standalone":
            continue
        try:
            uuids.append(_uuid.UUID(str(p)))
        except (ValueError, TypeError):
            continue
    if not uuids or not tenant_id:
        return {}
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from shared.db import get_db_session_for_tenant  # noqa: PLC0415
        from shared.models.orm import Project  # noqa: PLC0415

        async with get_db_session_for_tenant(str(tenant_id)) as session:
            result = await session.execute(
                select(Project.id, Project.display_name).where(Project.id.in_(uuids))
            )
        return {str(pid): name for pid, name in result.all()}
    except Exception:  # pragma: no cover - defensive
        logger.warning("project name resolution failed", exc_info=True)
        return {}


@traces_router.get(
    "",
    response_model=list[TraceListItemOut],
    dependencies=[Depends(require_permission("trace:view"))],
)
async def list_traces(
    request: Request,
    agent: Optional[str] = None,
    project: Optional[str] = None,
    user: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> list[TraceListItemOut]:
    """List the traces the caller may see, newest first.

    FILTERING IS PUSHED INTO LANGFUSE WHERE LANGFUSE CAN DO IT, because a filter
    applied after the page comes back returns a short page and breaks paging:

      project -> an extra `project:<id>` tag. Langfuse ANDs its tag filter, which is
                 exactly right for one project.
      user    -> `userId`, a first-class query field on this endpoint.

    The remaining case is a scoped caller who has NOT picked a project: "any of these
    three projects" is not expressible in one tag query, so we issue one query per
    visible project and merge — the same shape cost.py:179-194 already uses per
    workspace, and for the same reason. Beyond _MAX_PROJECT_FANOUT that becomes more
    round-trips than it is worth, so we fall back to the old fetch-then-filter and
    accept a short page rather than hammering Langfuse.

    An org-wide caller keeps the single tenant-tagged query.
    """
    if not _enabled():
        return []
    visible = await _visible_projects(db, request)

    base: dict[str, Any] = {"limit": limit, "page": 1}
    if user:
        base["userId"] = user

    if project:
        # One project asked for: push it down. Scoping still runs afterwards — an
        # unauthorised project id must not become readable by naming it.
        raw = await _lf_get(
            "/api/public/traces",
            {**base, "tags": [_tenant_tag(request), f"project:{project}"]},
        )
        traces = (raw or {}).get("data") or []
    elif visible is not None and 0 < len(visible) <= _MAX_PROJECT_FANOUT:
        traces = await _fanout_by_project(request, sorted(visible), base, limit)
    elif visible is not None and not visible:
        # Sees no projects at all: no query to make, and no rows to show.
        traces = []
    else:
        raw = await _lf_get("/api/public/traces", {**base, "tags": _tenant_tag(request)})
        traces = (raw or {}).get("data") or []

    rows = _scope_rows([_map_list_item(t) for t in traces], visible)
    # Resolve real project display names (chat traces without a project stay "Standalone").
    names = await _resolve_project_names({r.projectId for r in rows}, request.state.tenant_id)
    for r in rows:
        if r.projectId == "standalone":
            r.projectName = "Standalone"
        else:
            r.projectName = names.get(r.projectId, r.projectName)
    # Frontend passes agentType in `agent`; apply client-side (list lacks a filter for it).
    return _apply_trace_filters(rows, agent, project)


@traces_router.get(
    "/project-summary",
    response_model=ProjectCostSummaryOut,
    dependencies=[Depends(require_permission("trace:view"))],
)
async def project_summary(
    request: Request,
    project_id: str,
    window_days: int = Query(7, ge=1, le=365),
    db: AsyncSession = Depends(get_db_session),
) -> ProjectCostSummaryOut:
    """Total LLM cost + input/output tokens for one project over the window.

    Sourced from Langfuse's daily-metrics endpoint, scoped by the tenant + project
    tags on the traces (only traces created after project tagging landed are counted).
    """
    empty = ProjectCostSummaryOut(
        projectId=project_id, windowDays=window_days, totalCostUsd=0.0,
        inputTokens=0, outputTokens=0, totalTokens=0, generatedAt=_now_iso(),
    )
    # A project the caller cannot see is refused rather than answered with zeroes:
    # zeroes are themselves a fact about it, and an indistinguishable one from "no
    # spend yet".
    visible = await _visible_projects(db, request)
    if visible is not None and project_id not in visible:
        raise HTTPException(status_code=404, detail="not found")
    if not _enabled():
        return empty
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    data = await _lf_get(
        "/api/public/metrics/daily",
        {
            "tags": [_tenant_tag(request), f"project:{project_id}"],
            "fromTimestamp": cutoff,
        },
    )
    days = (data or {}).get("data") or []
    total_cost = 0.0
    in_tok = out_tok = 0
    for day in days:
        total_cost += float(day.get("totalCost") or 0.0)
        for u in day.get("usage") or []:
            in_tok += int(u.get("inputUsage") or 0)
            out_tok += int(u.get("outputUsage") or 0)
    return ProjectCostSummaryOut(
        projectId=project_id,
        windowDays=window_days,
        totalCostUsd=round(total_cost, 6),
        inputTokens=in_tok,
        outputTokens=out_tok,
        totalTokens=in_tok + out_tok,
        generatedAt=_now_iso(),
    )


@traces_router.get(
    "/metrics",
    response_model=TraceMetricsOut,
    dependencies=[Depends(require_permission("trace:view"))],
)
async def trace_metrics(
    request: Request,
    window_days: int = Query(30, ge=1, le=365),
    agent: Optional[str] = None,
    project: Optional[str] = None,
    user: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> TraceMetricsOut:
    """Windowed aggregate over the tenant's traces (latency p50/p95, cost, by-agent).

    Honours the same agent/project/user filters as GET /traces so the summary cards
    reflect exactly the rows shown in the table (e.g. picking one project rescopes
    the trace count, latency, and cost to that project).

    `user` is pushed into Langfuse as `userId` rather than filtered here: the
    aggregate reads up to ten pages, and narrowing after the read would compute
    "this person's p95" from whichever of their traces happened to fall in those
    thousand rows.
    """
    empty = TraceMetricsOut(
        # errorRate stays unset even here: over zero traces there is no rate, and
        # "0.0%" would read as a measured all-clear rather than an absence of data.
        windowDays=window_days, totalTraces=0, errorRate=None,
        latencyP50Ms=0, latencyP95Ms=0, totalCostUsd=0.0, byAgent=[],
        generatedAt=_now_iso(),
    )
    if not _enabled():
        return empty
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    items: list[TraceListItemOut] = []
    for page in range(1, 11):  # cap at 10 pages (≤1000 traces) for the aggregate
        _params: dict[str, Any] = {
            "tags": [_tenant_tag(request)] + ([f"project:{project}"] if project else []),
            "limit": 100,
            "page": page,
            "fromTimestamp": cutoff,
        }
        if user:
            _params["userId"] = user
        data = await _lf_get("/api/public/traces", _params)
        batch = (data or {}).get("data") or []
        if not batch:
            break
        items.extend(_map_list_item(t) for t in batch)
        meta = (data or {}).get("meta") or {}
        if page >= int(meta.get("totalPages") or page):
            break
    # Scope FIRST, then apply the user's own filters: every figure below is a total,
    # and a total computed over the tenant and then trimmed would still have been
    # derived from rows the caller cannot open.
    items = _scope_rows(items, await _visible_projects(db, request))
    # Rescope to the selected agent/project so the cards match the filtered table.
    items = _apply_trace_filters(items, agent, project)
    if not items:
        return empty

    latencies = sorted(r.latencyMs for r in items)
    total_cost = round(sum(r.cost.usd for r in items), 6)
    by_agent: dict[str, list[TraceListItemOut]] = {}
    for r in items:
        by_agent.setdefault(r.agentType, []).append(r)

    return TraceMetricsOut(
        windowDays=window_days,
        totalTraces=len(items),
        # None, not 0.0: span levels are not on the list path, so "no errors" is
        # not something this aggregate is in a position to claim.
        errorRate=None,
        latencyP50Ms=_percentile(latencies, 0.50),
        latencyP95Ms=_percentile(latencies, 0.95),
        totalCostUsd=total_cost,
        byAgent=[
            TraceMetricsByAgentOut(
                agentType=a,
                traceCount=len(rs),
                errorRate=None,
                latencyP50Ms=_percentile(sorted(x.latencyMs for x in rs), 0.50),
                latencyP95Ms=_percentile(sorted(x.latencyMs for x in rs), 0.95),
                costUsd=round(sum(x.cost.usd for x in rs), 6),
            )
            for a, rs in sorted(by_agent.items())
        ],
        generatedAt=_now_iso(),
    )


@traces_router.get(
    "/{trace_id}",
    response_model=TraceOut,
    dependencies=[Depends(require_permission("trace:view"))],
)
async def get_trace(
    request: Request, trace_id: str, db: AsyncSession = Depends(get_db_session)
) -> TraceOut:
    """Full trace detail with spans + a deep-link into the Langfuse UI."""
    if not _enabled():
        raise HTTPException(status_code=404, detail="Tracing disabled")
    t = await _lf_get(f"/api/public/traces/{trace_id}", {})
    if t is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    # Tenant guard: refuse a trace that is not tagged for the caller's tenant.
    if _tenant_tag(request) not in (t.get("tags") or []):
        raise HTTPException(status_code=404, detail="Trace not found")

    base = _map_list_item(t)
    # Project guard, on top of the tenant guard above: the tenant tag stops a
    # cross-tenant read, not a cross-PROJECT one inside the same organisation.
    _visible = await _visible_projects(db, request)
    if _visible is not None and base.projectId not in _visible:
        raise HTTPException(status_code=404, detail="Trace not found")
    # Resolve the real project display name for the detail header.
    if base.projectId == "standalone":
        project_name = "Standalone"
    else:
        _names = await _resolve_project_names({base.projectId}, request.state.tenant_id)
        project_name = _names.get(base.projectId, base.projectName)
    trace_start = _parse_dt(t.get("timestamp"))
    observations = t.get("observations") or []
    spans = [_map_span(o, trace_start) for o in observations]
    # Trace-level token totals (shown as "cost · N tok" in the detail header) — the
    # trace LIST endpoint omits token usage, so sum the per-observation usage here.
    total_in = sum(_obs_tokens(o)[0] for o in observations)
    total_out = sum(_obs_tokens(o)[1] for o in observations)
    trace_cost = CostOut(
        usd=float(t.get("totalCost") or 0.0), inputTokens=total_in, outputTokens=total_out
    )
    worst = "default"
    for lvl in ("error", "warning", "default", "debug"):
        if any(s.level == lvl for s in spans):
            worst = lvl
            break
    html_path = t.get("htmlPath")
    langfuse_url = (
        f"{LANGFUSE_HOST.rstrip('/')}{html_path}" if html_path
        else f"{LANGFUSE_HOST.rstrip('/')}/project/{t.get('projectId', '')}/traces/{trace_id}"
    )
    return TraceOut(
        # userId arrives via base.model_dump() — it is on the list projection now, so
        # passing it here as well would be a duplicate keyword argument.
        **base.model_dump(),
        spans=spans,
        langfuseUrl=langfuse_url,
        release=t.get("release"),
    ).model_copy(update={
        "status": "failed" if worst == "error" else "approved",
        "worstLevel": worst,
        "cost": trace_cost,
        "projectName": project_name,
        "scores": [
            TraceScoreOut(name=str(s.get("name", "")), value=float(s.get("value") or 0.0),
                          comment=s.get("comment"))
            for s in (t.get("scores") or [])
            if isinstance(s, dict) and isinstance(s.get("value"), (int, float))
        ],
    })


def _percentile(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return int(sorted_vals[idx])
