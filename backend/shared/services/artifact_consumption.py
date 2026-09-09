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


# ── reading a document (phase 6) ─────────────────────────────────────────────

#: How much extracted text an agent gets. A 200-page PDF is perhaps 400k characters;
#: handing that to a model costs the whole context window and most of the turn's
#: budget for something it may only have needed the first page of.
MAX_DOCUMENT_CHARS = 40_000

_TRUNCATED = (
    "\n\n[... truncated at {n} characters. This document is longer; ask for a specific "
    "section rather than assuming this is all of it. ...]"
)


async def read_document_for_agent(
    *,
    tenant_id: str,
    project_id: str,
    artifact_id: str,
    consumer_stage: str,
    consumer_run_id: Optional[str] = None,
    consumed_by: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """Extract one approved document's text, or refuse with a reason.

    Returns `(text, note)`. `text` is None on every refusal and `note` always says why,
    because an agent told only "no" will either invent the contents or report a bug
    that is not there.

    FAILS CLOSED, AND RE-CHECKS. The metadata `read_upstream` handed over is not a
    capability: this resolves the id again and applies the same rule, so a document
    whose approval is withdrawn between the two calls stops being readable. Passing an
    id the caller was never offered gets the same refusal as passing a bad one.

    THE RULE, unchanged from `readable_documents`:

        approved (either scope)                every agent
        pending or rejected                    nobody

    The covered-by-a-published-version half of this gate was removed: approving a
    document into the project's record is now what makes it readable downstream. See
    `readable_documents` for why.

    COMMITS, like the rest of this module: an agent tool is not inside a request, so
    nobody else will commit the consumption row it records.
    """
    if not tenant_id or not project_id or not artifact_id:
        return None, "no project context in this session"

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            from shared.models.orm import Artifact  # noqa: PLC0415
            from shared.services.artifact_versions import (  # noqa: PLC0415
                latest_published, readable_documents, record_document_consumption,
            )
            from sqlalchemy import select  # noqa: PLC0415

            row = (await db.execute(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.project_id == project_id,
                )
            )).scalar_one_or_none()
            if row is None:
                return None, "that document does not exist in this project"
            if row.approval_status != "approved":
                return None, (
                    f"that document is {row.approval_status}, not approved. "
                    f"{NOT_PUBLISHED_HINT}"
                )

            # Re-derive what this consumer may see rather than trusting the id. Still
            # a real re-check even though approval is now the whole gate: `row` was
            # fetched by id, and a document belonging to a DIFFERENT project or one
            # whose approval was withdrawn between the two calls must not slip through.
            allowed = {d["id"] for d in await readable_documents(db, project_id)}
            if str(row.id) not in allowed:
                return None, (
                    "that document is not readable in this project. Only approved "
                    "documents can be read; an owner has to accept it first."
                )

            if not row.blob_path:
                return None, "that document has no stored file"

            data = await _download(row)
            if data is None:
                return None, "that document's file could not be retrieved"

            text = _extract(row.blob_path, data)
            if text is None:
                return None, "that document's text could not be extracted"

            await record_document_consumption(
                db, tenant_id=tenant_id, project_id=project_id,
                artifact_id=str(row.id), producing_stage=row.stage,
                consumer_stage=consumer_stage,
                # RESOLVED, not trusted. `consumer_run_id` has an FK to runs.id, and
                # callers pass the session id — which IS the run id in pipeline mode
                # and an arbitrary string in chat. Passing that through would fail the
                # insert and lose the whole read, so an id that names no run becomes
                # None: the consumption is still recorded, just not attributed to a run.
                consumer_run_id=await _run_id_or_none(db, consumer_run_id),
                consumed_by=consumed_by,
            )
            await db.commit()

            if len(text) > MAX_DOCUMENT_CHARS:
                # TRUNCATED WITH A MARKER, never silently cut. An agent handed the
                # first 40k characters with no signal will treat them as the whole
                # document and reason confidently about a conclusion it never saw.
                text = text[:MAX_DOCUMENT_CHARS] + _TRUNCATED.format(
                    n=MAX_DOCUMENT_CHARS
                )
            return text, "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "document read failed: %s reading %s in project %s: %s",
            consumer_stage, artifact_id, project_id, type(exc).__name__, exc_info=True,
        )
        return None, f"could not read that document ({type(exc).__name__})"


async def _run_id_or_none(db, candidate: Optional[str]) -> Optional[str]:
    """`candidate` if it names a real run, else None."""
    import uuid as _uuid  # noqa: PLC0415

    from sqlalchemy import text as _text  # noqa: PLC0415

    if not candidate:
        return None
    try:
        _uuid.UUID(str(candidate))
    except (ValueError, AttributeError, TypeError):
        # A chat session id, not a run. Not an error — most reads happen in chat.
        return None
    exists = (await db.execute(
        _text("SELECT 1 FROM runs WHERE id = CAST(:i AS uuid)"), {"i": str(candidate)}
    )).scalar()
    return str(candidate) if exists else None


async def _download(row) -> Optional[bytes]:
    """The bytes, or None. Never raises into the agent path."""
    from shared.services.artifact_store import get_blob_client  # noqa: PLC0415

    client = get_blob_client()
    if client is None:
        return None
    try:
        return await client.download_bytes(row.blob_path)
    except Exception:  # noqa: BLE001
        logger.warning("document bytes unavailable for %s", row.id, exc_info=True)
        return None


def _extract(blob_path: str, data: bytes) -> Optional[str]:
    """Extracted text, or None.

    `extract_file_text` reads from a PATH, and these bytes come from blob storage, so
    they go to a temporary file named with the ORIGINAL extension — the function
    dispatches on it, and a temp name without one would fall through every branch.
    """
    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from shared.tools.document_tools import (  # noqa: PLC0415
        extract_file_text, extraction_succeeded,
    )

    ext = os.path.splitext(blob_path)[1] or ".txt"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as fh:
            fh.write(data)
            tmp = fh.name
        text = extract_file_text(tmp)
        return text if extraction_succeeded(text) else None
    except Exception:  # noqa: BLE001
        logger.warning("extraction failed for %s", blob_path, exc_info=True)
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                # A leaked temp file is a smaller problem than an exception here
                # masking the extraction result the caller is waiting for.
                pass
