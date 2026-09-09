"""Publish a project's APPROVED documents to SharePoint, and read what is filed there.

WHY THIS IS NOT `documentation_agent/tools/doc_tools.py`. That module's
`publish_to_sharepoint` files `session.generated_docs` — whatever the Documentation
agent happened to write during this conversation, held in memory, never reviewed by
anybody. That is the right source for that agent, and the wrong one everywhere else.
This publishes from the `artifacts` table instead, and only rows an owner has approved.

APPROVED ONLY, AND IT IS THE WHOLE POINT OF THE TOOL. A SharePoint library is the
business's record — things filed there get read by people who were not in the chat and
have no way to tell a draft from a signed-off document. Uploading an unapproved one is
hard to walk back: the file is out, in a system this platform does not control, and
deleting it is not something the agent can do (see below). So a pending or rejected
document is refused BY NAME, with the reason, rather than silently skipped — a silent
skip reads as "nothing to publish" and sends somebody looking for a bug.

THERE IS NO DELETE, DELIBERATELY. Nothing in this module or anywhere else in the
codebase can remove a file from SharePoint. Publishing is additive and reading is
read-only; taking something out of the business's document library is a person's job,
done in SharePoint, where that system's own permissions and recycle bin apply.

THE CONNECTOR DECIDES WHAT IS PERMITTED, not this module. `get_connector_for_session`
returns a `ScopedConnector` bound to (project, agent), and `write_adapter` refuses when
the project's grant for this stage does not permit writing. Both ids must be passed or
the connector permits nothing at all — see the note in `doc_tools._sharepoint_session`,
which learned that the hard way.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

#: SharePoint's single-PUT ceiling. Larger files need an upload session, which the
#: connector's adapter does not implement — so this refuses with a size rather than
#: letting the adapter fail with something less actionable.
_MAX_UPLOAD_BYTES = 4 * 1024 * 1024


async def _resolve(agent_id: str):
    """(connector, target, tenant_id, project_id) for this session, or (None, reason)."""
    from config.ws_helper import get_project_id, get_tenant_id, get_user_id  # noqa: PLC0415

    tenant_id = get_tenant_id() or ""
    project_id = get_project_id() or ""
    if not tenant_id or not project_id:
        return None, "ERROR: this conversation is not attached to a project."

    try:
        from config.connector_factory import get_connector_for_session  # noqa: PLC0415
        from shared.services.notification_targets import sharepoint_target  # noqa: PLC0415

        target = await sharepoint_target(tenant_id)
        if not target:
            return None, (
                "ERROR: SharePoint is not connected for this tenant. An admin can "
                "connect it on the Integrations page (Documents & knowledge)."
            )
        # NAMED, so SharePoint records WHO filed the document. A resolution with no
        # owner falls back to a shared credential and the library shows the platform as
        # the author of everything — which is the opposite of what publishing an
        # approved document is for. `test_credential_attribution` guards this invariant
        # across the codebase and caught this file.
        connector = await get_connector_for_session(
            kind="sharepoint", tenant_id=tenant_id,
            project_id=project_id, agent_id=agent_id,
            owner_id=get_user_id() or "",
        )
        return (connector, target, tenant_id, project_id), ""
    except Exception as exc:  # noqa: BLE001
        # Type name only: a connector error can carry a token or a tenant URL.
        return None, f"ERROR reaching SharePoint: {type(exc).__name__}"


async def _approved_documents(tenant_id: str, project_id: str, stage: str) -> list[dict]:
    """This stage's approved documents, plus the project-wide ones.

    The same reach `readable_documents` gives an agent: approval is the gate, and a
    project-wide policy belongs to every stage. Rows are returned with their blob path
    because this module is the one place that does need to fetch the bytes.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import Artifact  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as db:
        rows = (await db.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.approval_status == "approved",
            )
        )).scalars().all()

    out = []
    for a in rows:
        if a.artifact_type == "story" or not a.blob_path:
            continue
        if a.stage is not None and a.stage != stage:
            continue
        out.append({
            "id": str(a.id),
            "name": (a.blob_path or "").rsplit("/", 1)[-1],
            "blob_path": a.blob_path,
            "content_type": a.content_type or "application/octet-stream",
            "scope": "project" if a.stage is None else "agent",
        })
    return out


