"""WebhookConsumer — Redis Stream consumer that creates Temporal SDLCWorkflow runs.

Subclasses AbstractWorker to consume the webhooks:{connector_kind} stream,
building a deterministic workflow_id and starting SDLCWorkflow via the cached
Temporal client. Run creation is gated by ENABLE_WEBHOOK_TRIGGERS.

Stream key naming: webhooks:{connector_kind} (NOT tasks:{agent_type}).
DLQ key naming:    dlq:webhooks:{connector_kind}

Security note (T-m6-07-D): the temporal_client is injected from app.state at
lifespan — never call Client.connect() inside the consumer loop (T-M5-23).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from workers.base_worker import AbstractWorker
import config.env as _env

logger = logging.getLogger(__name__)


class WebhookConsumer(AbstractWorker):
    """Consumes webhooks:{connector_kind} stream; creates Temporal SDLCWorkflow runs.

    Constructor accepts either:
      - connector_kind (str) + temporal_client — for lifespan spawning per connector
      - temporal_client (keyword) alone — the connector_kind is extracted from each
        message's 'connector' field (used in unit tests where no connector is pre-set)

    redis_client is accepted for testing isolation; in production the base class
    creates its own connection via REDIS_URL.
    """

    def __init__(
        self,
        connector_kind: Optional[str] = None,
        temporal_client=None,
        *,
        consumer_name: str = "webhook-consumer-1",
        redis_client=None,
    ):
        # Accept temporal_client as positional-or-keyword for both call sites
        # (lifespan: WebhookConsumer(ck, tc) and test: WebhookConsumer(temporal_client=tc))
        effective_connector = connector_kind or "generic"
        super().__init__(
            agent_type=f"webhook_{effective_connector}",
            consumer_name=consumer_name,
        )
        # Override stream_key: webhooks:{connector_kind} not tasks:{agent_type}
        self.stream_key = f"webhooks:{effective_connector}"
        self.dlq_key = f"dlq:webhooks:{effective_connector}"
        # Cached from app.state; never call Client.connect() here (T-M5-23)
        self._temporal_client = temporal_client
        self._connector_kind = effective_connector
        # Optional injected Redis client for unit-test isolation
        self._injected_redis = redis_client

    async def _dead_letter(self, client, msg_id, fields) -> None:
        """Override AbstractWorker._dead_letter to emit a WARNING for webhook dead-letters.

        Calls super() to preserve the M3 DLQ behavior (XADD to dlq:webhooks:{ck},
        XACK, DEAD_LETTER_DEPTH.inc(), logger.error).  Adds a WARNING-level log so
        that unprocessable webhook messages are observable at WARNING severity — the
        level monitoring systems typically page on — without silently being dropped
        or requiring ERROR-level log search (REQ-M6-13, T-m6-08-R).

        Changes are localized here; base_worker.py is NOT modified so M3 task-queue
        DLQ behavior is untouched.
        """
        # Emit WARNING first so monitoring can detect the dead-letter event even
        # if the super() logger.error is filtered at a higher threshold.
        logger.warning(
            "Webhook message %s could not be processed after %s reclaim attempts "
            "— dead-lettering to %s (connector=%s). "
            "Investigate handle_task failure to prevent data loss (REQ-M6-13).",
            msg_id,
            3,  # _MAX_RECLAIM_ATTEMPTS from base class
            self.dlq_key,
            self._connector_kind,
        )
        # Call super() to retain: XADD to DLQ, XACK, DEAD_LETTER_DEPTH.inc(), logger.error
        await super()._dead_letter(client, msg_id, fields)

    async def handle_task(self, fields: dict) -> None:
        """Parse the stream message and create a Temporal SDLCWorkflow run.

        Fields dict contains:
          'event'     — JSON-encoded CanonicalWorkItem (from stream_producer)
          'connector' — connector kind (e.g. 'github', 'jira') — optional fallback
        """
        # The stream entry may carry 'event' as bytes (from Redis) or str (from tests)
        raw_event = fields.get(b"event") or fields.get("event") or b"{}"
        if isinstance(raw_event, bytes):
            raw_event = raw_event.decode("utf-8")
        event = json.loads(raw_event)

        # Connector kind: prefer constructor-set value; fall back to per-message field
        connector_kind = self._connector_kind
        if connector_kind == "generic":
            connector_kind = (
                fields.get(b"connector", b"").decode("utf-8")
                or fields.get("connector", "")
                or event.get("connector", "generic")
            )

        await _create_run_from_webhook_event(
            self._temporal_client,
            connector_kind,
            event,
        )


async def _create_run_from_webhook_event(
    temporal_client,
    connector_kind: str,
    event: dict,
) -> None:
    """Start a Temporal SDLCWorkflow run from a webhook event.

    Gated by ENABLE_WEBHOOK_TRIGGERS — if false, returns immediately without
    calling start_workflow (T-m6-07-EoP safe rollback).

    Deterministic workflow_id = webhook-{connector_kind}-{tenant_id}-{event_id}
    ensures that if the same event is delivered twice (stream delivers at-least-once),
    the second call raises WorkflowAlreadyStartedError which is caught and logged;
    the message is XACKed normally (Pitfall 6 avoidance / T-m6-07-T).
    """
    if not _env.ENABLE_WEBHOOK_TRIGGERS:
        # Feature flag off — do not start any workflow (default-safe rollback)
        return

    from workflows.sdlc_workflow import SDLCWorkflow
    from shared.models.workflow_models import SDLCWorkflowInput
    from temporalio.exceptions import WorkflowAlreadyStartedError

    tenant_id = event.get("tenant_id", "")
    event_id = event.get("event_id") or event.get("id") or str(uuid.uuid4())
    # Deterministic id — second delivery of same event gets WorkflowAlreadyStartedError
    workflow_id = f"webhook-{connector_kind}-{tenant_id}-{event_id}"

    # Persist a runs row BEFORE starting the workflow so the trigger is observable
    # via GET /runs (SC-06) and sync_run_status_activity (update-only) has a row to
    # update. Defensive: a DB failure must NOT block the Temporal trigger (D-07
    # observability mirror is secondary), so we fall back to a throwaway run_id.
    run_id = await _ensure_webhook_run_row(tenant_id, workflow_id)

    try:
        await temporal_client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(
                run_id=run_id,
                project_id=event.get("project", event.get("project_id", "")),
                tenant_id=tenant_id,
                trigger="webhook",
                work_item_id=event.get("source_key"),
            ),
            id=workflow_id,
            task_queue=_env.TEMPORAL_TASK_QUEUE,
        )
        logger.info(
            "Webhook %s started workflow %s (connector=%s)",
            event_id,
            workflow_id,
            connector_kind,
        )
    except WorkflowAlreadyStartedError:
        # Idempotent — second delivery of same event; Redis dedup is primary gate.
        # MUST NOT re-raise: the caller (_process_one in AbstractWorker) will xack
        # normally, keeping the message out of the PEL / DLQ (Pitfall 6 avoidance).
        logger.info(
            "Webhook %s already triggered workflow %s — skipping duplicate",
            event_id,
            workflow_id,
        )


async def _ensure_webhook_run_row(tenant_id: str, workflow_id: str) -> str:
    """Insert (idempotently) a runs row for a webhook-triggered run; return its run_id.

    project_id is NULL — a webhook delivery is not bound to a local Postgres project
    row (the event carries the provider's project key, not a project UUID; runs.project_id
    is nullable since migration 0005). trigger='webhook' so GET /runs can surface it.

    Idempotent on temporal_workflow_id: a re-delivery that slips past the Redis dedup
    TTL reuses the existing row instead of creating a duplicate.

    Best-effort (D-07): if tenant_id is not a UUID or the DB write fails, the Temporal
    trigger must still fire — return a throwaway UUID so start_workflow proceeds, just
    without the Postgres mirror.
    """
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid4())

    try:
        from shared.db import get_db_session_for_tenant
        from shared.models.orm import Run
        from sqlalchemy import select

        async with get_db_session_for_tenant(str(tenant_uuid)) as session:
            existing = (
                await session.execute(
                    select(Run).where(Run.temporal_workflow_id == workflow_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return str(existing.id)

            run = Run(
                tenant_id=tenant_uuid,
                project_id=None,
                trigger="webhook",
                status="running",
                stage="requirements",
                current_stage="requirements",
                temporal_workflow_id=workflow_id,
            )
            session.add(run)
            return str(run.id)
    except Exception as exc:  # noqa: BLE001 — observability mirror must never block the trigger
        logger.warning(
            "Webhook run-row persist failed (%s) for %s — proceeding trigger-only",
            type(exc).__name__,
            workflow_id,
        )
        return str(uuid.uuid4())
