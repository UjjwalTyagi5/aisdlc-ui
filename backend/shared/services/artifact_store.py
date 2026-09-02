"""Persist an agent's generated document as a blob + an `Artifact` row.

ONE PLACE THAT COMPOSES THE BLOB PATH, and that is the whole point of this module
existing rather than each agent calling `upload_bytes` itself.

`BlobStorageClient.upload_bytes`'s own docstring says the name must be
`{tenant_id}/{run_id}/{artifact_type}/{filename}` and that you must "never accept
blob_name directly from user input" — because that prefix IS the tenant boundary in
blob storage. Nothing else separates one tenant's documents from another's. A second
copy of this composition is how one of them eventually forgets the prefix, so there is
deliberately only one.

WHAT THIS REPLACES. Requirements and Design generated their documents to a flat,
process-wide `outputs/` directory under fixed names — `outputs/brd.docx`,
`outputs/pdd.docx`, `outputs/risk_register.docx` (see the prompts in
`deployment_agent/api.py`). No tenant segment, no run segment. One tenant's BRD
overwrote another's, and `GET /sdlc/agent/requirement/download/{filename}` served
whichever was on disk to any caller holding `artifact:view`.

THE JSONB PAYLOAD IS NOT REPLACED BY THIS. `runs.requirements_payload` /
`runs.design_artifacts` remain the structured hand-off the next agent reads — Design's
registry input is literally `requirements_payload`. This stores the *human* deliverable
(the BRD, the PDD, the diagrams) alongside it. They answer different questions and both
are needed.

DEGRADES, NEVER RAISES. `AZURE_BLOB_ACCOUNT_URL` is unset in most dev environments and
`process_api` sets `app.state.blob_client = None` in that case. A generated document
that could not be uploaded is not a failed run, so `store_artifact` records the row with
`blob_url = None` and returns normally. Callers get an `Artifact` either way and can
tell the difference by checking `blob_url`.
"""
from __future__ import annotations

import logging
import re
import uuid as _uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import Artifact

logger = logging.getLogger(__name__)

#: Blob names permit a lot; this is deliberately narrower. The leaf must survive being
#: pasted into a URL, a Content-Disposition header and a Windows filesystem, so it is
#: reduced to an explicit safe set rather than filtered for known-bad characters.
_SAFE_LEAF = re.compile(r"[^A-Za-z0-9._-]+")

#: A leaf that is entirely unsafe characters would sanitise to "" and produce a path
#: ending in a slash, which addresses the *directory*.
_FALLBACK_LEAF = "document"

MAX_LEAF_LENGTH = 120


def safe_leaf_name(filename: str) -> str:
    """Reduce a model-chosen filename to a single safe path segment.

    THE MODEL NAMES THE DOCUMENT; THE CODE DECIDES THE PATH. Everything that could make
    this value escape its prefix is removed rather than rejected, because rejecting
    would fail a run over a document title:

      * any `/` or `\\` — the separator, so a "filename" cannot add path segments
      * `..` — traversal, even after the separators are gone
      * leading dots — hidden files, and `.` / `..` as whole names
      * anything outside [A-Za-z0-9._-], including spaces and non-ASCII

    Truncated to keep the total blob name well inside Azure's 1024-character limit once
    the tenant, run and type segments are prepended.
    """
    leaf = _SAFE_LEAF.sub("_", (filename or "").strip())
    # After substitution a name can still be all dots ("..", "..."), which some
    # filesystems and URL normalisers resolve upward. Strip them from both ends.
    leaf = leaf.strip("._-")
    if not leaf:
        return _FALLBACK_LEAF
    if len(leaf) > MAX_LEAF_LENGTH:
        # Keep the extension when there is one — a .docx that arrives named .doc_trunc
        # is a file the browser opens with the wrong application.
        stem, _, ext = leaf.rpartition(".")
        if stem and 0 < len(ext) <= 8:
            keep = MAX_LEAF_LENGTH - len(ext) - 1
            leaf = f"{stem[:keep]}.{ext}"
        else:
            leaf = leaf[:MAX_LEAF_LENGTH]
    return leaf


