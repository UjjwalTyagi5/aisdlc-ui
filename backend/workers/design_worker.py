"""DesignWorker — consumes ``tasks:design`` and runs the design graph.

Wires connector dependency-injection and LangGraph execution into the
AbstractWorker consume loop. Implements the REQ-M3-10 credential lifecycle:
set_connector() before invoking the graph, clear_connector() in a finally block
so the connector reference is released from the contextvar even if the graph
raises.
"""
import json
import logging

from workers.base_worker import AbstractWorker
from config.connectors.context import set_connector, clear_connector
from config.connector_factory import get_connector_for_session

logger = logging.getLogger(__name__)


class DesignWorker(AbstractWorker):
    """Worker for the design agent. Stream key: tasks:design."""

    def __init__(self, consumer_name: str):
        super().__init__(agent_type="design", consumer_name=consumer_name)

    async def handle_task(self, fields: dict) -> None:
        # The pre-compiled module-level `app` (with its checkpointer already baked
        # in via _build_checkpointer("design")) is imported lazily so that a heavy
        # or broken agent-graph import cannot block worker process startup or the
        # credential-hygiene tests. The worker never builds/passes a checkpointer.
        from agents_orchestrator.design_architecture_agent.agents.architecture import app as design_graph

        # redis-py 7.x returns bytes keys/values — decode each field.
        run_id = fields.get(b"run_id", b"").decode()
        tenant_id = fields.get(b"tenant_id", b"").decode()
        payload = json.loads(fields.get(b"payload", b"{}"))
        model_id = payload.get("model_id") or fields.get(b"model_id", b"").decode() or None

        connector = await get_connector_for_session(
            kind="azure_devops", tenant_id=tenant_id,
            project_id=str(payload.get("project_id") or ""),
        )
        set_connector(connector)
        try:
            payload.setdefault("tenant_id", tenant_id)
            payload.setdefault("model_id", model_id)
            config = {"configurable": {"thread_id": f"design:{run_id}"}}
            await design_graph.ainvoke(payload, config)
        finally:
            # REQ-M3-10: release the connector from the contextvar even on error.
            clear_connector()