async def _unapproved_named(tenant_id: str, project_id: str, name: str) -> Optional[str]:
    """The status of a same-named document that is NOT approved, if one exists.

    Used to answer "why is my file not being published" with the actual reason. Without
    it the refusal is "no approved document called that", which is true and unhelpful
    when the document is sitting right there, pending.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import Artifact  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as db:
        rows = (await db.execute(
            select(Artifact).where(Artifact.project_id == project_id)
        )).scalars().all()
    for a in rows:
        if (a.blob_path or "").rsplit("/", 1)[-1] == name and a.approval_status != "approved":
            return a.approval_status or "pending"
    return None


async def _download(blob_path: str) -> Optional[bytes]:
    from shared.services.artifact_store import get_blob_client  # noqa: PLC0415

    client = get_blob_client()
    if client is None:
        return None
    try:
        return await client.download_bytes(blob_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sharepoint publish: download failed: %s", type(exc).__name__)
        return None


def make_sharepoint_tools(agent_id: str, stage: str) -> list:
    """The three SharePoint tools, bound to one agent. There is no fourth.

    `agent_id` is what the connector's access level is resolved against; `stage` is
    which documents belong to this screen. They are passed by the agent that registers
    the tools and never by the model — a tool argument would let a prompt claim to be a
    different agent and borrow its grant.
    """

    @tool
    async def publish_approved_to_sharepoint(filename: str = "", folder: str = "") -> str:
        """File this project's APPROVED documents into its SharePoint library.

        Only documents an owner has approved can be published — a pending or rejected
        one is refused by name. Call this only when the user explicitly asks to publish
        or file something to SharePoint.

        Args:
            filename: publish only this document. Omit to publish every approved one.
            folder:   override the configured library folder.
        """
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, target, tenant_id, project_id = resolved

        docs = await _approved_documents(tenant_id, project_id, stage)
        if filename:
            wanted = [d for d in docs if d["name"] == filename]
            if not wanted:
                # THE USEFUL REFUSAL. Say whether it is unapproved or absent — they are
                # different problems with different next steps.
                status = await _unapproved_named(tenant_id, project_id, filename)
                if status:
                    return (
                        f"ERROR: {filename!r} is {status}, not approved, so it cannot be "
                        "published to SharePoint. Ask its owner to approve it first — "
                        "the Documents panel on this agent's screen is where that happens."
                    )
                return f"ERROR: no approved document named {filename!r} on this project."
            docs = wanted

        if not docs:
            return (
                "There are no approved documents to publish yet. A document becomes "
                "publishable once its owner approves it."
            )

        drive_id = target.get("drive_id", "")
        base = (folder or target.get("folder") or "").strip("/")

        published, failures = [], []
        for doc in docs:
            data = await _download(doc["blob_path"])
            if data is None:
                failures.append(f"{doc['name']}: its stored file could not be read")
                continue
            if len(data) > _MAX_UPLOAD_BYTES:
                failures.append(
                    f"{doc['name']}: {len(data) // (1024 * 1024)} MB is over SharePoint's "
                    "4 MB single-upload limit"
                )
                continue
            path = f"{base}/{doc['name']}" if base else doc["name"]
            try:
                result = await connector.write_adapter(
                    "publish_document",
                    drive_id=drive_id,
                    path=path,
                    content=data,
                    content_type=doc["content_type"],
                )
                published.append((doc["name"], (result or {}).get("webUrl", "")))
            except ValueError as exc:
                failures.append(f"{doc['name']}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{doc['name']}: {type(exc).__name__}")

        if not published:
            return "ERROR publishing to SharePoint: " + "; ".join(failures)
        lines = [f"Published {len(published)} approved document(s) to SharePoint:"]
        lines += [f"- {n}{f' — {u}' if u else ''}" for n, u in published]
        if failures:
            lines.append(f"{len(failures)} failed: " + "; ".join(failures))
        return "\n".join(lines)

    @tool
    async def list_sharepoint_documents(folder: str = "") -> str:
        """List what is already filed in the project's SharePoint library."""
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, target, _tenant, _project = resolved
        try:
            items = await connector.read_adapter(
                "list_documents",
                drive_id=target.get("drive_id", ""),
                folder=(folder or target.get("folder") or "").strip("/"),
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR listing SharePoint documents: {type(exc).__name__}"
        if not items:
            return "The SharePoint library has no documents in that folder."
        return "\n".join(
            f"- {i.get('name', '?')} (id: {i.get('id', '?')})" for i in items
        )

    @tool
    async def read_sharepoint_document(item_id: str) -> str:
        """Read the text of one document already filed in SharePoint, by its id.

        Ids come from `list_sharepoint_documents`. Reading is the only thing this can
        do to a SharePoint file — nothing here can delete or overwrite one.
        """
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, target, _tenant, _project = resolved
        try:
            doc: Any = await connector.read_adapter(
                "get_document", drive_id=target.get("drive_id", ""), item_id=item_id,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading that SharePoint document: {type(exc).__name__}"
        if not doc:
            return f"ERROR: no SharePoint document with id {item_id!r}."
        text = doc.get("text") or doc.get("content") or ""
        if not text:
            return f"{doc.get('name', item_id)} has no readable text."
        return str(text)[:40_000]

    return [
        publish_approved_to_sharepoint,
        list_sharepoint_documents,
        read_sharepoint_document,
    ]