#: Stand-ins for a missing segment. A run need not belong to a project (Run.project_id
#: is nullable), and without a project there is no business unit either. Substituting a
#: literal keeps the path DEPTH CONSTANT, so "the fourth segment is the agent" stays
#: true for every blob in the container; collapsing the segment instead would shift
#: every level below it and make the layout unreadable exactly where it is least
#: obvious. The leading underscore cannot collide with a UUID.
_NO_WORKSPACE = "_no-business-unit"
_NO_PROJECT = "_no-project"
_NO_AGENT = "_no-agent"


def blob_path_for(
    tenant_id: str,
    run_id: str,
    artifact_type: str,
    filename: str,
    *,
    workspace_id: str | None = None,
    project_id: str | None = None,
    agent: str | None = None,
) -> str:
    """`{tenant}/{business_unit}/{project}/{agent}/{run}/{type}/{filename}`.

    THE FIRST SEGMENT IS THE ISOLATION BOUNDARY and everything after it is
    organisation. Blob storage has no rows and no row-level security — it is a flat
    key-value namespace — so the tenant prefix is the only thing separating one
    tenant's documents from another's, and `is_blob_path` tests exactly that prefix.
    Keep it first.

    THE MIDDLE SEGMENTS MIRROR THE PRODUCT'S OWN HIERARCHY: organisation → business
    unit → project → agent. The layout used to be `{tenant}/{run}/{type}/{file}`, which
    is correct but scatters everything a project ever produced across unrelated run
    ids — there was no way to see one project's documents, or one agent's, without
    resolving every run first.

    IDS, NOT DISPLAY NAMES, at every level. A path built from names breaks the moment
    somebody renames a project or a business unit: the blobs already written keep the
    old name and are orphaned from the row that points at them. Ids never change. The
    cost is a container that is hard to read by eye in the portal, which is the right
    trade because the UI never shows these paths — it shows the filename and downloads
    by artifact id.

    RUN ID STAYS, below the agent. Two runs of the same agent routinely produce the
    same filename (`brd.docx`), and `upload_bytes` overwrites by default, so without it
    the second run silently destroys the first one's document.

    EVERY SEGMENT IS SANITISED, not just the filename. `artifact_type` comes from the
    agent rather than the user today, but it is one refactor away from being
    caller-supplied, and a `..` in any segment escapes the tenant prefix exactly as it
    would in the leaf.
    """
    return "/".join(
        (
            safe_leaf_name(str(tenant_id)),
            safe_leaf_name(str(workspace_id)) if workspace_id else _NO_WORKSPACE,
            safe_leaf_name(str(project_id)) if project_id else _NO_PROJECT,
            safe_leaf_name(str(agent)) if agent else _NO_AGENT,
            safe_leaf_name(str(run_id)),
            safe_leaf_name(artifact_type),
            safe_leaf_name(filename),
        )
    )


_process_blob_client: Any = None
_blob_client_tried = False


def get_blob_client() -> Any:
    """A process-wide BlobStorageClient, or None when blob storage is unconfigured.

    WHY NOT `request.app.state.blob_client`. The caller that needs this most —
    `chat_artifacts.register_generated_file` — runs inside agent tool code, several
    frames below any FastAPI request, with no `Request` to reach the app state through.
    Threading one down purely to fetch a client would mean changing every tool signature
    between here and there.

    Lazily built and cached, honouring `BlobStorageClient`'s own "one instance per app
    lifetime" note as closely as a non-request caller can. `_blob_client_tried` makes the
    unconfigured case a single check rather than a construction attempt per generated
    file — the common local-dev path, where `AZURE_BLOB_ACCOUNT_URL` is unset.
    """
    global _process_blob_client, _blob_client_tried
    if _blob_client_tried:
        return _process_blob_client
    _blob_client_tried = True
    try:
        from config.env import AZURE_BLOB_ACCOUNT_URL  # noqa: PLC0415
        if not AZURE_BLOB_ACCOUNT_URL:
            logger.info("AZURE_BLOB_ACCOUNT_URL unset — generated files stay local")
            return None
        from shared.storage.azure_blob import BlobStorageClient  # noqa: PLC0415
        _process_blob_client = BlobStorageClient()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blob client unavailable: %s", type(exc).__name__)
        _process_blob_client = None
    return _process_blob_client


