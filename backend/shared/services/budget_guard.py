"""Hierarchical monthly budget enforcement (org ⊇ workspace ⊇ project).

`check_budgets(tenant_id, project_id)` is the single gate used at run-start (→ HTTP 409)
and at each model resolution (mid-run fail-closed). It raises `BudgetExceededError` when
the current calendar-month spend of ANY scope in the chain meets/exceeds that scope's
`monthly_budget_usd`. A NULL/0 budget means "no cap at this level" (inherit the parent).

Spend is read from the durable `usage_monthly` rollup (authoritative — it reflects every
completed LLM call). On a spend-read infra error the guard FAILS OPEN unless
`BUDGET_ENFORCE_FAIL_CLOSED=true` (a monitoring outage must not block every run by
default; the flag flips it for environments that require a hard cap).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy import text

from config.env import BUDGET_ENFORCE_FAIL_CLOSED
from shared.db import get_db_session_for_tenant
from shared.services.budget_store import read_scope_spend, resolve_scope_chain

logger = logging.getLogger(__name__)

_SCOPE_LABEL = {"org": "organization", "workspace": "workspace", "project": "project"}
_SCOPE_TABLE = {"org": "organizations", "workspace": "workspaces", "project": "projects"}


class BudgetExceededError(Exception):
    """A scope's monthly cost budget is exhausted — the run must not proceed."""

    def __init__(self, scope: str, scope_id: str, budget_usd: float, spent_usd: float):
        self.scope = scope
        self.scope_id = scope_id
        self.budget_usd = budget_usd
        self.spent_usd = spent_usd
        label = _SCOPE_LABEL.get(scope, scope)
        super().__init__(
            f"Monthly {label} budget exhausted (${spent_usd:.2f} of ${budget_usd:.2f} used). "
            f"Raise the {label} budget to continue."
        )


# (tenant:project) -> (budgets, fetched_at). Short TTL so budget edits take effect fast.
_BUDGET_CACHE: dict[str, tuple[list[tuple[str, str, Optional[float]]], float]] = {}
_BUDGET_TTL_SECONDS = 60


def clear_budget_cache() -> None:
    """Drop the cached budgets — call after a budget edit so it applies immediately."""
    _BUDGET_CACHE.clear()


async def _load_scope_budgets(
    tenant_id: str, project_id: Optional[str]
) -> list[tuple[str, str, Optional[float]]]:
    """[(scope, scope_id, monthly_budget_usd|None), ...] for the project's chain."""
    key = f"{tenant_id}:{project_id or ''}"
    cached = _BUDGET_CACHE.get(key)
    if cached and (time.monotonic() - cached[1]) < _BUDGET_TTL_SECONDS:
        return cached[0]

    chain = await resolve_scope_chain(tenant_id, project_id)
    budgets: list[tuple[str, str, Optional[float]]] = []
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as s:
            for scope, sid in chain:
                table = _SCOPE_TABLE[scope]  # fixed map — never user input
                row = (await s.execute(
                    text(f"SELECT monthly_budget_usd FROM {table} WHERE id = :id"),
                    {"id": sid},
                )).first()
                budget = float(row[0]) if row and row[0] is not None else None
                budgets.append((scope, sid, budget))
    except Exception:  # pragma: no cover - config read failure → no caps (fail open)
        logger.debug("budget load failed; treating as no budgets", exc_info=True)
        return []
    _BUDGET_CACHE[key] = (budgets, time.monotonic())
    return budgets


async def check_budgets(tenant_id: str, project_id: Optional[str] = None) -> None:
    """Raise BudgetExceededError if any scope in the chain is at/over its monthly budget."""
    if not tenant_id:
        return
    for scope, scope_id, budget in await _load_scope_budgets(tenant_id, project_id):
        if not budget or budget <= 0:
            continue  # no cap at this level
        try:
            spent = await read_scope_spend(tenant_id, scope, scope_id, strict=True)
        except Exception:
            if BUDGET_ENFORCE_FAIL_CLOSED:
                # Cannot confirm spend and policy says fail closed → block.
                raise BudgetExceededError(scope, scope_id, budget, budget)
            logger.debug("budget spend read failed; failing open (scope=%s)", scope)
            continue
        if spent >= budget:
            raise BudgetExceededError(scope, scope_id, budget, spent)
