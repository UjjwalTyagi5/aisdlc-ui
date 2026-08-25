"""Durable + hot spend accounting for the budget hierarchy.

Two writers, one authoritative reader:
  - record_usage_rollup(...)  — UPSERT the durable Postgres `usage_monthly` rollup
    for each scope in a project's chain (project → workspace → org) AND bump the fast
    Redis month counters. Called from the usage meter on every LLM completion.
  - read_scope_spend(...)     — LIFETIME cost for one scope, summed across every
    month of the durable rollup (authoritative; reflects all completed calls). Used
    by budget_guard for enforcement.

BUDGETS ARE TOTALS, NOT ALLOWANCES. A scope's budget is what it may spend over its
life; it does not refill on the 1st. The rows stay keyed by month because per-month
history is worth having for reporting, but the month is a detail of how spend is
STORED — no reader treats it as the budget window.

Scope model: org (== tenant_id) ⊇ workspace ⊇ project. A standalone/chat call with no
project records only the org scope. Everything is best-effort — metering must never
break a run, so all writes swallow errors.

Calendar month (UTC, 'YYYYMM') matches the per-model meter's existing cost key.
"""
from __future__ import annotations

import logging
import time
import uuid as _uuid

from sqlalchemy import text

from shared.db import get_db_session_for_tenant

logger = logging.getLogger(__name__)


def month_key() -> str:
    t = time.gmtime()
    return f"{t.tm_year}{t.tm_mon:02d}"


# project_id -> (scope_chain, fetched_at). 5-min TTL — the hierarchy rarely changes and
# this avoids a DB round-trip on every LLM completion / resolve.
_CHAIN_CACHE: dict[str, tuple[list[tuple[str, str]], float]] = {}
_CHAIN_TTL_SECONDS = 300


async def resolve_scope_chain(tenant_id: str, project_id: str | None) -> list[tuple[str, str]]:
    """Return [(scope, scope_id), ...] to attribute a call's cost to.

    Always includes ('org', tenant_id). When project_id is given, adds
    ('workspace', workspace_id) + ('project', project_id) by looking up the project's
    workspace (tenant-scoped, cached). Lookup failure degrades to org-only.
    """
    chain: list[tuple[str, str]] = [("org", str(tenant_id))] if tenant_id else []
    if not project_id or not tenant_id:
        return chain

    cached = _CHAIN_CACHE.get(str(project_id))
    if cached and (time.monotonic() - cached[1]) < _CHAIN_TTL_SECONDS:
        return cached[0]

    workspace_id: str | None = None
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as s:
            row = (await s.execute(
                text("SELECT workspace_id FROM projects WHERE id = :pid"),
                {"pid": str(project_id)},
            )).first()
        if row and row[0]:
            workspace_id = str(row[0])
    except Exception:  # pragma: no cover - defensive; degrade to org-only
        logger.debug("resolve_scope_chain: project lookup failed", exc_info=True)

    full = list(chain)
    if workspace_id:
        full.append(("workspace", workspace_id))
    full.append(("project", str(project_id)))
    _CHAIN_CACHE[str(project_id)] = (full, time.monotonic())
    return full


async def workspace_id_for_project(tenant_id: str, project_id: str | None) -> str | None:
    """Resolve a project's workspace id (cached), or None. For Langfuse workspace tagging."""
    if not project_id or not tenant_id:
        return None
    try:
        return dict(await resolve_scope_chain(tenant_id, project_id)).get("workspace")
    except Exception:  # pragma: no cover - defensive; tracing tags are best-effort
        return None


async def record_usage_rollup(
    tenant_id: str, project_id: str | None, cost_usd: float, tokens: int
) -> None:
    """Attribute (cost, tokens) to every scope in the project's chain — durable + hot."""
    if not tenant_id or (not cost_usd and not tokens):
        return
    scopes = await resolve_scope_chain(tenant_id, project_id)
    if not scopes:
        return
    month = month_key()
    # Durable rollup (authoritative). One UPSERT per scope under the tenant RLS session.
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as s:
            for scope, scope_id in scopes:
                await s.execute(
                    text(
                        "INSERT INTO usage_monthly "
                        "(id, tenant_id, scope, scope_id, month, cost_usd, total_tokens, updated_at) "
                        "VALUES (:id, :t, :scope, :sid, :month, :cost, :tok, now()) "
                        "ON CONFLICT (tenant_id, scope, scope_id, month) DO UPDATE SET "
                        "cost_usd = usage_monthly.cost_usd + :cost, "
                        "total_tokens = usage_monthly.total_tokens + :tok, "
                        "updated_at = now()"
                    ),
                    {
                        "id": str(_uuid.uuid4()), "t": str(tenant_id), "scope": scope,
                        "sid": scope_id, "month": month,
                        "cost": float(cost_usd or 0.0), "tok": int(tokens or 0),
                    },
                )
            await s.commit()
    except Exception:  # pragma: no cover - metering must never break a run
        logger.debug("record_usage_rollup: durable UPSERT failed (swallowed)", exc_info=True)

    # Hot Redis counters (fast reporting / future dashboards). Best-effort.
    if cost_usd and cost_usd > 0:
        try:
            from shared.services.model_rate_limit import record_budget_cost  # noqa: PLC0415

            await record_budget_cost(scopes, float(cost_usd), month)
        except Exception:  # pragma: no cover
            logger.debug("record_usage_rollup: Redis bump failed (swallowed)", exc_info=True)


async def read_scope_spend(
    tenant_id: str, scope: str, scope_id: str, *, strict: bool = False
) -> float:
    """LIFETIME cost for one scope from the durable rollup (0.0 if none).

    SUMS EVERY MONTH, not the current one. A budget is a total the scope may spend
    over its life, not an allowance that refills on the 1st — so the figure this
    returns has to accumulate the same way. Reading only `month_key()` meant a
    project that exhausted its budget was blocked until the calendar turned over,
    and then silently free again.

    `usage_monthly` is unchanged and still keyed by month: the per-month rows are
    worth keeping for reporting, and summing them costs one aggregate. The month
    grain is now a detail of the STORAGE, not of the budget.

    strict=False (reporting): swallow infra errors and return 0.0 so a spend read never
    breaks a card/endpoint. strict=True (enforcement): re-raise so budget_guard can
    apply the fail-open / fail-closed policy explicitly.
    """
    if not tenant_id or not scope_id:
        return 0.0
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as s:
            row = (await s.execute(
                text(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_monthly "
                    "WHERE tenant_id = :t AND scope = :scope AND scope_id = :sid"
                ),
                {"t": str(tenant_id), "scope": scope, "sid": str(scope_id)},
            )).first()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:  # pragma: no cover - defensive
        logger.debug("read_scope_spend: read failed", exc_info=True)
        if strict:
            raise
        return 0.0
