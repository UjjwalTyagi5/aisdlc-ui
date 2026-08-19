"""Normalize GitHub Actions `workflow_run` webhook deliveries.

DELIBERATELY NOT A CanonicalWorkItem. Every other normalizer in this package calls
make_board_item, because every other normalizer handles a work item — an issue, a
story, a pull request. A CI run is not a work item, and forcing it into that shape
would produce a board entry per push. So this returns a flat pipeline-run event and
`make_board_item` is intentionally absent.

That shape difference is load-bearing, not cosmetic. The consumer for this stream is
webhooks.pipeline_consumer.PipelineRunConsumer, which records run state and NOTHING
else. The generic WebhookConsumer starts a full SDLCWorkflow per event
(webhooks/consumer.py::_create_run_from_webhook_event) — correct for a board item,
catastrophic for a CI run, which would kick off requirements→design→dev→test→deploy
several times per push. Do not point the generic consumer at this stream, and do not
"fix" this normalizer into a board item.

The ValueError below is the first of three independent barriers against that hazard
(the others: a separate stream key, and a consumer class with no start_workflow call).
"""
from __future__ import annotations

from typing import Any, Dict


def normalize_github_actions_event(
    payload: Dict[str, Any], tenant_id: str = "", event_id: str = ""
) -> Dict[str, Any]:
    """Map a GitHub `workflow_run` delivery to a flat pipeline-run event.

    Raises:
        ValueError: the payload is not a workflow_run delivery. The router turns this
            into a 400, so a `push` or `issues` payload misdelivered to this endpoint
            never enters the pipeline stream.
    """
    run = payload.get("workflow_run")
    if not isinstance(run, dict):
        raise ValueError(
            "not a workflow_run payload — this endpoint accepts GitHub Actions "
            "workflow_run deliveries only"
        )

    repository = payload.get("repository") or {}

    return {
        "provider": "github_actions",
        "tenant_id": tenant_id,
        "event_id": event_id,
        "action": payload.get("action", ""),
        "repo": repository.get("full_name", ""),
        "workflow_run_id": str(run.get("id", "")),
        "workflow_name": run.get("name", "") or "",
        # `status` is queued|in_progress|completed; `conclusion` is only set once the
        # run completes (success|failure|cancelled|timed_out|skipped|…).
        "status": run.get("status", "") or "",
        "conclusion": run.get("conclusion") or "",
        "head_branch": run.get("head_branch", "") or "",
        "head_sha": run.get("head_sha", "") or "",
        "html_url": run.get("html_url", "") or "",
        "run_attempt": run.get("run_attempt", 1),
        "run_number": run.get("run_number", 0),
        "event": run.get("event", "") or "",
        "created_at": run.get("created_at", "") or "",
        "updated_at": run.get("updated_at", "") or "",
    }
