"""PipelineRunConsumer — records CI pipeline-run state from the github_actions stream.

DELIBERATELY HAS NO start_workflow CALL, AND MUST NEVER GAIN ONE.

WebhookConsumer._create_run_from_webhook_event unconditionally starts an SDLCWorkflow
for every event it consumes. That is right for a board item — a new issue reasonably
kicks off the pipeline — and catastrophic for a CI run: GitHub Actions emits a
workflow_run event on every queue, start and completion, so pointing WebhookConsumer at
webhooks:github_actions would launch a full requirements→design→development→testing→
deployment pipeline several times per push.

This class is the third of three independent barriers against that (the others: the
normalizer rejects non-workflow_run payloads, and these events land on their own stream
key that no other consumer subscribes to). It reads the stream, records what happened,
and stops. A test asserts by source inspection that "start_workflow" does not appear in
this module — if you find yourself needing it here, you want a different design.

Stream key: webhooks:github_actions
DLQ key:    dlq:webhooks:github_actions
"""
from __future__ import annotations

import json
import logging

from workers.base_worker import AbstractWorker

logger = logging.getLogger(__name__)


class PipelineRunConsumer(AbstractWorker):
    """Consumes webhooks:github_actions and records CI run state.

    Two side effects, both observational:
      1. An audit event (`deployment.pipeline_run`) so the run is in the audit trail.
      2. A publish on the artifact pub/sub channel so a live deployment view can react.

    Inherits dead-letter handling from AbstractWorker.
    """

    def __init__(self, *, consumer_name: str = "pipeline-run-consumer-1", redis_client=None):
        super().__init__(agent_type="webhook_github_actions", consumer_name=consumer_name)
        self.stream_key = "webhooks:github_actions"
        self.dlq_key = "dlq:webhooks:github_actions"
        self._injected_redis = redis_client

    async def handle_task(self, fields: dict) -> None:
        """Record one pipeline-run event.

        Fields dict carries 'event' — the JSON pipeline-run event produced by
        webhooks.normalizers.github_actions (a flat dict, NOT a CanonicalWorkItem).
        """
        raw_event = fields.get(b"event") or fields.get("event") or b"{}"
        if isinstance(raw_event, bytes):
            raw_event = raw_event.decode("utf-8")
        event = json.loads(raw_event)

        tenant_id = event.get("tenant_id", "") or ""
        run_ref = str(event.get("workflow_run_id", "") or "")
        conclusion = event.get("conclusion") or event.get("status") or ""

        logger.info(
            "pipeline run %s on %s: status=%s conclusion=%s",
            run_ref,
            event.get("repo", ""),
            event.get("status", ""),
            event.get("conclusion", ""),
        )

        # 1. Audit trail — best-effort; a CI notification must not dead-letter because
        #    the audit service hiccupped.
        try:
            from shared.audit.models import AuditEventPayload
            from shared.audit.service import audit_service

            await audit_service.emit(
                AuditEventPayload(
                    tenant_id=tenant_id,
                    event_type="deployment.pipeline_run",
                    resource_type="workflow_run",
                    resource_id=run_ref,
                    actor_id="system:github_actions",
                    payload=event,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pipeline run audit emit failed (run=%s): %s", run_ref, type(exc).__name__
            )

        # 2. Surface it to any live deployment view listening on the artifact channel.
        try:
            from shared.services.artifact_service import _ARTIFACT_CHANNEL

            client = await self._get_redis_client()
            if client is not None:
                await client.publish(
                    _ARTIFACT_CHANNEL,
                    json.dumps(
                        {
                            "type": "pipeline_run",
                            "tenant_id": tenant_id,
                            "workflow_run_id": run_ref,
                            "repo": event.get("repo", ""),
                            "status": event.get("status", ""),
                            "conclusion": conclusion,
                            "html_url": event.get("html_url", ""),
                        }
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("pipeline run publish skipped: %s", type(exc).__name__)

    async def _get_redis_client(self):
        """Return the injected client, or the base worker's own connection."""
        if self._injected_redis is not None:
            return self._injected_redis
        return getattr(self, "_redis", None)
