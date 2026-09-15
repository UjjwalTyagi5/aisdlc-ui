"""UsageMeterCallbackHandler — records per-model token + cost usage into Redis.

This is the reliable, self-contained feed for TPM + monthly-cost enforcement. It runs
as a LangChain callback and, on every LLM completion, increments the offering's Redis
TPM window + monthly cost counter (shared.services.model_rate_limit.record_usage).
resolve_model_for_run then reads those counters to enforce tpm_limit / cost_limit_usd.

"Reliable" is now true. It claimed to run "both sync + async variants for astream
coverage" and did neither: LangChain resolves the hook by name, so the `aon_llm_end`
half was dead code, and the sync half needed a running event loop it never had on the
async path. See `on_llm_end` for what that cost.

Why not the existing AuditCallbackHandler: its DB writes (agent_call_logs) are not
landing in this environment; this meter writes only to Redis (no RLS, no ORM) so
metering is decoupled from that broken path.

The offering to attribute usage to is taken from the constructor when known
(pipeline activities pass input.offering_id) and otherwise from the run's
resolved-model contextvar (get_resolved_model), which the agent nodes set.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from langchain_core.callbacks.base import BaseCallbackHandler

logger = logging.getLogger(__name__)

# How long an executor thread will wait for the rollup it handed to the event loop.
# Bounded so a wedged database cannot pin a LangChain worker thread for the life of the
# process; generous enough that an ordinary upsert never trips it.
_RECORD_TIMEOUT_S = 15.0

# Strong references to scheduled rollups. asyncio keeps only a weak one, so a task with
# no other referent can be collected before it runs — silent lost spend, again.
_PENDING: set[Any] = set()


class UsageMeterCallbackHandler(BaseCallbackHandler):
    def __init__(
        self,
        tenant_id: str,
        offering_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._tenant_id = tenant_id or ""
        self._offering_id = offering_id or ""
        self._project_id = str(project_id) if project_id else ""
        # THE LOOP THIS HANDLER WAS BUILT ON. `on_llm_end` is a sync callback, and
        # LangChain runs sync callbacks for an async run inside `run_in_executor` — a
        # worker thread with no running loop of its own (callbacks/manager.py:375-387).
        # Without a loop captured here there is nothing to schedule the rollup onto from
        # that thread, which is exactly how every write was lost. Constructed on the
        # request's loop by `langfuse_langchain_extras`, so this is that loop.
        try:
            self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - built outside a loop; sync path only
            self._loop = None

    def _offering(self) -> str:
        if self._offering_id:
            return self._offering_id
        try:
            from shared.services.model_resolver import get_resolved_model  # noqa: PLC0415

            rm = get_resolved_model()
            return rm.offering_id if rm else ""
        except Exception:  # pragma: no cover - defensive
            return ""

    @staticmethod
    def _resolved_model_name() -> str:
        """The run's model from the resolver contextvar, or "" if there is none.

        WHY A SECOND SOURCE FOR THE MODEL NAME. A STREAMING run arrives here with an
        EMPTY `llm_output`: LangChain's `_combine_llm_outputs` returns `{}` (chat_models
        .py:653) and `langchain_litellm` does not override it, so the name that was in
        `_create_chat_result` never survives the streaming path. Tokens still arrive via
        `usage_metadata`, so this looked fine — a row with real tokens and `"unknown"`
        for the model, which prices to exactly $0.00. Every agent built with
        `streaming=True` (the design agent among them) metered that way.
        """
        try:
            from shared.services.model_resolver import get_resolved_model  # noqa: PLC0415

            rm = get_resolved_model()
            return getattr(rm, "model", "") or "" if rm else ""
        except Exception:  # pragma: no cover - defensive
            return ""

    @classmethod
    def _extract(cls, response: Any) -> tuple[str, int, int]:
        llm_output = getattr(response, "llm_output", {}) or {}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        in_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        out_tok = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        if not in_tok and not out_tok:
            # Fallback: LangChain standard usage_metadata on the generations' messages.
            try:
                for gen_list in getattr(response, "generations", []) or []:
                    for gen in gen_list:
                        um = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
                        in_tok += um.get("input_tokens", 0) or 0
                        out_tok += um.get("output_tokens", 0) or 0
            except Exception:  # pragma: no cover - defensive
                pass
        # The response first — it is what actually produced these tokens — then the run's
        # resolved model, and only then give up. "unknown" reaches `compute_cost_usd` as
        # an unpriced model, so it is a zero-cost row, not a missing one.
        model = (
            llm_output.get("model_name")
            or llm_output.get("model")
            or cls._resolved_model_name()
            or "unknown"
        )
        return str(model), int(in_tok or 0), int(out_tok or 0)

    async def _record(self, response: Any) -> None:
        model, in_tok, out_tok = self._extract(response)
        if not (in_tok or out_tok):
            return  # nothing to attribute
        try:
            from shared.cost.pricing import compute_cost_usd  # noqa: PLC0415

            cost = float(compute_cost_usd(model, in_tok, out_tok) or 0.0)
        except Exception:  # pragma: no cover - defensive
            cost = 0.0

        # Per-offering Redis meter (TPM / monthly-cost-limit enforcement) — only when an
        # offering is resolved for this run.
        offering = self._offering()
        if offering:
            # ISOLATED, because the durable rollup below is the money and this is not.
            # `record_usage` writes Redis, and an unguarded raise here unwound the whole
            # of `_record` -- the outer handler swallowed it and the `usage_monthly`
            # write never happened. So a Redis outage silently stopped all cost
            # accounting while the comment below promised the opposite, and the Cost
            # page and budget guard both read zero. Redis is a hot counter; losing it
            # must not lose the ledger.
            try:
                from shared.services.model_rate_limit import record_usage  # noqa: PLC0415
                await record_usage(self._tenant_id, offering, in_tok + out_tok, cost)
            except Exception:
                logger.warning(
                    "usage meter: per-offering Redis meter failed for tenant %s "
                    "(rate limiting degraded; durable rollup still recorded)",
                    self._tenant_id, exc_info=True,
                )

        # Hierarchical budget accounting (org → workspace → project): durable
        # usage_monthly rollup + hot Redis counters. ALWAYS runs (independent of the
        # per-offering meter) so chat/standalone calls with no offering are still
        # attributed to org/workspace/project — the fix for empty usage_monthly.
        try:
            from shared.services.budget_store import record_usage_rollup  # noqa: PLC0415

            await record_usage_rollup(
                self._tenant_id, self._project_id or None, cost, in_tok + out_tok
            )
        except Exception:  # pragma: no cover - metering must never break a run
            # WARNING, not debug. This is a lost row of spend: the budget guard and the
            # Cost page both read `usage_monthly`, so a swallowed failure here
            # under-reports real money and silently raises the effective cap. Debug
            # level meant it never appeared in a normal deployment.
            logger.warning(
                "usage meter: budget rollup FAILED for tenant %s -- spend not recorded",
                self._tenant_id, exc_info=True,
            )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Record one completion's tokens and cost. Must work from any context.

        THIS HAS THREE CALLERS AND THEY ARRIVE DIFFERENTLY, which is what the previous
        version got wrong. LangChain looks up `on_llm_end` by that exact name and then
        (callbacks/manager.py:375-387):

          async run, sync handler  -> `run_in_executor`: a WORKER THREAD, no running loop
          sync run                 -> called directly, usually with no loop at all
          run_inline / inside a loop -> called with this thread's loop running

        The old code did `asyncio.get_running_loop().create_task(...)` and swallowed the
        `RuntimeError`. In the first case — which is every agent on this platform, since
        they all `astream`/`ainvoke` — there is no running loop in that worker thread, so
        it raised and was swallowed EVERY TIME. `usage_monthly` and `agent_call_logs` were
        empty on 2026-09-14 for that reason alone: not a metering bug downstream, a
        callback that never once ran. Nothing logged it, because losing the loop looked
        identical to "not in async context, nothing to do".

        There was also an `aon_llm_end` here. No such LangChain hook exists — `getattr`
        above only ever looks for `on_llm_end` — so it was dead code that made the async
        path look deliberately handled.
        """
        coro = self._record(response)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            # Inside a loop already: schedule, and hold a reference so the task cannot be
            # garbage-collected before it runs.
            task = loop.create_task(coro)
            _PENDING.add(task)
            task.add_done_callback(_PENDING.discard)
            return

        if self._loop is not None and not self._loop.is_closed():
            # The executor-thread case. Hand the coroutine to the loop that owns this
            # run and WAIT for it: blocking a LangChain worker thread is cheap, and the
            # alternative is fire-and-forget accounting that loses spend at shutdown.
            # The loop is only awaiting `run_in_executor` here, so it is free to run
            # this — there is no deadlock to create.
            try:
                asyncio.run_coroutine_threadsafe(coro, self._loop).result(
                    timeout=_RECORD_TIMEOUT_S
                )
            except Exception:
                # WARNING, not debug. This is a lost row of spend, and the whole reason
                # this method is written the way it is.
                logger.warning(
                    "usage meter: could not record usage for tenant %s -- spend not "
                    "recorded", self._tenant_id, exc_info=True,
                )
            return

        # No loop anywhere: a genuinely synchronous run. Drive the coroutine ourselves.
        try:
            asyncio.run(coro)
        except Exception:
            logger.warning(
                "usage meter: could not record usage for tenant %s -- spend not recorded",
                self._tenant_id, exc_info=True,
            )
