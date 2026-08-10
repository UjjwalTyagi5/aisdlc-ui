"""Standalone Temporal worker process for the SDLC pipeline (M5-07).

Registers SDLCWorkflow and all six activity functions on the sdlc-agents
task queue in the sdlc-dev namespace.  Run alongside uvicorn — NOT embedded
in the FastAPI process.

Usage (from agentic_app/):
    python -m workers.temporal_worker

The connection is provider-agnostic (D-03): switching to Temporal Cloud is
a config swap — set TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, and optionally
TEMPORAL_TLS_* in the environment; no code changes required.

Architecture decisions respected:
  D-01: This process replaces in-process agent execution; M3 Redis workers stay dormant.
  D-03: Client.connect is fully parameterised via config.env — no hardcoded addresses.
  D-09: ENABLE_TEMPORAL flag guards the hot path in the API; the worker itself is
        always started explicitly by an operator, so the flag is not re-checked here.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from temporalio.client import Client
from temporalio.worker import Worker

from config.env import TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE
from workflows.sdlc_workflow import SDLCWorkflow
from workflows.activity_dispatch import get_all_activity_fns
from workflows.activities.emit_escalation_activity import emit_escalation_activity
from workflows.activities.sync_status_activity import sync_run_status_activity

logger = logging.getLogger(__name__)

_ACTIVITY_FUNCTIONS = get_all_activity_fns() + [
    emit_escalation_activity,
    sync_run_status_activity,
]


async def main() -> None:
    """Connect to Temporal and run the SDLC worker until interrupted."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info(
        "Connecting to Temporal at %s namespace=%s task_queue=%s",
        TEMPORAL_ADDRESS,
        TEMPORAL_NAMESPACE,
        TEMPORAL_TASK_QUEUE,
    )

    client = await Client.connect(
        TEMPORAL_ADDRESS,
        namespace=TEMPORAL_NAMESPACE,
    )

    # Open the durable async LangGraph checkpointer pool (enterprise) so activity
    # graph runs persist execution state. No-op in local mode (MemorySaver).
    from config.checkpoint import aopen_checkpointer
    await aopen_checkpointer()

    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[SDLCWorkflow],
        activities=_ACTIVITY_FUNCTIONS,
    )

    logger.info(
        "SDLC Temporal worker started — registered %d activities + SDLCWorkflow",
        len(_ACTIVITY_FUNCTIONS),
    )

    # Install SIGTERM handler so the process shuts down cleanly under systemd / docker stop.
    # asyncio.loop.add_signal_handler is Unix-only; on Windows (ProactorEventLoop) it
    # raises NotImplementedError, so fall back to Ctrl+C / KeyboardInterrupt there.
    loop = asyncio.get_running_loop()

    def _handle_sigterm() -> None:
        logger.info("SIGTERM received — initiating graceful shutdown")
        loop.stop()

    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
    except (NotImplementedError, AttributeError):
        logger.info(
            "Signal-based shutdown unavailable on this platform (%s) — "
            "use Ctrl+C to stop the worker.",
            sys.platform,
        )

    try:
        await worker.run()
    finally:
        from config.checkpoint import aclose_checkpointer
        await aclose_checkpointer()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker interrupted — exiting")
        sys.exit(0)
