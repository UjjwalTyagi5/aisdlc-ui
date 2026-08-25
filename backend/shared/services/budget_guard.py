"""Hierarchical TOTAL budget enforcement (org ⊇ workspace ⊇ project).

`check_budgets(tenant_id, project_id)` is the single gate used at run-start (→ HTTP 409)
and at each model resolution (mid-run fail-closed). It raises `BudgetExceededError` when
the LIFETIME spend of ANY scope in the chain meets/exceeds that scope's budget. A NULL/0
budget means "no cap at this level" (inherit the parent).

A budget is a total, not a monthly allowance: it does not refill on the 1st. An
exhausted scope stays blocked until somebody raises the figure — which a Project Admin
does by raising a budget_increase request to their Business Unit Admin.

Spend is read from the durable `usage_monthly` rollup (authoritative — it reflects every
completed LLM call). On a spend-read infra error the guard FAILS OPEN unless
`BUDGET_ENFORCE_FAIL_CLOSED=true` (a monitoring outage must not block every run by
default; the flag flips it for environments that require a hard cap).
"""
from __future__ import annotations

from datetime import date

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


class BudgetWindowClosedError(Exception):
    """The scope has a budget, but today is outside the period it is authorised for.

    Distinct from BudgetExceededError on purpose: "you have spent it all" and "this
    was funded until March" need different answers from whoever reads them. The
    first is raised by a budget increase, the second by moving the dates.
    """

    def __init__(self, scope: str, scope_id: str, state: str, start: Optional[str], end: Optional[str]):
        self.scope = scope
        self.scope_id = scope_id
        self.state = state
        self.start = start
        self.end = end
        label = _SCOPE_LABEL.get(scope, scope)
        if state == "expired":
            detail = f"ended on {end}"
        else:
            detail = f"does not start until {start}"
        super().__init__(
            f"This {label}'s budget {detail}. Extend the budget period to continue."
        )


class BudgetExceededError(Exception):
    """A scope's total cost budget is exhausted — the run must not proceed."""

    def __init__(self, scope: str, scope_id: str, budget_usd: float, spent_usd: float):
        self.scope = scope
        self.scope_id = scope_id
        self.budget_usd = budget_usd
        self.spent_usd = spent_usd
        label = _SCOPE_LABEL.get(scope, scope)
        super().__init__(
            f"Total {label} budget exhausted (${spent_usd:.2f} of ${budget_usd:.2f} used). "
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
) -> list[tuple[str, str, Optional[float], Optional[str], Optional[str]]]:
    """[(scope, scope_id, budget|None, start|None, end|None), ...] for the chain.

    The window is a PROJECT column only (migration 0035) — the dates are set at
    project creation, and workspaces/orgs carry none. Those scopes report (None,
    None), which `_window_state` reads as "no window" and lets through.
    """
    key = f"{tenant_id}:{project_id or ''}"
    cached = _BUDGET_CACHE.get(key)
    if cached and (time.monotonic() - cached[1]) < _BUDGET_TTL_SECONDS:
        return cached[0]

    chain = await resolve_scope_chain(tenant_id, project_id)
    budgets: list[tuple[str, str, Optional[float], Optional[str], Optional[str]]] = []
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as s:
            for scope, sid in chain:
                table = _SCOPE_TABLE[scope]  # fixed map — never user input
                cols = (
                    "monthly_budget_usd, budget_start_date, budget_end_date"
                    if scope == "project"
                    else "monthly_budget_usd, NULL, NULL"
                )
                row = (await s.execute(
                    text(f"SELECT {cols} FROM {table} WHERE id = :id"),
                    {"id": sid},
                )).first()
                budget = float(row[0]) if row and row[0] is not None else None
                start = row[1].isoformat() if row and row[1] is not None else None
                end = row[2].isoformat() if row and row[2] is not None else None
                budgets.append((scope, sid, budget, start, end))
    except Exception:  # pragma: no cover - config read failure → no caps (fail open)
        logger.debug("budget load failed; treating as no budgets", exc_info=True)
        return []
    _BUDGET_CACHE[key] = (budgets, time.monotonic())
    return budgets


def _window_state(start: Optional[str], end: Optional[str], today: Optional[str] = None) -> str:
    """"none" | "scheduled" | "active" | "expired".

    Mirrors budgetWindowState in frontend/lib/schemas/budget-window.ts, including
    why the comparison is on strings: `YYYY-MM-DD` sorts lexicographically the same
    way it sorts chronologically, so this needs no date parsing and raises no
    timezone question a budget period has no useful answer to.
    """
    if not start and not end:
        return "none"
    today = today or date.today().isoformat()
    if end and today > end:
        return "expired"
    if start and today < start:
        return "scheduled"
    return "active"


async def check_budgets(tenant_id: str, project_id: Optional[str] = None) -> None:
    """Refuse a run whose chain is over budget, or outside its budget's window."""
    if not tenant_id:
        return
    for scope, scope_id, budget, start, end in await _load_scope_budgets(tenant_id, project_id):
        if not budget or budget <= 0:
            continue  # no cap at this level

        # THE WINDOW QUALIFIES THE BUDGET, so it is checked only where there is one.
        # A scope with no cap has no funding period to expire, and blocking it would
        # invent a limit nobody set. Checked BEFORE spend: outside the window the
        # amount spent is beside the point, and reading it would put a database
        # round-trip in front of an answer already known.
        state = _window_state(start, end)
        if state in ("scheduled", "expired"):
            raise BudgetWindowClosedError(scope, scope_id, state, start, end)
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
