"""RequirementsWorker — consumes ``tasks:requirements`` and runs the requirements graph.

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
# The chat path's resolver, reused rather than reimplemented. Private by name only:
# it is the single definition of "which board did this stage wire", and it
# deliberately returns None instead of falling back to azure_devops.
from shared.services.agent_run import _stage_board_kind

logger = logging.getLogger(__name__)


class RequirementsWorker(AbstractWorker):
    """Worker for the requirements agent. Stream key: tasks:requirements."""

    #: The stage this worker runs. Must match the key the stage picker
    #: writes into `projects.connectors` / `projects.tool_access_modes`,
    #: because that is what the grant is stored under.
    AGENT_ID = "requirements"

    def __init__(self, consumer_name: str):
        super().__init__(agent_type="requirements", consumer_name=consumer_name)

    async def handle_task(self, fields: dict) -> None:
        # The pre-compiled module-level `app` (with its checkpointer already baked
        # in via _build_checkpointer("requirements")) is imported lazily so that a
        # heavy or broken agent-graph import cannot block worker process startup or
        # the credential-hygiene tests. The worker never builds/passes a checkpointer.
        from agents_orchestrator.requirements_agent.agents.planning import app as requirements_graph

        # redis-py 7.x returns bytes keys/values — decode each field.
        run_id = fields.get(b"run_id", b"").decode()
        tenant_id = fields.get(b"tenant_id", b"").decode()
        payload = json.loads(fields.get(b"payload", b"{}"))
        model_id = payload.get("model_id") or fields.get(b"model_id", b"").decode() or None

        # Both arguments below were previously wrong in a way that silently disabled
        # every board tool in this worker:
        #
        #   kind      was hardcoded "azure_devops". A project wired to Jira had its
        #             grant looked up under target_ref="azure_devops", found none, and
        #             got a connector permitting nothing — while the tenant's actual
        #             Jira board went untouched.
        #   agent_id  was omitted. Since migration 0024 the level is stored per
        #             (stage, tool), and `effective_access` returns None for a caller
        #             that names no stage — so even a correctly-wired Azure DevOps
        #             project resolved to no access.
        #
        # Both failures look identical from the agent's side ("the board is not
        # readable"), which is why they survived: the message was plausible.
        project_id = str(payload.get("project_id") or "")
        kind = await _stage_board_kind(tenant_id, project_id, self.AGENT_ID)
        if kind:
            connector = await get_connector_for_session(
                kind=kind, tenant_id=tenant_id, project_id=project_id,
                agent_id=self.AGENT_ID,
            )
            set_connector(connector)
        else:
            # Clear BEFORE the run, not only after. Injection is now conditional, so a
            # task with no board must not be able to inherit a connector left in this
            # context by whatever ran before it — the tools would then read a different
            # project's board and the run would look like it worked.
            clear_connector()
            # No board wired to this stage. Injecting nothing is the point: the board
            # tools then answer "connect a board on the Integrations page", which is
            # true and actionable. Injecting an Azure DevOps connector instead — as
            # this worker used to — produces a permission error about a provider the
            # tenant never chose.
            logger.info(
                "%s: no board wired to stage %s (project=%s); running without a connector",
                type(self).__name__, self.AGENT_ID, project_id,
            )
        try:
            payload.setdefault("tenant_id", tenant_id)
            payload.setdefault("model_id", model_id)
            config = {"configurable": {"thread_id": f"requirements:{run_id}"}}
            await requirements_graph.ainvoke(payload, config)
        finally:
            # REQ-M3-10: release the connector from the contextvar even on error.
            # Unconditional, as before: the cost of clearing when nothing was set is
            # nil, and the cost of not clearing is one run reading another's board.
            clear_connector()
