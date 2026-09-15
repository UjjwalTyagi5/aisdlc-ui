"""Publish a project's APPROVED documents to Confluence, and manage the space they land in.

WHY THIS EXISTS WHEN `documentation_agent/tools/doc_tools.py` ALREADY PUBLISHES TO
CONFLUENCE. That module's `publish_to_confluence` is bound to one agent, hard-coded at
import time, and files `session.generated_docs` — what the Documentation agent wrote
during this conversation. This is the `make_sharepoint_tools` shape instead: a FACTORY
any agent can bind, publishing from the `artifacts` table, and only rows an owner has
approved.

THE GAP IT CLOSES IS NOT A MISSING FEATURE, IT IS A MISSING BINDING. Asked to publish a
BRD to Confluence and create a space for it, the PM agent answered — correctly — that
"this platform only publishes to the project's SharePoint library". It binds
`make_sharepoint_tools(agent_id="plan", stage="plan")` and there was no Confluence
equivalent to bind, so the capability existed in `config/connectors/confluence.py` and
could not be reached from the screen the user was on.

APPROVED ONLY, for the same reason as SharePoint: a Confluence space is read by people
who were not in the chat and cannot tell a draft from a signed-off document. A pending
or rejected document is refused BY NAME with its status, never silently skipped.

CREATING A SPACE IS NOT UNDOABLE FROM HERE. There is no `delete_space` in this module
or in the connector, deliberately — removing a space takes its whole page tree with it
and is a person's job, done in Confluence, where that product's own permissions and
audit apply. The same rule the SharePoint module states about deletes.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

#: Attachment ceiling. Confluence Cloud's own default is 100 MB per file, but an
#: agent-driven upload has to fit in a request this process is holding open, and a
#: refusal naming the size is more useful than a timeout thirty seconds in.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


async def _resolve(agent_id: str):
    """(connector, tenant_id, project_id) for this session, or (None, reason).

    Mirrors `sharepoint_artifacts._resolve`, including the part that matters most:
    the connector is resolved with an OWNER, so Confluence records the real person as
    the author of the page. Without it the resolution falls back to a shared credential
    and every page in the space is authored by the platform — which is the opposite of
    what publishing an approved document is for. `test_credential_attribution` guards
    this across the codebase.
    """
    from config.ws_helper import get_project_id, get_tenant_id, get_user_id  # noqa: PLC0415

    tenant_id = get_tenant_id() or ""
    project_id = get_project_id() or ""
    if not tenant_id or not project_id:
        return None, "ERROR: this conversation is not attached to a project."

    try:
        from config.connector_factory import get_connector_for_session  # noqa: PLC0415

        connector = await get_connector_for_session(
            kind="confluence", tenant_id=tenant_id,
            project_id=project_id, agent_id=agent_id,
            owner_id=get_user_id() or "",
        )
        return (connector, tenant_id, project_id), ""
    except Exception as exc:  # noqa: BLE001
        # Type name only: a connector error can carry a token or a site URL.
        return None, (
            f"ERROR reaching Confluence: {type(exc).__name__}. Confluence may not be "
            "connected for this project — an admin connects it on the project's "
            "Integrations page."
        )


def _space_required(space: str) -> Optional[str]:
    """The refusal when no space was given, or None if one was.

    THE OLD MESSAGE POINTED AT A SETTING NOBODY CAN SET. `doc_tools` told the reader to
    "ask an admin to set one on the Integrations page"; `confluence-space-key` has no
    reachable writer, so `notification_targets.confluence_target()` is always None and
    that instruction could never be carried out. This asks for the thing the caller can
    actually supply.
    """
    if space.strip():
        return None
    return (
        "ERROR: which Confluence space should this go in? Pass a space key (for "
        "example ENG), or call create_confluence_space first to make one."
    )


def make_confluence_tools(agent_id: str, stage: str) -> list:
    """The five Confluence tools, bound to one agent.

    `agent_id` is what the connector's access level is resolved against; `stage` is
    which documents belong to this screen. Both are passed by the agent that registers
    the tools and never by the model — a tool argument would let a prompt claim to be a
    different agent and borrow its grant. Same contract as `make_sharepoint_tools`.
    """

    @tool
    async def create_confluence_space(
        key: str = "", name: str = "", description: str = ""
    ) -> str:
        """Create a new Confluence space for this project.

        Call this only when the user explicitly asks for a new space. If they name an
        existing space instead, just publish into it — spaces cannot be deleted from
        here, so an unwanted one has to be cleaned up by hand in Confluence.

        Args:
            key:         the space key, e.g. QUICKLINK. Letters and digits only.
            name:        the human title. Defaults to the key.
            description: optional one-line description.
        """
        if not key.strip():
            return (
                "ERROR: a space key is required — a short identifier like ENG or "
                "QUICKLINK. It cannot be changed after the space is created."
            )
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved
        try:
            space = await connector.write_adapter(
                "create_space", key=key, name=name, description=description,
            )
        except ValueError as exc:
            return f"ERROR creating the space: {exc}"
        except Exception as exc:  # noqa: BLE001
            return (
                f"ERROR creating the space: {type(exc).__name__}. A space with that key "
                "may already exist, or this account may not be allowed to create spaces."
            )
        return (
            f"Created Confluence space {space.get('key', key)} "
            f"({space.get('name', '')}). Pages can now be published into it."
        )

    @tool
    async def publish_approved_to_confluence(
        space: str = "", filename: str = "", parent_id: str = "", as_attachment: bool = True
    ) -> str:
        """Publish this project's APPROVED documents to Confluence as pages.

        Each document becomes a page in the space, with the original file attached to
        it so the reader can download exactly what was approved. Only approved
        documents can be published — a pending or rejected one is refused by name.

        Args:
            space:         the space key to publish into. Required.
            filename:      publish only this document. Omit to publish every approved one.
            parent_id:     create the pages under this page, rather than at the root.
            as_attachment: also attach the original file. True by default, because a
                           page summarising a document is not the document.
        """
        refusal = _space_required(space)
        if refusal:
            return refusal
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, tenant_id, project_id = resolved

        # Reused rather than re-implemented: the SharePoint module owns the definition
        # of "approved document for this stage", and two definitions would drift into
        # two different answers about what is publishable.
        from shared.tools.sharepoint_artifacts import (  # noqa: PLC0415
            _approved_documents,
            _download,
            _unapproved_named,
        )

        docs = await _approved_documents(tenant_id, project_id, stage)
        if filename:
            wanted = [d for d in docs if d["name"] == filename]
            if not wanted:
                status = await _unapproved_named(tenant_id, project_id, filename)
                if status:
                    return (
                        f"ERROR: {filename!r} is {status}, not approved, so it cannot be "
                        "published to Confluence. Ask its owner to approve it first — "
                        "the Documents panel on this agent's screen is where that happens."
                    )
                return f"ERROR: no approved document named {filename!r} on this project."
            docs = wanted

        if not docs:
            return (
                "There are no approved documents to publish yet. A document becomes "
                "publishable once its owner approves it."
            )

        published, failures = [], []
        for doc in docs:
            title = doc["name"].rsplit(".", 1)[0]
            try:
                page = await connector.write_adapter(
                    "create_page",
                    space=space,
                    title=title,
                    content=(
                        f"<p>Approved project document: <strong>{doc['name']}</strong>."
                        "</p><p>The approved file is attached to this page.</p>"
                    ),
                    parent_id=parent_id,
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{doc['name']}: could not create its page ({type(exc).__name__})")
                continue

            page_id = str(page.get("id", ""))
            note = ""
            if as_attachment and page_id:
                data = await _download(doc["blob_path"])
                if data is None:
                    note = " (page created; its stored file could not be read to attach)"
                elif len(data) > _MAX_ATTACHMENT_BYTES:
                    note = (
                        f" (page created; {len(data) // (1024 * 1024)} MB is over this "
                        "platform's 20 MB attachment limit)"
                    )
                else:
                    try:
                        await connector.write_adapter(
                            "upload_attachment",
                            page_id=page_id,
                            filename=doc["name"],
                            content=data,
                            content_type=doc["content_type"],
                        )
                    except Exception as exc:  # noqa: BLE001
                        note = f" (page created; attaching the file failed: {type(exc).__name__})"
            published.append((doc["name"], page.get("url", ""), note))

        if not published:
            return "ERROR publishing to Confluence: " + "; ".join(failures)
        lines = [f"Published {len(published)} approved document(s) to Confluence space {space}:"]
        lines += [f"- {n}{f' — {u}' if u else ''}{note}" for n, u, note in published]
        if failures:
            lines.append(f"{len(failures)} failed: " + "; ".join(failures))
        return "\n".join(lines)

    @tool
    async def list_confluence_spaces() -> str:
        """List the Confluence spaces this account can see, with their keys."""
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved
        try:
            spaces = await connector.read_adapter("list_spaces")
        except Exception as exc:  # noqa: BLE001
            return f"ERROR listing Confluence spaces: {type(exc).__name__}"
        if not spaces:
            return "This Confluence account can see no spaces."
        return "\n".join(
            f"- {s.get('name', '?')} (key: {s.get('key', '?')})" for s in spaces
        )

    @tool
    async def list_confluence_pages(space: str = "") -> str:
        """List the pages already in a Confluence space."""
        refusal = _space_required(space)
        if refusal:
            return refusal
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved
        try:
            pages = await connector.read_adapter("list_pages", space=space)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR listing pages in {space}: {type(exc).__name__}"
        if not pages:
            return f"Space {space} has no pages yet."
        return "\n".join(
            f"- {p.get('title', '?')} (id: {p.get('id', '?')})" for p in pages
        )

    @tool
    async def read_confluence_page(page_id: str) -> str:
        """Read one Confluence page by id. Ids come from `list_confluence_pages`."""
        if not page_id.strip():
            return "ERROR: a page id is required. Use list_confluence_pages to find one."
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved
        try:
            page: Any = await connector.read_adapter("fetch_page_detail", page_id=page_id)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading that Confluence page: {type(exc).__name__}"
        if not page:
            return f"ERROR: no Confluence page with id {page_id!r}."
        body = page.get("body") or page.get("content") or ""
        if not body:
            return f"{page.get('title', page_id)} has no readable body."
        # Truncated, and it says so: a silent cut looks like the page simply ends there.
        text = str(body)
        return text[:40_000] + ("\n\n[truncated]" if len(text) > 40_000 else "")

    return [
        create_confluence_space,
        publish_approved_to_confluence,
        list_confluence_spaces,
        list_confluence_pages,
        read_confluence_page,
    ]