def is_blob_path(blob_path: str | None, tenant_id: str) -> bool:
    r"""Was this path written by `store_artifact`, or is it a legacy local file path?

    THIS DISTINCTION IS LOAD-BEARING. Before blob storage, `register_generated_file`
    recorded a local filesystem path in `blob_path` and a `/generated/...` static URL in
    `blob_url`. Handing either to `download_bytes` asks Azure for a blob named
    `C:\pwc_work\...`, which fails as a confusing 502 rather than an honest "this
    artifact predates blob storage".

    The test is the tenant prefix, because that is precisely what `store_artifact`
    guarantees and what no local path can accidentally satisfy: a Windows path starts
    with a drive letter, a POSIX one with `/`, and neither begins with this tenant's
    UUID followed by a separator.
    """
    if not blob_path or not tenant_id:
        return False
    return blob_path.startswith(f"{safe_leaf_name(str(tenant_id))}/")


async def _workspace_for_project(db: AsyncSession, project_id: str) -> Optional[str]:
    """The business unit owning `project_id`, or None if it cannot be resolved.

    NEVER RAISES. A path segment is not worth failing a generated document over: an
    unresolvable project yields `_no-business-unit` and the artifact is still stored,
    still listed and still downloadable. The query runs under the caller's session, so
    RLS applies and a project in another tenant resolves to None rather than leaking a
    workspace id.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from shared.models.orm import Project  # noqa: PLC0415

    try:
        row = (
            await db.execute(select(Project.workspace_id).where(Project.id == project_id))
        ).scalar_one_or_none()
        return str(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not resolve business unit for project %s (%s)",
            project_id, type(exc).__name__,
        )
        return None


async def store_artifact(
    db: AsyncSession,
    *,
    tenant_id: str,
    run_id: str,
    artifact_type: str,
    filename: str,
    data: bytes,
    project_id: str | None = None,
    agent: str | None = None,
    content_type: str = "application/octet-stream",
    blob_client: Any = None,
) -> Artifact:
    """Upload `data` and record an `Artifact` row. Never raises on a storage failure.

    `tenant_id` and `run_id` must come from verified server-side state
    (`request.state.tenant_id`, the run being executed) — never from a model, a form
    field or a tool argument. They are the isolation boundary; accepting them from the
    caller would hand it away.

    Returns the persisted `Artifact`. `blob_url` is None when blob storage is not
    configured or the upload failed — the row still exists so the document is listed
    and the failure is visible, rather than the run silently producing nothing.
    """
    if not tenant_id or not run_id:
        # Refusing beats writing to a path with an empty segment, which would collapse
        # two tenants' documents into one prefix.
        raise ValueError("store_artifact requires a tenant_id and a run_id")

    # The business unit is DERIVED, never passed in. A caller that supplied both a
    # project and a workspace could supply a mismatched pair, and the resulting path
    # would file the artifact under a unit that does not own it.
    workspace_id = await _workspace_for_project(db, project_id) if project_id else None

    blob_name = blob_path_for(
        tenant_id,
        run_id,
        artifact_type,
        filename,
        workspace_id=workspace_id,
        project_id=project_id,
        agent=agent,
    )

    blob_url: Optional[str] = None
    if blob_client is None:
        logger.info(
            "No blob client configured — recording %s for run %s with no blob",
            artifact_type, run_id,
        )
    else:
        try:
            blob_url = await blob_client.upload_bytes(
                data, blob_name, content_type=content_type
            )
        except Exception as exc:  # noqa: BLE001 — a storage outage must not fail the run
            # type name only: an Azure error can carry a SAS token or an account URL.
            logger.warning(
                "Artifact upload failed for run %s (%s): %s",
                run_id, artifact_type, type(exc).__name__,
            )

    artifact = Artifact(
        id=_uuid.uuid4(),
        run_id=run_id,
        tenant_id=tenant_id,
        artifact_type=artifact_type,
        blob_url=blob_url,
        blob_path=blob_name,
        content_type=content_type,
        size_bytes=len(data),
    )
    db.add(artifact)
    await db.flush()
    return artifact
