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


#: CQL fragments. Kept as constants because the query is assembled from user text and
#: the quoting is the part that breaks: a stray double quote closes the literal early.
DQ = chr(34)
BACKSLASH = chr(92)
NEWLINE = chr(10)
TEXT_CQL = 'text ~ "{term}"'
SPACE_CQL = 'space = "{space}" AND {rest}'

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
    except Exception as exc:  # noqa: BLE001
        # Type name only: a connector error can carry a token or a site URL.
        return None, (
            f"ERROR reaching Confluence: {type(exc).__name__}. Confluence may not be "
            "connected for this project — an admin connects it on the project's "
            "Integrations page."
        )
    if await _not_connected(connector, tenant_id):
        return None, f"ERROR: {_not_connected_message()}"
    return (connector, tenant_id, project_id), ""


async def _not_connected(connector: Any, tenant_id: str) -> bool:
    """True when the acting user has no Confluence credential on this project.

    Checked once here rather than once per page: a publish of five documents would
    otherwise report five identical failures, and a search would report httpx's
    UnsupportedProtocol for the blank site URL an unconnected user resolves to.
    Anything that is not a real connector (a test double) passes through.
    """
    auth_adapter = getattr(connector, "auth_adapter", None)
    if not callable(auth_adapter):
        return False
    try:
        auth = await auth_adapter(tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — the request itself will say what is wrong
        return False
    return isinstance(auth, dict) and "token" in auth and not auth.get("token")


def _why(exc: BaseException) -> str:
    """What to tell the model about a failed call: the reason when it is one the user
    can act on, otherwise the type name only (a connector error can carry a token or
    a site URL)."""
    try:
        from config.connectors.confluence import ConfluenceNotConnected  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return type(exc).__name__
    if isinstance(exc, ConfluenceNotConnected):
        return str(exc)
    return type(exc).__name__


def _not_connected_message() -> str:
    """The connector's own wording, plus what the model must do with it."""
    from config.connectors.confluence import NOT_CONNECTED_MESSAGE  # noqa: PLC0415

    return (
        f"{NOT_CONNECTED_MESSAGE} Tell the user this plainly — it is their credential "
        "that is missing, not a limitation of this agent."
    )


def attachment_card(name: str) -> str:
    """The storage-format file card Confluence renders for an attached file."""
    from html import escape  # noqa: PLC0415

    return f'<p><ac:link><ri:attachment ri:filename="{escape(name, quote=True)}" /></ac:link></p>'


async def _show_attachment_in_body(connector: Any, page_id: str, filename: str) -> None:
    """Append the file card for `filename` to the page's body, once."""
    page = await connector.read_adapter("fetch_page_detail", page_id=page_id)
    body = page.get("content") or ""
    card = attachment_card(filename)
    if card in body:
        return
    await connector.write_adapter(
        "update_page",
        page_id=page_id,
        title=page.get("title") or "",
        content=body + card,
        version=int(page.get("version") or 0) + 1 if page.get("version") else 0,
    )


async def _page_titled(connector: Any, space: str, title: str) -> Optional[dict]:
    """The current page carrying `title` in `space`, or None."""
    pages = await connector.read_adapter("list_pages", space=space, title=title)
    for page in pages or []:
        if (page.get("title") or "") == title and (page.get("status") or "current") == "current":
            return page
    return None


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


def _url_suffix(page: dict) -> str:
    """` - <url>` when the API gave one, else nothing. Never a bare dash."""
    url = (page or {}).get("url") or ""
    return f" - {url}" if url else ""


def page_body(name: str, *, attached: bool, attach_failed: bool = False) -> str:
    """The storage-format body of a published document's page. Pure.

    THE FILE HAS TO BE IN THE BODY. Confluence keeps attachments under the page's ⋯
    menu; a body that merely says "the file is attached" shows prose and no file, and
    the reader concludes the document never arrived — which is exactly what happened
    with the first BRD published this way. An `<ac:link>` to `<ri:attachment>` is what
    Confluence renders as a file card the reader can open.

    Written in two passes because the attachment can only be uploaded to a page that
    already exists: the page is created with `attached=False`, the file is uploaded,
    and the body is then rewritten with `attached=True`. A body that linked the file
    BEFORE the upload would, on an upload failure, promise a file that is not there.
    """
    from html import escape  # noqa: PLC0415

    safe = escape(name, quote=True)
    parts = [f"<p>Approved project document: <strong>{safe}</strong>.</p>"]
    if attached:
        parts.append(attachment_card(name) + "<p>The approved file is attached to this page.</p>")
    elif attach_failed:
        parts.append(
            "<p>The approved file could not be attached to this page; the document "
            "remains in the project's record.</p>"
        )
    return "".join(parts)


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
                f"ERROR creating the space: {_why(exc)}. A space with that key "
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
            _approved_elsewhere,
            _approved_owner_stage,
            _download,
            _unapproved_named,
            nothing_to_publish,
            owned_elsewhere,
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
                # Approved, but another stage's. The rule stands; the answer names the
                # owner so the user goes there instead of re-approving.
                owner = await _approved_owner_stage(tenant_id, project_id, filename)
                if owner:
                    return owned_elsewhere(filename, owner)
                return f"ERROR: no approved document named {filename!r} on this project."
            docs = wanted

        if not docs:
            return nothing_to_publish(
                stage, await _approved_elsewhere(tenant_id, project_id, stage)
            )

        published, failures = [], []
        for doc in docs:
            title = doc["name"].rsplit(".", 1)[0]
            # PUBLISHING IS REPEATABLE. Confluence keeps page titles unique within a
            # space, so a second publish of the same document — a new approved
            # version, or a retry after the first attempt's reply was lost — got
            # `400 A page with this title already exists` and the agent told the
            # user to "check the space key". A page that already carries this title
            # is that document's page: it is updated, and its attachment versioned,
            # rather than refused.
            page, existing = None, None
            try:
                existing = await _page_titled(connector, space, title)
            except Exception:  # noqa: BLE001 — creation below still tells the truth
                existing = None
            if existing:
                page = existing
            else:
                try:
                    page = await connector.write_adapter(
                        "create_page",
                        space=space,
                        title=title,
                        # No attachment link yet — the file is uploaded to the page AFTER
                        # it exists, and the body is rewritten once it has landed.
                        content=page_body(doc["name"], attached=False),
                        parent_id=parent_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{doc['name']}: could not create its page ({_why(exc)})")
                    continue

            page_id = str(page.get("id", ""))
            note = " (updated the existing page of that name)" if existing else ""
            if as_attachment and page_id:
                data = await _download(doc["blob_path"])
                attached = False
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
                        attached = True
                    except Exception as exc:  # noqa: BLE001
                        note = f" (page created; attaching the file failed: {_why(exc)})"
                # SECOND PASS: make the body show the truth — a file card when the
                # upload landed, a plain statement when it did not. Failing here loses
                # nothing that was published, so it is noted rather than fatal.
                try:
                    await connector.write_adapter(
                        "update_page",
                        page_id=page_id,
                        # The title we just created the page with — v2 rejects an
                        # update without one, and passing it saves the connector a read.
                        title=title,
                        content=page_body(
                            doc["name"], attached=attached, attach_failed=not attached,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    note += f" (the page body could not be updated: {_why(exc)})"
            published.append((doc["name"], page.get("url", ""), note))

        if not published:
            return "ERROR publishing to Confluence: " + "; ".join(failures)
        lines = [f"Published {len(published)} approved document(s) to Confluence space {space}:"]
        lines += [f"- {n}{f' — {u}' if u else ''}{note}" for n, u, note in published]
        if failures:
            lines.append(f"{len(failures)} failed: " + "; ".join(failures))
        return "\n".join(lines)

    @tool
    async def create_confluence_page(
        space: str = "", title: str = "", content: str = "", parent_id: str = ""
    ) -> str:
        """Create a Confluence page from content written here.

        FOR AGENT-AUTHORED CONTENT - a summary, a runbook, a set of notes. It is NOT
        the way to file a project document: `publish_approved_to_confluence` is, and it
        refuses anything an owner has not approved. Passing an unapproved document's
        text through this tool would launder it past that gate, so do not.

        Args:
            space:     the space key.
            title:     the page title.
            content:   Confluence storage format (HTML-like). Plain text works too.
            parent_id: nest under this page. Omit for the space root.
        """
        refusal = _space_required(space)
        if refusal:
            return refusal
        if not title.strip():
            return "ERROR: a page title is required."
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved
        try:
            page = await connector.write_adapter(
                "create_page", space=space, title=title,
                content=content or "", parent_id=parent_id,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR creating the page: {_why(exc)}"
        return (
            f"Created page {page.get('title', title)!r} in {space} "
            f"(id: {page.get('id', '?')}){_url_suffix(page)}"
        )

    @tool
    async def update_confluence_page(
        page_id: str = "", title: str = "", content: str = ""
    ) -> str:
        """Edit an existing Confluence page. Ids come from `list_confluence_pages`.

        Confluence versions every edit, so the previous text stays recoverable in the
        page history - which is why editing is offered here and deleting is not.

        REPLACES THE BODY, it does not append. Read the page first if you mean to add
        to it: passing one paragraph here discards everything else on the page.

        Args:
            page_id: the page to change.
            title:   new title. Omit to leave it.
            content: new body, in storage format. Omit to leave it.
        """
        if not page_id.strip():
            return "ERROR: a page id is required. Use list_confluence_pages to find one."
        if not title.strip() and not content.strip():
            return "ERROR: nothing to change - pass a new title, new content, or both."
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved
        try:
            # No `version` passed: the connector fetches the current one and derives
            # the next. A guessed version is rejected by Confluence with a 409.
            page = await connector.write_adapter(
                "update_page", page_id=page_id, title=title, content=content,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR updating page {page_id}: {_why(exc)}"
        return f"Updated Confluence page {page.get('title', page_id)!r}{_url_suffix(page)}"

    @tool
    async def comment_on_confluence_page(page_id: str = "", text: str = "") -> str:
        """Add a comment to a Confluence page - a note, a question, a review remark.

        Args:
            page_id: from `list_confluence_pages`.
            text:    the comment body.
        """
        if not page_id.strip() or not text.strip():
            return "ERROR: both a page id and comment text are required."
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved
        try:
            await connector.write_adapter("add_comment", page_id=page_id, text=text)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR commenting on page {page_id}: {_why(exc)}"
        return f"Commented on Confluence page {page_id}."

    @tool
    async def search_confluence(query: str = "", space: str = "") -> str:
        """Search Confluence content by text, optionally within one space.

        Searches the SITE, not this project - Confluence has no notion of which pages
        belong to an SDLC project, so a result may come from anywhere this credential
        can read. Narrow with `space` when that matters.

        Args:
            query: free text to look for.
            space: restrict to this space key. Omit to search everywhere readable.
        """
        if not query.strip():
            return "ERROR: what should I search for?"
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, _tenant, _project = resolved

        # QUOTED FOR CQL. An unescaped double quote in the term closes the literal
        # early and turns the rest of the user's words into broken query syntax.
        safe = query.replace(DQ, BACKSLASH + DQ)
        cql = TEXT_CQL.format(term=safe)
        if space.strip():
            cql = SPACE_CQL.format(space=space.strip(), rest=cql)
        try:
            hits = await connector.read_adapter("search_content", cql=cql)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR searching Confluence: {_why(exc)}"
        if not hits:
            return f"Nothing in Confluence matches {query!r}."
        return NEWLINE.join(
            f"- {h.get('title', '?')} ({h.get('type', 'page')}, "
            f"space {h.get('spaceKey', '?')}, id: {h.get('id', '?')})"
            for h in hits[:40]
        )

    @tool
    async def attach_file_to_confluence_page(page_id: str = "", filename: str = "") -> str:
        """Attach one of this project's APPROVED documents to an existing page.

        Approved-only for the same reason `publish_approved_to_confluence` is: an
        attachment on a wiki page is read as the record, and a draft filed there is
        hard to walk back.

        Args:
            page_id:  the page to attach to.
            filename: the approved document's filename.
        """
        if not page_id.strip() or not filename.strip():
            return "ERROR: both a page id and a filename are required."
        resolved, reason = await _resolve(agent_id)
        if not resolved:
            return reason
        connector, tenant_id, project_id = resolved

        from shared.tools.sharepoint_artifacts import (  # noqa: PLC0415
            _approved_documents,
            _download,
            _unapproved_named,
        )

        docs = [d for d in await _approved_documents(tenant_id, project_id, stage)
                if d["name"] == filename]
        if not docs:
            status = await _unapproved_named(tenant_id, project_id, filename)
            if status:
                return (
                    f"ERROR: {filename!r} is {status}, not approved, so it cannot be "
                    "attached. Ask its owner to approve it first."
                )
            return f"ERROR: no approved document named {filename!r} on this project."

        doc = docs[0]
        data = await _download(doc["blob_path"])
        if data is None:
            return f"ERROR: {filename!r} is approved but its stored file could not be read."
        if len(data) > _MAX_ATTACHMENT_BYTES:
            return (
                f"ERROR: {filename!r} is {len(data) // (1024 * 1024)} MB, over this "
                "platform's 20 MB attachment limit."
            )
        try:
            await connector.write_adapter(
                "upload_attachment", page_id=page_id, filename=filename,
                content=data, content_type=doc["content_type"],
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR attaching {filename!r}: {_why(exc)}"
        # THE FILE HAS TO BE IN THE BODY, here as in publish_approved_to_confluence:
        # an attachment lives under the page's ⋯ menu, and a reader of a page that
        # says "Design documentation for the QuickLink project." and shows no file
        # concludes the file never arrived. So the page's body gains the file card.
        try:
            await _show_attachment_in_body(connector, page_id, filename)
        except Exception as exc:  # noqa: BLE001
            return (
                f"Attached {filename!r} to Confluence page {page_id}, but the page body "
                f"could not be updated to show it ({_why(exc)}) — the file is under the "
                "page's attachments."
            )
        return f"Attached {filename!r} to Confluence page {page_id}; the page now shows the file."

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
            return f"ERROR listing Confluence spaces: {_why(exc)}"
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
            return f"ERROR listing pages in {space}: {_why(exc)}"
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
            return f"ERROR reading that Confluence page: {_why(exc)}"
        if not page:
            return f"ERROR: no Confluence page with id {page_id!r}."
        body = page.get("body") or page.get("content") or ""
        if not body:
            return f"{page.get('title', page_id)} has no readable body."
        # Truncated, and it says so: a silent cut looks like the page simply ends there.
        text = str(body)
        return text[:40_000] + ("\n\n[truncated]" if len(text) > 40_000 else "")

    return [
        # Reading first: every write below needs an id these produce.
        list_confluence_spaces,
        list_confluence_pages,
        read_confluence_page,
        search_confluence,
        # Writing.
        create_confluence_space,
        create_confluence_page,
        update_confluence_page,
        comment_on_confluence_page,
        publish_approved_to_confluence,
        attach_file_to_confluence_page,
    ]
