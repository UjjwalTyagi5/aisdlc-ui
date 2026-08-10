"""UsageMeterCallbackHandler — records per-model token + cost usage into Redis.

This is the reliable, self-contained feed for TPM + monthly-cost enforcement. It
runs as a LangChain callback (both sync + async variants for astream coverage) and,
on every LLM completion, increments the offering's Redis TPM window + monthly cost
counter (shared.services.model_rate_limit.record_usage). resolve_model_for_run then
reads those counters to enforce tpm_limit / cost_limit_usd.

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
    def _extract(response: Any) -> tuple[str, int, int]:
        llm_output = getattr(response, "llm_output", {}) or {}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        model = llm_output.get("model_name") or llm_output.get("model") or "unknown"
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
            from shared.services.model_rate_limit import record_usage  # noqa: PLC0415
            await record_usage(self._tenant_id, offering, in_tok + out_tok, cost)

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
            logger.debug("usage meter: budget rollup failed (swallowed)", exc_info=True)

    async def aon_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            await self._record(response)
        except Exception:  # pragma: no cover - metering must never break a run
            logger.debug("usage meter aon_llm_end failed (swallowed)", exc_info=True)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        # Schedule from a sync context without blocking (mirrors AuditCallbackHandler._fire).
        try:
            asyncio.get_running_loop().create_task(self._record(response))
        except RuntimeError:
            pass
        except Exception:  # pragma: no cover - defensive
            pass
