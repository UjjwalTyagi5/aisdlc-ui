"""Teach each project's Langfuse what its models cost, using LiteLLM's price table.

WHY THIS EXISTS. Langfuse prices a generation itself, at ingestion, by matching the
recorded model string against the models registered in that project. Nothing this
platform sends carries a cost, and nothing can: `langchain_litellm` builds
`llm_output = {"token_usage", "model"}` and drops LiteLLM's own
`_hidden_params.response_cost` before any callback sees it, and Langfuse's LangChain
handler (`on_llm_end`) calls `generation.update(output=, usage=, usage_details=, input=,
model=)` — it never passes `cost_details` on a success path. So the only way a real
number reaches a trace is for Langfuse itself to know the model.

It did not. Observed live on 2026-09-14: a design-agent turn recorded
`model=azure/gpt-5-mini`, 8,252 in / 345 out, `modelId: None`, `calculatedTotalCost: 0`.
Langfuse ships prices for a few hundred well-known models, and a tenant's BYOK model is
generally not one of them — least of all in the `provider/model` form LangChain reports
for ChatLiteLLM.

LITELLM IS THE PRICE SOURCE, here and in `shared/cost/pricing.py`. It is already a hard
dependency, `shared/services/model_catalog.py` already builds the BYOK picker straight
from `litellm.model_cost`, and it is keyed by the same strings the agents actually send.
One source for both ledgers is the point: the Cost & Budgets page and the Traces page
should never be able to quote different money for the same call.

THIS GOES THROUGH THE PUBLIC API, NOT THE DATABASE, which is the opposite of how
`provisioning.py` next door creates organizations and projects — and deliberately so.
That module writes SQL because Langfuse's org/project management API is Enterprise-only;
`/api/public/models` is not, so there is no reason to reach behind it. Writing the rows
directly was tried first and it APPEARS to work: the rows land, they are well-formed, and
Langfuse's own API reads them back correctly. Traces still cost $0, because Langfuse
caches model lookups per project — including the misses — and only its own write path
invalidates that cache. A price written in SQL takes effect whenever the cache happens to
expire, which is indistinguishable from the bug it was meant to fix. Measured on
2026-09-14: SQL-written row, still $0 twenty minutes later; same row re-created through
the API, correct cost on the very next trace.

THE MATCH PATTERN COVERS BOTH SPELLINGS of the model, because the same call is named
differently depending on who reports it — the usage meter sees `azure/gpt-5-mini`, a
direct-SDK caller sends the bare `gpt-5-mini`. One row covers both.

SEEDING IS LAZY, exactly like the bindings this hangs off (see bindings.py): a model is
priced the first time a project resolves it, not when the project is created. A project
that never runs an agent needs no prices, and the set of models a tenant may pick changes
without asking anyone. The cost is one extra call on a project's first turn with a new
model; `_SEEDED` makes every turn after that free.

FAIL-OPEN, LIKE EVERY OTHER OBSERVABILITY PATH HERE. Losing a price is a wrong number on
a page; it is never a reason to fail an agent run.

Backfill and re-sync with `python -m scripts.sync_langfuse_model_prices`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

# (langfuse_project_id, model_name) already written by THIS process. Bounded because a
# long-lived process serving many projects must not grow without limit — the same
# reasoning as bindings.py's client cache. Eviction just means one redundant round trip.
_SEEDED: set[tuple[str, str]] = set()
_SEEDED_MAX = 4096

# Strong references to in-flight background syncs. asyncio holds only a weak one, so a
# task with no other referent can be garbage-collected mid-flight — the documented way
# fire-and-forget work silently never runs.
_PENDING: set[Any] = set()

# Langfuse stores prices PER TOKEN; this platform reasons in per-million everywhere else
# (model_catalog, pricing, the model picker). One conversion, in one place.
_PER_MILLION = Decimal(1_000_000)

# Langfuse's `unit` vocabulary for a token-priced model.
_UNIT = "TOKENS"

# Bounded so a project with hundreds of registered models cannot turn one price check
# into an unbounded walk. 50 is the API's comfortable page size; Langfuse ships ~160
# built-ins, so this reaches every one of them and then some.
_MAX_PAGES = 20
_PAGE_SIZE = 50

_TIMEOUT_S = 15.0


def _bare(model: str) -> str:
    return model.rsplit("/", 1)[1] if "/" in model else model


def _match_pattern(model: str) -> str:
    """A Langfuse match pattern covering every spelling of ONE configured model.

    An alternation of the exact model id and its bare form, not a `(?:[^/]+/)?` wildcard
    prefix. The wildcard was the first attempt and it over-matches in two directions: it
    priced `openai/gpt-5-mini` off an Azure offering's row, and — because the bare row's
    pattern also matched the prefixed string — it left TWO rows competing to price the
    same call, with nothing deciding which won. Alternation over the spellings this
    model actually reports under is exact in both directions.

    `re.escape` because model ids carry regex metacharacters: `gpt-4.1`'s `.` matches any
    character, so an unescaped pattern would price `gpt-4o1` at `gpt-4.1`'s rate.
    """
    spellings = sorted({model, _bare(model)}, key=len, reverse=True)
    return f"(?i)^(?:{'|'.join(re.escape(s) for s in spellings)})$"


def _per_token_prices(model: str) -> Optional[tuple[Decimal, Decimal]]:
    """(input, output) USD per token for one model, or None if nothing prices it.

    Goes through `shared.cost.pricing`, so Langfuse is told exactly what the budget
    ledger charges — including the curated Anthropic rates, which override LiteLLM
    there and must override it here too or the two pages disagree by design.
    """
    from shared.cost.pricing import _prices_for  # noqa: PLC0415

    prices = _prices_for(model)
    if not prices:
        return None
    return (
        prices["input_per_mtok"] / _PER_MILLION,
        prices["output_per_mtok"] / _PER_MILLION,
    )


def _same_price(existing: dict, in_price: Decimal, out_price: Decimal, pattern: str) -> bool:
    """True when Langfuse already holds exactly this price, to the last significant digit.

    Compared as Decimal-of-str, not as float: the API hands prices back as JSON numbers,
    and `2.5e-07 != float(Decimal("0.00000025"))` is the kind of comparison that either
    rewrites a correct row on every sync or never updates a stale one.
    """
    try:
        return (
            existing.get("matchPattern") == pattern
            and Decimal(str(existing.get("inputPrice"))) == in_price
            and Decimal(str(existing.get("outputPrice"))) == out_price
        )
    except Exception:
        return False


async def _find_model(client, model: str) -> Optional[dict]:
    """This project's own registration for `model`, or None.

    Skips Langfuse-managed (built-in) entries. Those are shared defaults, not ours to
    delete, and on a shared instance deleting one would reprice another product's traces.
    """
    for page in range(1, _MAX_PAGES + 1):
        r = await client.get("/api/public/models", params={"limit": _PAGE_SIZE, "page": page})
        r.raise_for_status()
        body = r.json()
        for m in body.get("data", []):
            if m.get("modelName") == model and not m.get("isLangfuseManaged"):
                return m
        meta = body.get("meta") or {}
        if page >= int(meta.get("totalPages") or 1):
            return None
    return None


async def sync_model_price(
    *, host: str, public_key: str, secret_key: str, model: str, force: bool = False
) -> bool:
    """Register (or correct) one model's price in one Langfuse project.

    Returns True if anything was written. Raises on a genuine failure — callers on a
    run's path go through `ensure_model_price`, which swallows; the backfill script
    wants to hear about it.

    `force` re-registers a model whose price is ALREADY correct. That sounds pointless
    and is the one thing that fixes a project whose cached lookup predates the price:
    Langfuse invalidates on write, so a row that is right but was never written through
    the API leaves the miss cached until it expires on its own. Needed once for anything
    registered before this module used the API, and whenever a project traced a model
    before it was priced.
    """
    prices = _per_token_prices(model)
    if prices is None:
        logger.info(
            "langfuse prices: nothing prices %r — its traces will keep showing $0", model
        )
        return False
    in_price, out_price = prices
    pattern = _match_pattern(model)

    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(
        base_url=host.rstrip("/"), auth=(public_key, secret_key), timeout=_TIMEOUT_S
    ) as client:
        existing = await _find_model(client, model)
        if existing is not None:
            if not force and _same_price(existing, in_price, out_price, pattern):
                return False
            # DELETE then POST, because the public models API has no update verb. Safe
            # in the only direction that matters: Langfuse costs a generation when it
            # INGESTS it, so a already-stored trace keeps the cost it was given and this
            # window can only affect traces arriving in the same second.
            r = await client.delete(f"/api/public/models/{existing['id']}")
            r.raise_for_status()

        r = await client.post(
            "/api/public/models",
            json={
                "modelName": model,
                "matchPattern": pattern,
                "unit": _UNIT,
                # float() at the boundary only: JSON has no decimal type. Every figure
                # that reaches a ledger stays Decimal on this side of it.
                "inputPrice": float(in_price),
                "outputPrice": float(out_price),
            },
        )
        r.raise_for_status()
    return True


async def ensure_model_price(
    *, tenant_id: str, project_id: Optional[str], model: Optional[str]
) -> None:
    """Best-effort: make sure this project's Langfuse can price this model.

    Never raises and never blocks a run on a slow Langfuse. No binding (tracing off, or
    a project that has never traced) means there is nothing to teach.
    """
    if not (tenant_id and project_id and model):
        return
    try:
        from shared.db import get_db_session_for_tenant  # noqa: PLC0415
        from shared.observability.bindings import load_binding  # noqa: PLC0415

        async with get_db_session_for_tenant(str(tenant_id)) as session:
            binding = await load_binding(session, str(tenant_id), str(project_id))
        if binding is None:
            return

        key = (binding.langfuse_project_id, model)
        if key in _SEEDED:
            return
        # Claimed BEFORE the write, not after. Two turns starting together would
        # otherwise both see an empty cache and both write; the write is idempotent, so
        # the cost is a duplicate round trip rather than a duplicate row, but on a cold
        # process that is every agent racing the same registration.
        if len(_SEEDED) >= _SEEDED_MAX:
            _SEEDED.clear()
        _SEEDED.add(key)
    except Exception:  # pragma: no cover - a price is never worth an agent turn
        logger.debug("langfuse price sync: binding lookup failed (swallowed)", exc_info=True)
        return

    try:
        wrote = await sync_model_price(
            host=binding.langfuse_host,
            public_key=binding.public_key,
            secret_key=binding.secret_key,
            model=model,
        )
        if wrote:
            logger.info(
                "langfuse prices: %s is now priced in project %s",
                model, binding.langfuse_project_id,
            )
    except Exception:  # pragma: no cover - a price is never worth an agent turn
        # Release the claim so the next turn retries. Keeping it would make one
        # unreachable-Langfuse moment permanent for the life of the process.
        _SEEDED.discard(key)
        logger.debug("langfuse price sync failed (swallowed)", exc_info=True)


def schedule_model_price_sync(
    *, tenant_id: str, project_id: Optional[str], model: Optional[str]
) -> None:
    """Fire-and-forget `ensure_model_price`, for callers on a run's critical path.

    Model resolution happens before every agent turn and must not wait on a second
    service. A dropped task (process shutdown, no running loop) costs nothing: the next
    turn resolves the same model and schedules it again.
    """
    if not (tenant_id and project_id and model):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - no loop; nothing to schedule onto
        return
    task = loop.create_task(
        ensure_model_price(tenant_id=tenant_id, project_id=project_id, model=model)
    )
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
