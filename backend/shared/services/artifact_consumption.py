"""How an agent tool reads another stage's output — phase 3.

WHAT CHANGED. Four tools each hand-rolled the same query: take the latest non-null
`runs.{stage}_artifacts` ordered by `created_at desc`. None of them ever asked whether
a human had accepted it, because until phase 1 there was nothing to ask — the payload
was written in place and had no approval state at all.

    deployment_agent/tools/deploy_tools.py    read_upstream_artifacts
    documentation_agent/tools/doc_tools.py    read_upstream_artifacts
    code_review_agent/tools/review_tools.py   read_design_artifacts / requirements
    security_agent/tools/security_tools.py    select(Run.design_artifacts)

THE FLAG IS WHAT MAKES THIS SAFE. `projects.enforce_artifact_publication` is false by
default and every project starts there, so this module's default behaviour is
byte-for-byte what the tools did before. Only a project that has deliberately switched
it on reads published versions.

WHY A SEPARATE MODULE FROM `artifact_versions`. That one promises never to commit —
its callers are HTTP routes whose request-scoped session owns the single commit at the
end. Agent tools are not in a request: they open their own short-lived session and
nobody else will commit it. Recording a consumption and then not committing would
silently record nothing, so this module DOES commit, and says so loudly rather than
quietly breaking the other module's contract.

THERE IS NO FALLBACK TO THE DRAFT when enforcement is on. "No approved design exists
yet" is the answer. A fallback would make the gate decorative in exactly the way the
tenant-wide credential fallback made "Needs a credential" decorative until it was
removed — the feature would look like it worked while never refusing anything.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from shared.services.artifact_versions import (
    UpstreamRead, enforcement_enabled, read_upstream,
)

logger = logging.getLogger(__name__)

#: What a tool says when enforcement is on and the upstream stage has nothing signed
#: off. Deliberately explicit that work EXISTS but is not approved — "not found" would
#: send an agent looking for a bug that is not there.
NOT_PUBLISHED_HINT = (
    "This is not an error. The stage may well have produced output; it has not been "
    "published, so it is not available to build on yet."
)

LegacyReader = Callable[[], Awaitable[Any]]


async def read_upstream_for_agent(
    *,
    tenant_id: str,
    project_id: str,
    stage: str,
    consumer_stage: str,
    legacy_reader: LegacyReader,
    consumer_run_id: Optional[str] = None,
    consumed_by: Optional[str] = None,
) -> UpstreamRead:
    """Read one upstream stage, honouring the project's enforcement setting.

    `legacy_reader` is the tool's existing query, passed in rather than reimplemented:
    the four call sites differ in real ways (one reads THIS run's column in pipeline
    mode, others read the project's latest) and collapsing them into one query here
    would change behaviour for projects that have not opted in to anything.

    Returns an `UpstreamRead` either way. `unenforced=True` marks the legacy path, so a
    caller can never claim something was approved when approval was not being checked.
    """
    if not tenant_id or not project_id:
        return UpstreamRead(
            stage=stage, reason="no project context in this session")

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            enforced = await enforcement_enabled(db, project_id)
            if not enforced:
                payload = await legacy_reader()
                return UpstreamRead(stage=stage, payload=payload, unenforced=True)

            result = await read_upstream(
                db, tenant_id=tenant_id, project_id=project_id, stage=stage,
                consumer_stage=consumer_stage, consumer_run_id=consumer_run_id,
                consumed_by=consumed_by,
            )
            # This session is ours and nobody downstream will commit it. Without this
            # the consumption row is discarded and the evidence trail is empty while
            # every read appears to have worked.
            if result.found:
                await db.commit()
            return result
    except Exception as exc:  # noqa: BLE001
        # The tools this replaces swallowed every error into `pass`, so a broken
        # upstream read was indistinguishable from an absent one for the agent AND for
        # anyone reading the logs. Still non-fatal — an agent should degrade rather
        # than die — but it is logged and the reason says it FAILED, not that nothing
        # was there.
        logger.warning(
            "upstream read failed: %s reading %s in project %s: %s",
            consumer_stage, stage, project_id, type(exc).__name__, exc_info=True,
        )
        return UpstreamRead(
            stage=stage,
            reason=f"could not read upstream {stage} ({type(exc).__name__})",
        )


def describe(result: UpstreamRead) -> str:
    """The sentence a tool returns when there is no payload.

    One wording, so an agent sees the same shape from every stage and a prompt does not
    end up special-casing six different phrasings of "nothing there".
    """
    reason = result.reason or f"no {result.stage} available"
    return f"{reason} {NOT_PUBLISHED_HINT}"
